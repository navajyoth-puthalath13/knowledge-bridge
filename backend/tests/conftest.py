from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KB_DISABLE_KEYRING", "1")
    for k in ("OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def mock_responses() -> dict:
    return json.loads((FIXTURES / "mock_responses.json").read_text("utf-8"))


class Env:
    """A temporary source folder + vault + registry wired to the sync engine."""

    def __init__(self, tmp: Path, responses: dict):
        from app.config import AppConfig, SourceConfig
        from app.intelligence.registry import ensure_collections
        from app.providers.mock import MockProvider
        from app.storage.db import Database

        self.src = tmp / "source"
        shutil.copytree(FIXTURES / "notes", self.src)
        self.vault = tmp / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)  # a real vault has .obsidian/
        self.cfg = AppConfig(vault_path=str(self.vault), source=SourceConfig(kind="folder", folder_path=str(self.src)))
        # engine tests exercise every entity type; the app's defaults (main topics only) have their own tests
        from app.entities.types import ENTITY_TYPES
        self.cfg.intelligence.enabled_types = {k: k != "topic" for k in ENTITY_TYPES}
        self.db = Database(tmp / "kb.sqlite3")
        ensure_collections(self.db)
        self.provider = MockProvider(responses)

    def engine(self, provider="default"):
        from app.connectors.folder import FolderConnector
        from app.sync.engine import SyncEngine

        return SyncEngine(self.db, self.cfg, FolderConnector(self.src),
                          self.provider if provider == "default" else provider)

    def sync(self, **kw):
        provider = kw.pop("provider", "default")
        return self.engine(provider).run(**kw)

    # helpers -----------------------------------------------------------
    def entity(self, type_: str, name: str):
        return self.db.one("SELECT * FROM entities WHERE type=? AND name=? AND status='active'", (type_, name))

    def entities_of(self, type_: str) -> list[str]:
        return [r["name"] for r in self.db.q("SELECT name FROM entities WHERE type=? AND status='active' ORDER BY name",
                                             (type_,))]

    def note_eid(self, note_id: str) -> str:
        return self.db.scalar("SELECT id FROM entities WHERE note_id=?", (note_id,))

    def rels(self, **where) -> list:
        sql = "SELECT * FROM relationships WHERE 1=1" + "".join(f" AND {k}=?" for k in where)
        return self.db.q(sql, tuple(where.values()))

    def mirror(self, title: str) -> Path:
        return self.vault / "Apple Notes" / f"{title}.md"

    def gen(self, rel: str) -> Path:
        return self.vault / "Knowledge Bridge" / rel

    def edit_source(self, stem: str, html: str | None = None, **meta):
        if html is not None:
            (self.src / f"{stem}.html").write_text(html, "utf-8")
        mp = self.src / f"{stem}.meta.json"
        m = json.loads(mp.read_text("utf-8"))
        m.update(meta)
        mp.write_text(json.dumps(m), "utf-8")

    def mirror_hashes(self) -> dict[str, bytes]:
        root = self.vault / "Apple Notes"
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def env(tmp_path, mock_responses) -> Env:
    return Env(tmp_path, mock_responses)


PODCAST = "x-coredata://FIXTURE/ICNote/p101"
EVENT = "x-coredata://FIXTURE/ICNote/p102"
PERSONAL = "x-coredata://FIXTURE/ICNote/p103"
FOF = "x-coredata://FIXTURE/ICNote/p104"
READING = "x-coredata://FIXTURE/ICNote/p105"
RICH = "x-coredata://FIXTURE/ICNote/p106"
LOCKED = "x-coredata://FIXTURE/ICNote/p107"
