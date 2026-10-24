"""Entity recognition, deduplication, relationships and generated pages."""
import json

from app.intelligence.normalize import norm, similarity
from app.providers.mock import MockProvider

from .conftest import EVENT, FOF, PERSONAL, PODCAST, READING


def test_normalisation():
    assert norm("The Design of Everyday Things") == "design of everyday things"
    assert norm("Finding  Flow!") == "finding flow"
    assert 0.8 <= similarity("finding flow", "finding flow again") < 0.93  # review, not auto-merge
    assert similarity("finding flow", "finding flowe") >= 0.93  # obvious typo merges
    assert similarity("flow", "finding flow") < 0.8


def test_one_canonical_book_for_all_mentions(env):
    env.sync()
    assert env.entities_of("book") == ["Finding Flow", "The Design of Everyday Things"]
    book = env.entity("book", "Finding Flow")
    mentioning = {r["note_id"] for r in env.rels(tgt_id=book["id"], type="mentions", status="accepted")}
    # podcast, design event, and the differently formatted full-title mention at Friends of Figma
    assert mentioning == {PODCAST, EVENT, FOF}
    aliases = {r["alias"] for r in env.db.q("SELECT alias FROM aliases WHERE entity_id=?", (book["id"],))}
    assert "Finding Flow: The Psychology of Engagement with Everyday Life" in aliases


def test_evidence_and_source_references(env):
    env.sync()
    book = env.entity("book", "Finding Flow")
    r = env.rels(tgt_id=book["id"], note_id=PODCAST)[0]
    assert r["evidence"] == "Halfway through, the speaker mentioned Finding Flow and said it changed how she plans her week."
    assert r["src_id"] == env.note_eid(PODCAST)


def test_collections_and_hierarchy(env):
    env.sync()
    books = "collection-books"
    book = env.entity("book", "Finding Flow")
    assert env.rels(src_id=book["id"], tgt_id=books, type="belongs_to")
    show = env.entity("podcast_show", "Design Matters")
    ep = env.db.one("SELECT * FROM entities WHERE type='podcast_episode'")
    assert env.rels(src_id=ep["id"], tgt_id=show["id"], type="belongs_to")
    assert not env.rels(src_id=ep["id"], tgt_id="collection-podcasts")  # via its show, not directly
    assert env.rels(src_id=show["id"], tgt_id="collection-podcasts", type="belongs_to")
    event = env.entity("event", "Design Days 2026")
    series = env.entity("event_series", "Design Days")
    assert env.rels(src_id=event["id"], tgt_id=series["id"], type="belongs_to")
    assert env.rels(src_id=series["id"], tgt_id="collection-design-events", type="belongs_to")
    assert env.rels(src_id=env.note_eid(PERSONAL), tgt_id="collection-personal-thinking", type="belongs_to")
    # primary subject is recorded as "about"
    assert env.rels(src_id=env.note_eid(EVENT), tgt_id=event["id"], type="about")


def test_no_direct_link_between_notes_without_evidence(env):
    env.sync()
    a, b = env.note_eid(PODCAST), env.note_eid(EVENT)
    direct = [r for r in env.db.q("SELECT * FROM relationships WHERE (src_id=? AND tgt_id=?) OR (src_id=? AND tgt_id=?)",
                                  (a, b, b, a))]
    assert all(r["status"] == "pending" for r in direct)  # semantic suggestions wait for review
    assert not env.rels(src_id=env.note_eid(PERSONAL), type="related_to")  # shared theme alone is not a link


def test_hallucinated_and_invalid_entities_are_dropped(env, mock_responses):
    mock_responses["Reading list"]["entities"].append({"name": "Lunar Crystals", "type": "book", "surface": "Lunar"})
    mock_responses["Reading list"]["entities"].append({"name": "Mars", "type": "planet", "surface": "Flow"})
    env.provider = MockProvider(mock_responses)
    rep = env.sync()
    assert "Lunar Crystals" not in env.entities_of("book")
    assert "Mihaly Csikszentmihalyi" not in env.entities_of("person")  # not in the podcast note text
    msgs = " ".join(e["message"] for e in rep.errors)
    assert "not found in the note text" in msgs and "unknown entity type 'planet'" in msgs


def test_ambiguous_references_go_to_review(env):
    env.sync()
    pending = {json.loads(r["payload"])["candidate"]["name"]: r for r in
               env.db.q("SELECT * FROM reviews WHERE kind='ambiguous_entity' AND status='pending'")}
    assert {"Alex", "Flow", "Priya"} <= set(pending)
    assert "Alex" not in env.entities_of("person") and "Flow" not in env.entities_of("book")
    flow_opts = json.loads(pending["Flow"]["payload"])["options"]
    assert flow_opts and flow_opts[0]["name"] == "Finding Flow"


def test_stated_relationship_between_entities(env):
    env.sync()
    don, book = env.entity("person", "Don Norman"), env.entity("book", "The Design of Everyday Things")
    r = env.rels(src_id=don["id"], tgt_id=book["id"], type="related_to")[0]
    assert r["status"] == "accepted" and r["label"] == "author of"
    # low-confidence stated relation waits for review
    host = env.rels(src_id=env.entity("person", "Debbie Millman")["id"], type="related_to")[0]
    assert host["status"] == "pending"


def test_idempotent_second_run(env):
    env.sync()
    counts = env.db.one("SELECT (SELECT COUNT(*) FROM entities) e, (SELECT COUNT(*) FROM relationships) r, "
                        "(SELECT COUNT(*) FROM reviews WHERE status='pending') v")
    pages = {p: p.read_bytes() for p in env.gen("").rglob("*.md") if "Logs" not in p.parts}
    calls = len(env.provider.calls)
    rep = env.sync()
    assert rep.stats.mirrored == 0 and rep.stats.processed == 0 and len(env.provider.calls) == calls
    assert tuple(env.db.one("SELECT (SELECT COUNT(*) FROM entities) e, (SELECT COUNT(*) FROM relationships) r, "
                            "(SELECT COUNT(*) FROM reviews WHERE status='pending') v")) == tuple(counts)
    assert {p: p.read_bytes() for p in env.gen("").rglob("*.md") if "Logs" not in p.parts} == pages


def test_reprocessing_does_not_duplicate(env):
    env.sync()
    n_ent = env.db.scalar("SELECT COUNT(*) FROM entities")
    n_rel = env.db.scalar("SELECT COUNT(*) FROM relationships")
    env.sync(reprocess=[PODCAST, EVENT, FOF, READING])
    assert env.db.scalar("SELECT COUNT(*) FROM entities") == n_ent
    assert env.db.scalar("SELECT COUNT(*) FROM relationships") == n_rel


def test_generated_pages_and_links(env):
    env.sync()
    page = env.gen("Entities/Books/Finding Flow.md").read_text("utf-8")
    assert 'kb_id: "book-finding-flow-' in page
    assert "[[Knowledge Bridge/Collections/Books|Books]]" in page
    for t in ["Design Matters — Debbie Millman with a product designer", "Design Days 2026 — Day 2",
              "Friends of Figma Kochi — March meetup"]:
        assert f"[[Apple Notes/{t}|{t}]]" in page
    books = env.gen("Collections/Books.md").read_text("utf-8")
    assert "[[Knowledge Bridge/Entities/Books/Finding Flow|Finding Flow]] · 3 mentions" in books
    personal = env.gen("Collections/Personal Thinking.md").read_text("utf-8")
    assert "Entities/Personal Thinking/Engagement and difficult problems" in personal
    idx = json.loads(env.gen("Index/entities.json").read_text("utf-8"))
    assert any(e["name"] == "Finding Flow" for e in idx)
    rels = json.loads(env.gen("Index/relationships.json").read_text("utf-8"))
    assert all(r["status"] == "accepted" for r in rels)
    # every [[link]] in generated pages resolves to a file in the vault
    import re
    for p in env.gen("").rglob("*.md"):
        for target in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", p.read_text("utf-8")):
            assert (env.vault / f"{target}.md").exists(), f"broken link {target} in {p.name}"


def test_disabled_entity_type_is_not_extracted(env):
    env.cfg.intelligence.enabled_types["person"] = False
    env.sync()
    assert env.entities_of("person") == []
    assert env.entities_of("book")


def test_rules_only_mode_recognises_known_entities(env):
    env.sync()
    env.edit_source("personal-engagement", html="<div>Engagement and difficult problems</div>"
                    "<div>Rereading The Design of Everyday Things this week.</div>", modified_at="2026-10-01T00:00:00Z")
    rep = env.sync(provider=None)
    assert rep.stats.processed == 1 and rep.stats.llm_calls == 0
    book = env.entity("book", "The Design of Everyday Things")
    r = env.rels(tgt_id=book["id"], note_id=PERSONAL)
    assert r and r[0]["origin"] == "rule"


def test_llm_results_are_cached(env):
    env.sync()
    calls = len(env.provider.calls)
    # rebuilding the registry for a note without changes uses the cache (no new provider call)
    env.db.conn().execute("UPDATE notes SET processed_hash=NULL WHERE id=?", (PODCAST,))
    env.sync()
    assert len(env.provider.calls) == calls
    # explicit reprocessing bypasses the cache
    env.sync(reprocess=[PODCAST])
    assert len(env.provider.calls) == calls + 1
