"""Applying the user's decisions from the review queue.

Every decision is stored in `decisions`, so reprocessing a note later applies
the same answer automatically instead of asking again.
"""
from __future__ import annotations

import json

from ..config import AppConfig
from ..storage.db import Database, now
from . import registry as reg
from .models import ExtractedEntity
from .normalize import norm
from .pipeline import _maybe_alias, apply_candidate

ACTIONS = {
    "entity_match": {"same", "different"},
    "ambiguous_entity": {"create", "link", "dismiss"},
    "relationship": {"approve", "reject"},
}


def _entity(cand: dict) -> ExtractedEntity:
    return ExtractedEntity(**{k: v for k, v in cand.items() if k in ExtractedEntity.model_fields})


def resolve_review(db: Database, cfg: AppConfig, review_id: int, action: str, target_id: str | None = None) -> dict:
    r = db.one("SELECT * FROM reviews WHERE id=?", (review_id,))
    if not r:
        raise LookupError("review not found")
    if r["status"] != "pending":
        raise ValueError(f"review already {r['status']}")
    if action not in ACTIONS[r["kind"]]:
        raise ValueError(f"action must be one of {sorted(ACTIONS[r['kind']])}")
    p = json.loads(r["payload"])
    note_id = r["note_id"]
    result: dict = {"review_id": review_id, "action": action}

    with db.tx() as c:
        if r["kind"] == "entity_match":
            cand = p["candidate"]
            ent = _entity(cand)
            existing = reg.resolve_id(db, p["existing_id"])
            reg.set_decision(db, f"match:{cand['type']}:{norm(cand['name'])}:{p['existing_id']}", action)
            if action == "same":
                _maybe_alias(db, existing, ent)
                eid = existing
            else:
                eid = reg.create_entity(db, cand["type"], cand["name"], cand.get("aliases", []))
            apply_candidate(db, note_id, p["note_entity_id"], cand, eid, cfg)
            result["entity_id"] = eid

        elif r["kind"] == "ambiguous_entity":
            cand = p["candidate"]
            key = f"ambig:{note_id}:{cand['type']}:{norm(cand['name'])}"
            if action == "dismiss":
                reg.set_decision(db, key, "dismiss")
            else:
                if action == "link":
                    if not target_id:
                        raise ValueError("target_id is required to link")
                    eid = reg.resolve_id(db, target_id)
                    t = db.one("SELECT type FROM entities WHERE id=? AND status='active'", (eid,))
                    if not t or t["type"] != cand["type"]:
                        raise ValueError("target must be an active entity of the same type")
                    _maybe_alias(db, eid, _entity(cand))
                else:
                    eid = reg.create_entity(db, cand["type"], cand["name"], cand.get("aliases", []))
                reg.set_decision(db, key, f"link:{eid}")
                apply_candidate(db, note_id, p["note_entity_id"], cand, eid, cfg)
                result["entity_id"] = eid

        else:  # relationship
            rel = db.one("SELECT * FROM relationships WHERE id=?", (p["relationship_id"],))
            if rel:
                status = "accepted" if action == "approve" else "rejected"
                c.execute("UPDATE relationships SET status=?, updated_at=? WHERE id=?", (status, now(), rel["id"]))
                reg.set_decision(db, reg.rel_key(rel["src_id"], rel["tgt_id"], rel["type"]), status)

        c.execute("UPDATE reviews SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
                  (json.dumps({"action": action, "target_id": target_id}), now(), review_id))
        reg.refresh_structure(db)
    return result


def describe(db: Database, row) -> dict:
    """Review row -> JSON for the UI, with the relationship expanded."""
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    d["note_title"] = db.scalar("SELECT title FROM notes WHERE id=?", (row["note_id"],))
    if row["kind"] == "relationship":
        rel = db.one("SELECT r.*, s.name AS src_name, s.type AS src_type, t.name AS tgt_name, t.type AS tgt_type "
                     "FROM relationships r JOIN entities s ON s.id=r.src_id JOIN entities t ON t.id=r.tgt_id "
                     "WHERE r.id=?", (d["payload"]["relationship_id"],))
        d["relationship"] = dict(rel) if rel else None
    d["actions"] = sorted(ACTIONS[row["kind"]])
    return d
