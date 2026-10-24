"""HTTP API: auth, real counts from backend state, sync, reviews, conflicts."""
import time

import pytest
from fastapi.testclient import TestClient

from .conftest import FIXTURES

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import shutil

    from app.main import create_app

    monkeypatch.setenv("KB_MOCK_RESPONSES", str(FIXTURES / "mock_responses.json"))
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "notes", src)
    (tmp_path / "vault" / ".obsidian").mkdir(parents=True)  # a real vault has .obsidian/
    app = create_app(TOKEN)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        cfg = c.get("/config").json()
        cfg["vault_path"] = str(tmp_path / "vault")
        cfg["source"].update(kind="folder", folder_path=str(src))
        cfg["provider"]["name"] = "mock"
        assert c.put("/config", json=cfg).json()["problems"] == []
        c.src = src
        yield c


def _wait(c):
    for _ in range(200):
        if not c.get("/status").json()["activity"]["running"]:
            return c.get("/status").json()
        time.sleep(0.05)
    raise AssertionError("sync did not finish")


def test_auth_required(tmp_path):
    from app.main import create_app

    with TestClient(create_app(TOKEN)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/status").status_code == 401
        assert c.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        r = c.get("/status", headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example.com"})
        assert r.status_code == 403


def test_status_counts_come_from_backend(client):
    s = client.get("/status").json()
    assert s["counts"]["notes"] == 0 and s["counts"]["entities"] == 0 and s["last_sync"] is None
    assert client.post("/sync", json={}).json() == {"started": True}
    s = _wait(client)
    assert s["counts"]["notes"] == 6
    assert s["counts"]["entities"] == 11
    assert s["counts"]["reviews"] == 5
    assert s["counts"]["relationships"] > 10
    assert s["last_sync"]["status"] == "ok"
    assert s["connector"]["ok"] and s["vault"]["ok"]
    assert s["last_report"]["stats"]["created"] == 6


def test_dry_run_endpoint(client):
    client.post("/sync", json={"dry_run": True})
    s = _wait(client)
    assert s["counts"]["notes"] == 0
    assert any(p["action"] == "create" for p in s["last_report"]["plan"])


def test_concurrent_sync_rejected(client):
    client.post("/sync", json={})
    r = client.post("/sync", json={})
    assert r.status_code in (409, 200)  # 409 unless the first already finished
    _wait(client)


def test_entities_reviews_and_resolution(client):
    client.post("/sync", json={})
    _wait(client)
    books = client.get("/entities", params={"type": "book"}).json()
    ff = next(b for b in books if b["name"] == "Finding Flow")
    assert ff["mentions"] == 3
    detail = client.get(f"/entities/{ff['id']}").json()
    assert any(r["type"] == "mentions" and r["note_title"] for r in detail["relationships"])
    reviews = client.get("/reviews").json()
    flow = next(r for r in reviews if r["kind"] == "ambiguous_entity" and r["payload"]["candidate"]["name"] == "Flow")
    assert client.post(f"/reviews/{flow['id']}/resolve", json={"action": "link", "target_id": ff["id"]}).status_code == 200
    assert client.get("/entities", params={"q": "finding"}).json()[0]["mentions"] == 4
    bad = client.post(f"/reviews/{flow['id']}/resolve", json={"action": "link", "target_id": ff["id"]})
    assert bad.status_code == 400


def test_entity_types_toggle(client):
    types = {t["key"]: t for t in client.get("/entity-types").json()}
    assert types["book"]["enabled"] and not types["topic"]["enabled"]
    client.put("/entity-types/topic", json={"enabled": True})
    assert next(t for t in client.get("/entity-types").json() if t["key"] == "topic")["enabled"]


def test_rename_and_merge_entity(client):
    client.post("/sync", json={})
    _wait(client)
    people = {p["name"]: p for p in client.get("/entities", params={"type": "person"}).json()}
    client.patch(f"/entities/{people['Don Norman']['id']}", json={"add_alias": "Donald Norman"})
    assert "Donald Norman" in client.get("/entities", params={"q": "donald"}).json()[0]["aliases"]
    r = client.post(f"/entities/{people['Don Norman']['id']}/merge", json={"into": people["Debbie Millman"]["id"]})
    assert r.status_code == 200
    assert "Don Norman" not in {p["name"] for p in client.get("/entities", params={"type": "person"}).json()}


def test_conflict_endpoints(client):
    client.post("/sync", json={})
    _wait(client)
    vault = client.get("/config").json()["vault_path"]
    import json
    import pathlib

    m = pathlib.Path(vault) / "Apple Notes" / "Reading list.md"
    m.write_text("edited", "utf-8")
    (client.src / "reading-list.html").write_text("<div>Reading list</div><div>new</div>")
    meta = json.loads((client.src / "reading-list.meta.json").read_text())
    meta["modified_at"] = "2026-10-10T00:00:00Z"
    (client.src / "reading-list.meta.json").write_text(json.dumps(meta))
    client.post("/sync", json={})
    _wait(client)
    conflicts = client.get("/conflicts").json()
    assert len(conflicts) == 1 and conflicts[0]["obsidian_text"] == "edited"
    assert client.post(f"/conflicts/{conflicts[0]['id']}/resolve", json={"action": "use_apple_notes"}).status_code == 200
    assert "new" in m.read_text("utf-8")
    assert client.get("/conflicts").json() == []


def test_diagnostics_and_rebuild(client):
    client.post("/sync", json={})
    _wait(client)
    d = client.get("/diagnostics").json()
    assert d["integrity"]["ok"] == 6 and d["integrity"]["modified"] == [] and d["database"]["problems"] == []
    assert client.post("/rebuild").json()["unchanged"] > 0
    runs = client.get("/runs").json()
    assert runs[0]["status"] == "ok" and runs[0]["stats"]["mirrored"] == 6


def test_reprocess_endpoint(client):
    client.post("/sync", json={})
    _wait(client)
    nid = client.get("/notes").json()[0]["id"]
    assert client.post("/notes/reprocess", json={"note_ids": [nid]}).json() == {"started": True}
    s = _wait(client)
    assert s["last_report"]["stats"]["processed"] == 1


def test_automation_toggle(client):
    a = client.post("/automation", json={"enabled": True}).json()
    assert a["enabled"] and a["next_run_at"] and a["scheduler_alive"]
    a = client.post("/automation", json={"enabled": False}).json()
    assert not a["enabled"] and a["next_run_at"] is None


def test_secret_endpoint_never_returns_key(client):
    r = client.put("/secrets/openai", json={"value": "sk-secret-value"}).json()
    assert r["key_set"] is None or "sk-secret" not in str(r)
    cfg = client.get("/config").json()
    cfg["provider"]["name"] = "openai"
    client.put("/config", json=cfg)
    st = client.get("/status").json()
    assert st["provider"]["key_set"] is True and "sk-secret" not in str(st)
    assert "sk-secret" not in str(client.get("/config").json())


def test_saving_setup_creates_vault_folders(tmp_path):
    import shutil

    from app.main import create_app

    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "notes", src)
    (tmp_path / "vault" / ".obsidian").mkdir(parents=True)  # a real vault has .obsidian/
    with TestClient(create_app(TOKEN), headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        cfg = c.get("/config").json()
        assert cfg["provider"]["name"] == "groq"  # free tier is the default
        cfg["vault_path"] = str(tmp_path / "vault")
        cfg["source"].update(kind="folder", folder_path=str(src))
        r = c.put("/config", json=cfg).json()
        assert "Knowledge Bridge/Entities/Books" in r["created_folders"]
        assert (tmp_path / "vault" / "Knowledge Bridge" / "Collections" / "Books.md").exists()
        assert (tmp_path / "vault" / "Apple Notes").is_dir()


def test_rules_only_switches_to_groq_when_key_present(monkeypatch):
    import json

    from app.config import config_path
    from app.service import Service

    config_path().write_text(json.dumps({"provider": {"name": "none"}}))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    s = Service()
    try:
        assert s.cfg.provider.name == "groq"
        assert json.loads(config_path().read_text())["provider"]["name"] == "groq"
    finally:
        s.shutdown()


def test_saving_the_icloud_container_picks_the_vault(tmp_path):
    from app.main import create_app

    docs = tmp_path / "Documents"
    (docs / "Navajyoth" / ".obsidian").mkdir(parents=True)
    with TestClient(create_app(TOKEN), headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        cfg = c.get("/config").json()
        cfg["vault_path"] = str(docs)
        cfg["source"].update(kind="folder", folder_path=str(FIXTURES / "notes"))
        assert c.put("/config", json=cfg).json()["problems"] == []
        assert c.get("/config").json()["vault_path"] == str(docs / "Navajyoth")
        assert (docs / "Navajyoth" / "Knowledge Bridge" / "Collections").is_dir()
        assert not (docs / "Knowledge Bridge").exists()


def test_obsidian_vault_list_and_warning(tmp_path, monkeypatch):
    import json
    import shutil

    from app.main import create_app

    used = tmp_path / "Obsidian Vault"
    (used / ".obsidian").mkdir(parents=True)
    other = tmp_path / "Navajyoth "
    (other / ".obsidian").mkdir(parents=True)
    conf = tmp_path / "obsidian.json"
    conf.write_text(json.dumps({"vaults": {"a": {"path": str(used), "ts": 1, "open": True},
                                           "gone": {"path": str(tmp_path / "deleted"), "ts": 2}}}))
    monkeypatch.setenv("KB_OBSIDIAN_CONFIG", str(conf))
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "notes", src)
    with TestClient(create_app(TOKEN), headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        assert c.get("/obsidian/vaults").json()["vaults"] == [{"name": "Obsidian Vault", "path": str(used), "open": True}]
        cfg = c.get("/config").json()
        cfg["vault_path"] = str(other)
        cfg["source"].update(kind="folder", folder_path=str(src))
        c.put("/config", json=cfg)
        assert c.get("/status").json()["vault"]["known_to_obsidian"] is False
        cfg["vault_path"] = str(used)
        c.put("/config", json=cfg)
        assert c.get("/status").json()["vault"]["known_to_obsidian"] is True


def test_unknown_vault_switches_to_obsidians_open_vault(tmp_path, monkeypatch):
    import json

    from app.config import AppConfig, SourceConfig, save_config
    from app.service import Service

    used = tmp_path / "Documents" / "Obsidian Vault"
    (used / ".obsidian").mkdir(parents=True)
    other = tmp_path / "iCloud" / "Navajyoth "
    (other / ".obsidian").mkdir(parents=True)
    conf = tmp_path / "obsidian.json"
    conf.write_text(json.dumps({"vaults": {"a": {"path": str(used), "ts": 1, "open": True}}}))
    monkeypatch.setenv("KB_OBSIDIAN_CONFIG", str(conf))
    save_config(AppConfig(vault_path=str(other), source=SourceConfig(kind="folder", folder_path=str(FIXTURES / "notes"))))
    s = Service()
    try:
        assert s.cfg.vault_path == str(used)
        assert "Obsidian Vault" in s.status()["notice"]
    finally:
        s.shutdown()
