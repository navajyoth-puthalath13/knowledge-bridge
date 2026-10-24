"""Review queue decisions, persistence across reprocessing, merges."""
import json

import pytest

from app.intelligence import registry as reg
from app.intelligence.reviews import resolve_review

from .conftest import EVENT, FOF, READING


def _review(env, name=None, kind="ambiguous_entity"):
    for r in env.db.q("SELECT * FROM reviews WHERE kind=? AND status='pending'", (kind,)):
        p = json.loads(r["payload"])
        if name is None or p.get("candidate", {}).get("name") == name:
            return r
    raise AssertionError(f"no pending {kind} review for {name}")


def test_link_ambiguous_reference_to_existing_entity(env):
    env.sync()
    book = env.entity("book", "Finding Flow")
    resolve_review(env.db, env.cfg, _review(env, "Flow")["id"], "link", book["id"])
    assert READING in {r["note_id"] for r in env.rels(tgt_id=book["id"], type="mentions")}
    # the decision sticks: reprocessing does not ask again and keeps the link
    env.sync(reprocess=[READING])
    assert not [r for r in env.db.q("SELECT payload FROM reviews WHERE status='pending'")
                if json.loads(r["payload"]).get("candidate", {}).get("name") == "Flow"]
    assert READING in {r["note_id"] for r in env.rels(tgt_id=book["id"], type="mentions")}
    assert env.entities_of("book") == ["Finding Flow", "The Design of Everyday Things"]


def test_create_and_dismiss(env):
    env.sync()
    res = resolve_review(env.db, env.cfg, _review(env, "Alex")["id"], "create")
    assert env.entity("person", "Alex")["id"] == res["entity_id"]
    resolve_review(env.db, env.cfg, _review(env, "Priya")["id"], "dismiss")
    env.sync(reprocess=[FOF, "x-coredata://FIXTURE/ICNote/p106"])
    assert env.entities_of("person").count("Alex") == 1
    assert "Priya" not in env.entities_of("person")
    assert env.db.scalar("SELECT COUNT(*) FROM reviews WHERE status='pending' AND payload LIKE '%Priya%'") == 0


def test_link_requires_same_type(env):
    env.sync()
    person = env.entity("person", "Don Norman")
    with pytest.raises(ValueError):
        resolve_review(env.db, env.cfg, _review(env, "Flow")["id"], "link", person["id"])


def test_relationship_approve_and_reject_persist(env):
    env.sync()
    rel_reviews = env.db.q("SELECT * FROM reviews WHERE kind='relationship' AND status='pending'")
    assert len(rel_reviews) == 2
    semantic = next(r for r in rel_reviews if r["note_id"] == EVENT)
    host = next(r for r in rel_reviews if r["note_id"] != EVENT)
    resolve_review(env.db, env.cfg, semantic["id"], "reject")
    resolve_review(env.db, env.cfg, host["id"], "approve")
    env.sync(reprocess=[EVENT, "x-coredata://FIXTURE/ICNote/p101"])
    assert env.db.scalar("SELECT COUNT(*) FROM reviews WHERE kind='relationship' AND status='pending'") == 0
    sem_rel = env.db.one("SELECT * FROM relationships WHERE origin='semantic'")
    assert sem_rel is None  # rejected suggestions are not recreated
    host_rel = env.rels(type="related_to", label="host of")
    assert host_rel and host_rel[0]["status"] == "accepted"


def test_fuzzy_match_review(env, mock_responses):
    env.sync()
    # a title that extends an existing one: probably different, so the user decides
    mock_responses["Reading list"]["entities"].append(
        {"name": "Finding Flow Again", "type": "book", "surface": "Flow", "evidence": "Flow keeps coming up.",
         "confidence": 0.9})
    env.provider.responses = mock_responses
    env.sync(reprocess=[READING])
    r = _review(env, "Finding Flow Again", kind="entity_match")
    p = json.loads(r["payload"])
    assert p["existing_name"] == "Finding Flow" and 0.8 <= p["score"] < 0.93
    resolve_review(env.db, env.cfg, r["id"], "same")
    book = env.entity("book", "Finding Flow")
    assert "Finding Flow Again" in {a["alias"] for a in env.db.q("SELECT alias FROM aliases WHERE entity_id=?", (book["id"],))}
    assert "Finding Flow Again" not in env.entities_of("book")


def test_merge_entities(env):
    env.sync()
    a = reg.create_entity(env.db, "person", "D. Millman")
    reg.upsert_relationship(env.db, env.note_eid(READING), a, "mentions", READING, "x", 1, "accepted", "user")
    keep = env.entity("person", "Debbie Millman")["id"]
    reg.merge_entities(env.db, a, keep)
    assert env.db.scalar("SELECT status FROM entities WHERE id=?", (a,)) == "merged"
    assert env.rels(src_id=env.note_eid(READING), tgt_id=keep)
    assert reg.resolve_id(env.db, a) == keep
    assert env.db.integrity_check() == []


def test_review_cannot_be_resolved_twice(env):
    env.sync()
    rid = _review(env, "Alex")["id"]
    resolve_review(env.db, env.cfg, rid, "dismiss")
    with pytest.raises(ValueError):
        resolve_review(env.db, env.cfg, rid, "create")
