"""SQLite registry: sync state, canonical entities, aliases, relationships.

The database is the canonical source for generated knowledge. Markdown pages in
the generated folder are always re-renderable from it.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- One row per Apple Notes note ever seen.
CREATE TABLE IF NOT EXISTS notes (
    id              TEXT PRIMARY KEY,          -- connector source id (stable across renames)
    title           TEXT NOT NULL,
    folder          TEXT NOT NULL DEFAULT '',
    created_at      TEXT,
    modified_at     TEXT,
    source_hash     TEXT,                      -- hash of the raw source content we last mirrored
    mirror_path     TEXT,                      -- vault-relative path of the mirror file
    mirror_hash     TEXT,                      -- sha256 of the bytes we last wrote
    status          TEXT NOT NULL DEFAULT 'active',  -- active | missing | conflict | locked | error
    processed_hash  TEXT,                      -- source_hash the intelligence layer last processed
    processed_key   TEXT,                      -- provider/model/pipeline version used
    processed_at    TEXT,
    category        TEXT,                      -- podcast | book | event | personal | other
    last_error      TEXT,
    first_seen_at   TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Write-ahead journal so interrupted syncs can be recovered.
CREATE TABLE IF NOT EXISTS pending_writes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     TEXT NOT NULL,
    op          TEXT NOT NULL,                 -- write | rename
    path        TEXT NOT NULL,
    old_path    TEXT,
    content_hash TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id       TEXT NOT NULL,
    mirror_path   TEXT NOT NULL,
    expected_hash TEXT,
    found_hash    TEXT,
    incoming_hash TEXT,
    incoming_text TEXT,
    kind          TEXT NOT NULL,               -- edited_in_obsidian | deleted_in_obsidian | path_taken
    status        TEXT NOT NULL DEFAULT 'open',-- open | resolved
    resolution    TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,                 -- book, podcast_show, ..., note, collection
    name        TEXT NOT NULL,
    norm        TEXT NOT NULL,
    note_id     TEXT,                          -- for type 'note': the source note it represents
    page_path   TEXT,                          -- vault-relative path of the rendered page
    page_hash   TEXT,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active',-- active | merged
    merged_into TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entities_type_norm ON entities(type, norm);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entities_note ON entities(note_id) WHERE note_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS aliases (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL,
    norm      TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT 'extracted', -- extracted | user | canonical
    PRIMARY KEY (entity_id, norm)
);
CREATE INDEX IF NOT EXISTS ix_aliases_norm ON aliases(norm);

CREATE TABLE IF NOT EXISTS relationships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id      TEXT NOT NULL,
    tgt_id      TEXT NOT NULL,
    type        TEXT NOT NULL,                 -- belongs_to | mentions | references | related_to | about
    note_id     TEXT,                          -- source note providing the evidence (NULL = structural/user)
    label       TEXT,                          -- human wording, e.g. "author of"
    evidence    TEXT,
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'accepted', -- accepted | pending | rejected
    origin      TEXT NOT NULL DEFAULT 'llm',   -- llm | rule | user | structural
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rel ON relationships(src_id, tgt_id, type, IFNULL(note_id, ''));
CREATE INDEX IF NOT EXISTS ix_rel_tgt ON relationships(tgt_id);
CREATE INDEX IF NOT EXISTS ix_rel_note ON relationships(note_id);

-- Remembered user decisions so reprocessing never resurrects rejected items.
CREATE TABLE IF NOT EXISTS decisions (
    key        TEXT PRIMARY KEY,
    decision   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,                  -- entity_match | ambiguous_entity | relationship
    note_id    TEXT,
    payload    TEXT NOT NULL,                  -- JSON
    status     TEXT NOT NULL DEFAULT 'pending',-- pending | resolved | superseded
    resolution TEXT,
    dedupe_key TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_reviews_status ON reviews(status);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    trigger     TEXT NOT NULL DEFAULT 'manual',
    status      TEXT NOT NULL DEFAULT 'running',  -- running | ok | partial | failed | interrupted
    stats       TEXT,                          -- JSON counters
    errors      TEXT,                          -- JSON list
    plan        TEXT                           -- JSON list (dry run)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin wrapper: one connection per thread, explicit transactions."""

    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        self.write_lock = threading.RLock()
        self.conn().executescript(SCHEMA)  # executescript manages its own transaction
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Nested calls join the outer transaction."""
        with self.write_lock:
            c = self.conn()
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                c.execute("BEGIN IMMEDIATE")
            self._local.depth = depth + 1
            try:
                yield c
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    c.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    c.execute("COMMIT")

    def q(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, args).fetchone()

    def scalar(self, sql: str, args: tuple | list = ()):
        r = self.one(sql, args)
        return r[0] if r else None

    def integrity_check(self) -> list[str]:
        problems = [r[0] for r in self.q("PRAGMA integrity_check") if r[0] != "ok"]
        problems += [f"foreign key violation in {r[0]}" for r in self.q("PRAGMA foreign_key_check")]
        dangling = self.scalar(
            "SELECT COUNT(*) FROM relationships r WHERE NOT EXISTS (SELECT 1 FROM entities e WHERE e.id=r.src_id)"
            " OR NOT EXISTS (SELECT 1 FROM entities e WHERE e.id=r.tgt_id)"
        )
        if dangling:
            problems.append(f"{dangling} relationships reference missing entities")
        return problems

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
