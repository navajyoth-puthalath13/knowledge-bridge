"""Canonical entity registry operations on SQLite.

All functions expect to run inside `db.tx()` when they write.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from ..entities.types import COLLECTION_TYPE, COLLECTIONS, ENTITY_TYPES, NOTE_TYPE
from ..storage.db import Database, now
from .normalize import keys_for, norm, short_title, similarity, slug


@dataclass
class Match:
    entity_id: str | None
    score: float
    kind: str  # exact | fuzzy | none


def collection_id(name: str) -> str:
    return "collection-" + slug(name)


def ensure_collections(db: Database) -> None:
    with db.tx() as c:
        for name in COLLECTIONS:
            c.execute(
                "INSERT OR IGNORE INTO entities(id, type, name, norm, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (collection_id(name), COLLECTION_TYPE, name, norm(name), now(), now()),
            )


def new_entity_id(type_: str, name: str) -> str:
    return f"{type_.replace('_', '-')}-{slug(name, 32)}-{uuid.uuid4().hex[:6]}"


def create_entity(db: Database, type_: str, name: str, aliases: list[str] = (), note_id: str | None = None) -> str:
    eid = new_entity_id(type_, name)
    with db.tx() as c:
        c.execute(
            "INSERT INTO entities(id, type, name, norm, note_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (eid, type_, name, norm(name), note_id, now(), now()),
        )
        add_alias(db, eid, name, "canonical")
        for a in aliases:
            add_alias(db, eid, a)
    return eid


def add_alias(db: Database, entity_id: str, alias: str, source: str = "extracted") -> None:
    with db.tx() as c:
        for k in keys_for(alias):
            # the short-title key is recorded as an alias too, so "Finding Flow" matches the full title
            c.execute("INSERT OR IGNORE INTO aliases(entity_id, alias, norm, source) VALUES (?,?,?,?)",
                      (entity_id, alias if k == norm(alias) else short_title(alias), k, source))


def note_entity(db: Database, note_id: str, title: str) -> str:
    row = db.one("SELECT id, name FROM entities WHERE note_id=?", (note_id,))
    if row:
        if row["name"] != title:
            with db.tx() as c:
                c.execute("UPDATE entities SET name=?, norm=?, updated_at=? WHERE id=?",
                          (title, norm(title), now(), row["id"]))
        return row["id"]
    with db.tx() as c:
        eid = "note-" + uuid.uuid5(uuid.NAMESPACE_URL, note_id).hex[:12]
        c.execute(
            "INSERT INTO entities(id, type, name, norm, note_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (eid, NOTE_TYPE, title, norm(title), note_id, now(), now()),
        )
    return eid


def find_match(db: Database, type_: str, names: list[str], fuzzy_floor: float) -> Match:
    """Find the canonical entity of `type_` for any of `names`."""
    keys = set()
    for n in names:
        keys |= keys_for(n)
    if not keys:
        return Match(None, 0.0, "none")
    ph = ",".join("?" * len(keys))
    row = db.one(
        f"SELECT e.id FROM aliases a JOIN entities e ON e.id=a.entity_id "
        f"WHERE e.type=? AND e.status='active' AND a.norm IN ({ph}) ORDER BY a.source='canonical' DESC LIMIT 1",
        (type_, *keys),
    )
    if row:
        return Match(row["id"], 1.0, "exact")
    best: tuple[float, str | None] = (0.0, None)
    for r in db.q("SELECT a.entity_id, a.norm FROM aliases a JOIN entities e ON e.id=a.entity_id "
                  "WHERE e.type=? AND e.status='active'", (type_,)):
        for k in keys:
            s = similarity(k, r["norm"])
            if s > best[0]:
                best = (s, r["entity_id"])
    if best[1] and best[0] >= fuzzy_floor:
        return Match(best[1], best[0], "fuzzy")
    return Match(None, best[0], "none")


def resolve_id(db: Database, entity_id: str) -> str:
    """Follow merge chains to the surviving entity."""
    seen = set()
    while entity_id not in seen:
        seen.add(entity_id)
        r = db.one("SELECT status, merged_into FROM entities WHERE id=?", (entity_id,))
        if not r or r["status"] != "merged" or not r["merged_into"]:
            return entity_id
        entity_id = r["merged_into"]
    return entity_id


def upsert_relationship(db: Database, src: str, tgt: str, type_: str, note_id: str | None, evidence: str | None,
                        confidence: float | None, status: str, origin: str, label: str | None = None) -> int | None:
    if src == tgt:
        return None
    decision = get_decision(db, rel_key(src, tgt, type_))
    if decision == "rejected":
        return None
    if decision == "accepted":
        status = "accepted"
    with db.tx() as c:
        c.execute(
            "INSERT INTO relationships(src_id, tgt_id, type, note_id, label, evidence, confidence, status, origin, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(src_id, tgt_id, type, IFNULL(note_id, '')) DO UPDATE SET"
            " evidence=excluded.evidence, confidence=excluded.confidence, label=excluded.label, updated_at=excluded.updated_at,"
            " status=CASE WHEN relationships.status='accepted' THEN 'accepted' ELSE excluded.status END",
            (src, tgt, type_, note_id, label, evidence, confidence, status, origin, now(), now()),
        )
        return c.execute("SELECT id FROM relationships WHERE src_id=? AND tgt_id=? AND type=? AND IFNULL(note_id,'')=?",
                         (src, tgt, type_, note_id or "")).fetchone()["id"]


def rel_key(src: str, tgt: str, type_: str) -> str:
    return f"rel:{src}:{tgt}:{type_}"


def get_decision(db: Database, key: str) -> str | None:
    return db.scalar("SELECT decision FROM decisions WHERE key=?", (key,))


def set_decision(db: Database, key: str, decision: str) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO decisions(key, decision, created_at) VALUES (?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET decision=excluded.decision", (key, decision, now()))


def add_review(db: Database, kind: str, note_id: str | None, payload: dict, dedupe_key: str) -> int:
    with db.tx() as c:
        existing = c.execute("SELECT id FROM reviews WHERE dedupe_key=? AND status='pending'", (dedupe_key,)).fetchone()
        if existing:
            c.execute("UPDATE reviews SET payload=? WHERE id=?", (json.dumps(payload), existing["id"]))
            return existing["id"]
        cur = c.execute("INSERT INTO reviews(kind, note_id, payload, dedupe_key, created_at) VALUES (?,?,?,?,?)",
                        (kind, note_id, json.dumps(payload), dedupe_key, now()))
        return cur.lastrowid


def refresh_structure(db: Database) -> None:
    """Keep each entity linked to its parent or, failing that, its collection.

    An episode with an accepted belongs_to → show does not also link straight to
    Podcasts; one without a known show does."""
    with db.tx() as c:
        for e in c.execute("SELECT id, type FROM entities WHERE status='active'").fetchall():
            t = ENTITY_TYPES.get(e["type"])
            if not t or not t.collection:
                continue
            cid = collection_id(t.collection)
            has_parent = t.parent_type and c.execute(
                "SELECT 1 FROM relationships r JOIN entities p ON p.id=r.tgt_id WHERE r.src_id=? AND r.type='belongs_to'"
                " AND r.status='accepted' AND p.type=? AND p.status='active' LIMIT 1", (e["id"], t.parent_type)).fetchone()
            if has_parent:
                c.execute("DELETE FROM relationships WHERE src_id=? AND tgt_id=? AND origin='structural'", (e["id"], cid))
            else:
                upsert_relationship(db, e["id"], cid, "belongs_to", None, None, None, "accepted", "structural")


def merge_entities(db: Database, src_id: str, into_id: str) -> None:
    """Merge `src_id` into `into_id`: aliases, relationships and reviews move over."""
    if src_id == into_id:
        return
    with db.tx() as c:
        a, b = (c.execute("SELECT * FROM entities WHERE id=?", (i,)).fetchone() for i in (src_id, into_id))
        if not a or not b:
            raise ValueError("entity not found")
        if a["type"] != b["type"]:
            raise ValueError("can only merge entities of the same type")
        for al in c.execute("SELECT alias, norm, source FROM aliases WHERE entity_id=?", (src_id,)).fetchall():
            c.execute("INSERT OR IGNORE INTO aliases(entity_id, alias, norm, source) VALUES (?,?,?,?)",
                      (into_id, al["alias"], al["norm"], "user" if al["source"] == "canonical" else al["source"]))
        for col in ("src_id", "tgt_id"):
            for r in c.execute(f"SELECT * FROM relationships WHERE {col}=?", (src_id,)).fetchall():
                other = dict(r)
                other[col] = into_id
                c.execute("DELETE FROM relationships WHERE id=?", (r["id"],))
                if other["src_id"] != other["tgt_id"]:
                    c.execute(
                        "INSERT OR IGNORE INTO relationships(src_id, tgt_id, type, note_id, label, evidence, confidence, status,"
                        " origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (other["src_id"], other["tgt_id"], other["type"], other["note_id"], other["label"], other["evidence"],
                         other["confidence"], other["status"], other["origin"], other["created_at"], now()))
        c.execute("DELETE FROM aliases WHERE entity_id=?", (src_id,))
        c.execute("UPDATE entities SET status='merged', merged_into=?, updated_at=? WHERE id=?", (into_id, now(), src_id))
        set_decision(db, f"merge:{src_id}", into_id)
