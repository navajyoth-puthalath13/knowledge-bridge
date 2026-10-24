"""Real Apple Notes integration. Runs only on macOS with KB_MACOS_TESTS=1.

    KB_MACOS_TESTS=1 KB_TEST_NOTES_FOLDER="Knowledge Bridge Test" pytest tests/test_macos_integration.py -v

Create the folder in Notes first and put a few notes in it (see docs/APPLE_NOTES.md
for a checklist: headings, lists, a table, a link, an image, an attachment, a
locked note). The test only reads; it never modifies Notes.
"""
import os
import sys

import pytest

pytestmark = [
    pytest.mark.macos,
    pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("KB_MACOS_TESTS"),
                       reason="requires macOS and KB_MACOS_TESTS=1"),
]

FOLDER = os.environ.get("KB_TEST_NOTES_FOLDER", "Knowledge Bridge Test")
ACCOUNT = os.environ.get("KB_TEST_NOTES_ACCOUNT", "")


@pytest.fixture
def conn():
    from app.connectors.apple_notes import AppleNotesConnector

    return AppleNotesConnector(ACCOUNT, FOLDER)


def test_health(conn):
    ok, msg = conn.health()
    assert ok, msg


def test_folder_is_listed(conn):
    assert any(f.path == FOLDER for f in conn.list_folders())


def test_list_and_fetch_are_consistent(conn):
    notes = conn.list_notes()
    assert notes, f"put some notes in the '{FOLDER}' folder"
    assert len({n.id for n in notes}) == len(notes)
    fetched = conn.fetch([n.id for n in notes])
    assert {n.id for n in fetched} == {n.id for n in notes}
    for n in fetched:
        assert n.id.startswith("x-coredata://")
        assert n.locked or n.body, n.title


def test_ids_are_stable_between_calls(conn):
    assert [n.id for n in conn.list_notes()] == [n.id for n in conn.list_notes()]


def test_full_sync_into_temp_vault(conn, tmp_path):
    from app.config import AppConfig, SourceConfig
    from app.intelligence.registry import ensure_collections
    from app.storage.db import Database
    from app.sync.engine import SyncEngine

    (tmp_path / "vault" / ".obsidian").mkdir(parents=True)  # a real vault has .obsidian/
    cfg = AppConfig(vault_path=str(tmp_path / "vault"), source=SourceConfig(account=ACCOUNT, folder=FOLDER))
    db = Database(tmp_path / "kb.sqlite3")
    ensure_collections(db)
    rep = SyncEngine(db, cfg, conn, None).run()
    assert rep.status in ("ok", "partial"), rep.errors
    again = SyncEngine(db, cfg, conn, None).run()
    assert again.stats.mirrored == 0, "second sync should change nothing"
