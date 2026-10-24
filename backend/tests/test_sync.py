"""Incremental sync, renames, deletions, conflicts, recovery and dry runs."""
import pytest

from app.obsidian.writer import sha256_text

from .conftest import EVENT, PODCAST


def test_modified_note_is_updated_and_reprocessed(env, mock_responses):
    env.sync()
    env.edit_source("event-design-days", html="<div><h1>Design Days 2026 — Day 2</h1></div>"
                    "<div>Great talks on design systems in the morning.</div>",
                    modified_at="2026-09-11T08:00:00Z")
    mock_responses["Design Days 2026 — Day 2"]["entities"] = [
        e for e in mock_responses["Design Days 2026 — Day 2"]["entities"] if e["type"] != "book"]
    mock_responses["Design Days 2026 — Day 2"]["entities"][0]["evidence"] = "Design Days 2026 — Day 2"
    env.provider.responses = mock_responses
    rep = env.sync()
    assert rep.stats.updated == 1 and rep.stats.processed == 1 and rep.stats.unchanged == 6  # 5 notes + the locked one
    assert "Finding Flow" not in env.mirror("Design Days 2026 — Day 2").read_text("utf-8")
    book = env.entity("book", "Finding Flow")
    # the stale mention from the old text is gone, the book and its other mentions remain
    notes = {r["note_id"] for r in env.rels(tgt_id=book["id"], type="mentions")}
    assert EVENT not in notes and PODCAST in notes


def test_renamed_note_moves_mirror_without_duplicates(env):
    env.sync()
    old = env.mirror("Design Days 2026 — Day 2")
    env.edit_source("event-design-days", title="Design Days 2026 (day two)", modified_at="2026-09-11T08:00:00Z",
                    )
    html = (env.src / "event-design-days.html").read_text().replace("Design Days 2026 — Day 2", "Design Days 2026 (day two)")
    (env.src / "event-design-days.html").write_text(html)
    rep = env.sync()
    assert rep.stats.renamed == 1, rep.plan
    assert not old.exists() and env.mirror("Design Days 2026 (day two)").exists()
    assert env.db.scalar("SELECT COUNT(*) FROM notes") == 7
    assert env.db.scalar("SELECT COUNT(*) FROM entities WHERE note_id=?", (EVENT,)) == 1
    page = env.gen("Entities/Books/Finding Flow.md").read_text("utf-8")
    assert "[[Apple Notes/Design Days 2026 (day two)|" in page and "Day 2|" not in page


def test_missing_note_keeps_mirror_and_entities(env):
    env.sync()
    (env.src / "podcast-design-matters.html").unlink()
    (env.src / "podcast-design-matters.meta.json").unlink()
    rep = env.sync()
    assert rep.stats.missing == 1
    assert env.db.scalar("SELECT status FROM notes WHERE id=?", (PODCAST,)) == "missing"
    assert env.mirror("Design Matters — Debbie Millman with a product designer").exists()
    assert env.entity("podcast_show", "Design Matters") is not None
    page = env.gen("Entities/Books/Finding Flow.md").read_text("utf-8")
    assert "(removed from Apple Notes)" in page


def test_empty_source_listing_is_refused(env):
    env.sync()
    for p in env.src.iterdir():
        p.unlink()
    rep = env.sync()
    assert rep.status == "failed"
    assert env.db.scalar("SELECT COUNT(*) FROM notes WHERE status='missing'") == 0


def test_conflict_stops_write_and_keeps_both_versions(env):
    env.sync()
    m = env.mirror("Design Days 2026 — Day 2")
    edited = m.read_text("utf-8") + "\nMy edit in Obsidian\n"
    m.write_text(edited, "utf-8")
    env.edit_source("event-design-days", html="<div><h1>Design Days 2026 — Day 2</h1></div><div>Changed in Notes</div>",
                    modified_at="2026-09-12T08:00:00Z")
    rep = env.sync()
    assert rep.status == "partial" and rep.stats.conflicts == 1
    assert m.read_text("utf-8") == edited  # never overwritten
    c = env.db.one("SELECT * FROM conflicts WHERE status='open'")
    assert c["kind"] == "edited_in_obsidian" and "Changed in Notes" in c["incoming_text"]
    # a second sync does not duplicate the conflict or touch the file
    env.sync()
    assert env.db.scalar("SELECT COUNT(*) FROM conflicts") == 1 and m.read_text("utf-8") == edited


def test_resolve_conflict_with_apple_notes_version_backs_up_edit(env, tmp_path, monkeypatch):
    from app.service import Service

    env.sync()
    m = env.mirror("Design Days 2026 — Day 2")
    m.write_text("edited", "utf-8")
    env.edit_source("event-design-days", html="<div>Changed in Notes</div>", modified_at="2026-09-12T08:00:00Z")
    env.sync()
    svc = Service.__new__(Service)  # reuse the env's db/config without starting the scheduler
    svc.db, svc.cfg = env.db, env.cfg
    import threading
    svc.job_lock = threading.Lock()
    cid = env.db.scalar("SELECT id FROM conflicts")
    svc.resolve_conflict(cid, "use_apple_notes")
    assert "Changed in Notes" in m.read_text("utf-8")
    backups = list(env.gen("Logs/backups").rglob("*.md"))
    assert backups and backups[0].read_text("utf-8") == "edited"
    rep = env.sync()
    assert rep.stats.conflicts == 0 and env.db.scalar("SELECT status FROM notes WHERE id=?", (EVENT,)) == "active"


def test_keep_obsidian_detaches_note(env):
    from app.service import Service
    import threading

    env.sync()
    m = env.mirror("Design Days 2026 — Day 2")
    m.write_text("mine", "utf-8")
    env.edit_source("event-design-days", html="<div>Changed in Notes</div>", modified_at="2026-09-12T08:00:00Z")
    env.sync()
    svc = Service.__new__(Service)
    svc.db, svc.cfg, svc.job_lock = env.db, env.cfg, threading.Lock()
    svc.resolve_conflict(env.db.scalar("SELECT id FROM conflicts"), "keep_obsidian")
    env.edit_source("event-design-days", html="<div>Changed again</div>", modified_at="2026-09-13T08:00:00Z")
    rep = env.sync()
    assert m.read_text("utf-8") == "mine" and rep.stats.conflicts == 0
    svc.reattach(EVENT)
    env.sync()
    assert "Changed again" in m.read_text("utf-8")


def test_obsidian_edit_without_source_change_is_left_alone(env):
    env.sync()
    m = env.mirror("Reading list")
    m.write_text(m.read_text("utf-8") + "\nannotation\n", "utf-8")
    rep = env.sync()
    assert rep.stats.conflicts == 0 and "annotation" in m.read_text("utf-8")
    assert any("edited in Obsidian" in e["message"] for e in rep.errors)


def test_deleted_mirror_is_a_conflict_not_silently_recreated(env):
    env.sync()
    env.mirror("Reading list").unlink()
    env.edit_source("reading-list", modified_at="2026-10-02T00:00:00Z",
                    html="<div><h1>Reading list</h1></div><div>New item</div>")
    rep = env.sync()
    assert rep.stats.conflicts == 1
    assert env.db.scalar("SELECT kind FROM conflicts") == "deleted_in_obsidian"


def test_interrupted_write_is_recovered(env, monkeypatch):
    from app.sync import engine as eng

    original = eng.SyncEngine._upsert_note_row
    calls = {"n": 0}

    def crash_after_file_write(self, sn, summary, row, status, **kw):
        if kw.get("mirror_hash") and calls["n"] == 2:
            raise KeyboardInterrupt("power loss")  # not caught by the engine, like a real crash
        calls["n"] += 1 if kw.get("mirror_hash") else 0
        return original(self, sn, summary, row, status, **kw)

    monkeypatch.setattr(eng.SyncEngine, "_upsert_note_row", crash_after_file_write)
    with pytest.raises(KeyboardInterrupt):
        env.sync()
    monkeypatch.setattr(eng.SyncEngine, "_upsert_note_row", original)
    assert env.db.scalar("SELECT COUNT(*) FROM pending_writes") == 1
    rep = env.sync()
    assert rep.status == "ok", rep.errors
    assert rep.stats.conflicts == 0
    assert env.db.scalar("SELECT COUNT(*) FROM pending_writes") == 0
    assert env.db.scalar("SELECT status FROM sync_runs WHERE id=1") == "interrupted"
    files = sorted(p.name for p in (env.vault / "Apple Notes").glob("*.md"))
    assert len(files) == 6 and not any("(2)" in f for f in files)


def test_dry_run_writes_nothing(env):
    rep = env.sync(dry_run=True)
    assert rep.stats.created == 6
    assert not (env.vault / "Apple Notes").exists() and not (env.vault / "Knowledge Bridge").exists()
    assert env.db.scalar("SELECT COUNT(*) FROM notes") == 0 and env.provider.calls == []
    assert {p["action"] for p in rep.plan} >= {"create", "skip", "analyse"}


def test_title_collision_gets_distinct_files(env):
    (env.src / "dup.html").write_text("<div>Reading list</div><div>another note, same title</div>")
    (env.src / "dup.meta.json").write_text('{"id": "x-coredata://FIXTURE/ICNote/p999", "title": "Reading list"}')
    env.sync()
    assert env.mirror("Reading list").exists() and (env.vault / "Apple Notes" / "Reading list (2).md").exists()
    env.sync()
    assert len(list((env.vault / "Apple Notes").glob("Reading list*.md"))) == 2


def test_never_overwrites_unrelated_vault_file(env):
    (env.vault / "Apple Notes").mkdir()
    own = env.vault / "Apple Notes" / "Reading list.md"
    own.write_text("my own file", "utf-8")
    env.sync()
    assert own.read_text("utf-8") == "my own file"
    assert (env.vault / "Apple Notes" / "Reading list (2).md").exists()


def test_rebuild_from_registry_is_identical(env):
    import shutil
    from app.obsidian.render import render_all

    env.sync()
    root = env.gen("")
    snapshot = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    shutil.rmtree(root)
    render_all(env.db, env.cfg)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == snapshot


def test_edited_generated_page_is_backed_up(env):
    from app.obsidian.render import render_all

    env.sync()
    p = env.gen("Entities/Books/Finding Flow.md")
    p.write_text("I edited this", "utf-8")
    render_all(env.db, env.cfg)
    assert "Finding Flow" in p.read_text("utf-8")
    assert any(b.read_text("utf-8") == "I edited this" for b in env.gen("Logs/backups").rglob("*.md"))


def test_database_integrity_check(env):
    env.sync()
    assert env.db.integrity_check() == []


def test_apple_notes_subfolders_map_to_mirror_subfolders(env):
    from app.connectors.base import NoteSummary

    env.cfg.source.kind = "apple_notes"
    env.cfg.source.folder = "Knowledge"
    eng = env.engine()
    # the mirror keeps the same folder structure as Apple Notes, starting with the selected folder
    assert eng._subfolder(NoteSummary(id="a", title="t", folder="Knowledge")) == "Knowledge"
    assert eng._subfolder(NoteSummary(id="a", title="t", folder="Knowledge/Podcasts/2026")) == "Knowledge/Podcasts/2026"
    assert eng._subfolder(NoteSummary(id="a", title="t", folder="Knowledge/Talks: Q&A")) == "Knowledge/Talks Q&A"
    assert eng._desired_path("a", "Podcast with Dr. Gio Valiante", "Know-l-edge", None) == \
        "Apple Notes/Know-l-edge/Podcast with Dr. Gio Valiante.md"


def test_vault_structure_created_before_first_sync(env):
    from app.obsidian.structure import ensure_structure

    created = ensure_structure(env.cfg)
    for rel in ["Apple Notes", "Knowledge Bridge/Collections", "Knowledge Bridge/Entities/Books",
                "Knowledge Bridge/Entities/Podcast Shows", "Knowledge Bridge/Entities/Podcast Episodes",
                "Knowledge Bridge/Entities/People", "Knowledge Bridge/Entities/Events",
                "Knowledge Bridge/Entities/Personal Thinking", "Knowledge Bridge/Index", "Knowledge Bridge/Logs"]:
        assert (env.vault / rel).is_dir(), rel
    assert "Knowledge Bridge/Entities/Books" in created
    assert ensure_structure(env.cfg) == []  # idempotent


def test_long_notes_are_trimmed_for_analysis(env):
    from app.intelligence import pipeline

    long = "<div>Reading list</div>" + "<div>" + ("word " * 5000) + "</div>"
    env.edit_source("reading-list", html=long, modified_at="2026-10-25T00:00:00Z")
    rep = env.sync()
    assert any("only the first" in e["message"] for e in rep.errors)
    assert len(env.mirror("Reading list").read_text("utf-8")) > pipeline.MAX_ANALYSIS_CHARS  # mirror is complete


def test_vaults_container_is_rejected(tmp_path):
    from app.config import AppConfig, SourceConfig

    docs = tmp_path / "iCloud~md~obsidian" / "Documents"
    (docs / "Obsidian Vault" / ".obsidian").mkdir(parents=True)
    (docs / "KB Test" / ".obsidian").mkdir(parents=True)
    cfg = AppConfig(vault_path=str(docs), source=SourceConfig(kind="folder", folder_path=str(tmp_path)))
    msg = " ".join(cfg.problems())
    assert "not an Obsidian vault" in msg and "KB Test, Obsidian Vault" in msg
    cfg.vault_path = str(docs / "Obsidian Vault")
    assert cfg.problems() == []


def test_single_vault_inside_container_is_selected_automatically(tmp_path):
    from app.config import AppConfig

    docs = tmp_path / "Documents"
    (docs / "Navajyoth " / ".obsidian").mkdir(parents=True)
    cfg = AppConfig(vault_path=str(docs))
    assert cfg.fix_vault_container()
    assert cfg.vault_path.endswith("Navajyoth ") and cfg.vault_name() == "Navajyoth "
    assert not cfg.fix_vault_container()  # already a vault
