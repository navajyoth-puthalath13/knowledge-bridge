"""Per-note intelligence pipeline.

Reads a mirrored note (read-only), asks the provider for structured entities,
validates the output against the note text, reconciles it with the canonical
registry and stores mentions/relationships with evidence. It never writes to
the mirror; page rendering happens afterwards in obsidian/render.py.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ..config import AppConfig
from ..entities.types import CATEGORY_COLLECTION, ENTITY_TYPES, enabled_types
from ..connectors.html_to_md import plain_text_of_markdown
from ..obsidian.mirror import read_mirror_body
from ..obsidian.writer import file_sha256, sha256_text
from ..providers.base import LLMProvider, ProviderError
from ..storage.db import Database, now
from . import registry as reg
from .models import ExtractedEntity, NoteAnalysis
from .normalize import find_excerpt, norm, similarity
from .prompts import PROMPT_VERSION, build_messages

log = logging.getLogger(__name__)
PIPELINE_VERSION = "2"  # bump to reprocess every note once (e.g. after entity type changes)
# Free tiers cap tokens per request/minute (Groq ~12k TPM on llama-3.3-70b). Very long notes are
# trimmed for analysis only; the mirror and evidence checks always use the full note.
MAX_ANALYSIS_CHARS = 16_000


class IntegrityError(RuntimeError):
    pass


@dataclass
class NoteResult:
    note_id: str
    title: str
    entities_created: list[str] = field(default_factory=list)
    entities_reused: list[str] = field(default_factory=list)
    relationships: int = 0
    reviews: int = 0
    warnings: list[str] = field(default_factory=list)
    llm_called: bool = False
    cached: bool = False
    error: str | None = None


@dataclass
class Context:
    db: Database
    cfg: AppConfig
    provider: LLMProvider | None
    _texts: dict[str, tuple[str, str]] | None = None  # note_id -> (title, text)
    refresh: set[str] = field(default_factory=set)  # bypass the analysis cache for these notes

    def processing_key(self) -> str:
        p = self.provider.cache_key() if self.provider else "rules"
        return f"{p}|{PROMPT_VERSION}|{PIPELINE_VERSION}"

    def all_texts(self) -> dict[str, tuple[str, str]]:
        if self._texts is None:
            self._texts = {}
            vault = self.cfg.vault()
            for r in self.db.q("SELECT id, title, mirror_path FROM notes WHERE status IN ('active','conflict') "
                               "AND mirror_path IS NOT NULL"):
                try:
                    self._texts[r["id"]] = (r["title"], plain_text_of_markdown(read_mirror_body(vault / r["mirror_path"])))
                except OSError:
                    continue
        return self._texts


def note_text(ctx: Context, note: dict) -> str:
    """Read the mirror file and check it is exactly what the sync engine wrote."""
    path = ctx.cfg.vault() / note["mirror_path"]
    actual = file_sha256(path)
    if actual is None:
        raise IntegrityError(f"mirror file is missing: {note['mirror_path']}")
    if actual != note["mirror_hash"]:
        raise IntegrityError(f"mirror file changed outside Knowledge Bridge: {note['mirror_path']}")
    return plain_text_of_markdown(read_mirror_body(path))


def _guess_category(title: str, text: str) -> str:
    t = (title + " " + text[:400]).casefold()
    if re.search(r"\b(podcast|episode|ep\.?\s*\d+)\b", t):
        return "podcast"
    if re.search(r"\b(meetup|conference|workshop|event|summit|talk at)\b", t):
        return "event"
    return "other"


def _known_entities(ctx: Context, text: str, types: set[str], limit: int = 40) -> list[tuple[str, str]]:
    nt = f" {norm(text)} "
    out: dict[str, tuple[str, str]] = {}
    for r in ctx.db.q("SELECT e.id, e.type, e.name, a.norm FROM aliases a JOIN entities e ON e.id=a.entity_id "
                      "WHERE e.status='active'"):
        if r["type"] in types and len(r["norm"]) >= 3 and f" {r['norm']} " in nt:
            out[r["id"]] = (r["type"], r["name"])
    return list(out.values())[:limit]


def _candidate_notes(ctx: Context, note_id: str, text: str, limit: int = 8) -> list[tuple[str, str, str]]:
    words = {w for w in norm(text).split() if len(w) > 4}
    scored = []
    for nid, (title, body) in ctx.all_texts().items():
        if nid == note_id:
            continue
        other = {w for w in norm(body).split() if len(w) > 4}
        overlap = len(words & other)
        if overlap >= 2:
            scored.append((overlap, nid, title, " ".join(body.split())[:200]))
    scored.sort(reverse=True)
    return [(nid, title, prev) for _, nid, title, prev in scored[:limit]]


def analyse(ctx: Context, note: dict, text: str, result: NoteResult) -> tuple[NoteAnalysis, dict[str, str]]:
    """Returns the validated analysis and a map of candidate refs -> note ids."""
    if ctx.provider is None:
        return NoteAnalysis(note_category=_guess_category(note["title"], text)), {}
    types = [t for t in enabled_types(ctx.cfg.intelligence.enabled_types) if t.extractable]
    known = _known_entities(ctx, text, {t.key for t in types})
    cands = _candidate_notes(ctx, note["id"], text) if ctx.cfg.intelligence.semantic_suggestions else []
    refs = {f"n{i + 1}": nid for i, (nid, _, _) in enumerate(cands)}
    cand_prompt = [(f"n{i + 1}", title, prev) for i, (_, title, prev) in enumerate(cands)]
    if len(text) > MAX_ANALYSIS_CHARS:
        result.warnings.append(f"note is long; only the first {MAX_ANALYSIS_CHARS:,} characters were analysed")
        text = text[:MAX_ANALYSIS_CHARS]
    system, user = build_messages(note["title"], text, types, known, cand_prompt)
    key = sha256_text(json.dumps([ctx.processing_key(), [t.key for t in types], note["title"], text,
                                  [c[1] for c in cand_prompt]]))
    cached = None if note["id"] in ctx.refresh else ctx.db.scalar("SELECT response FROM llm_cache WHERE key=?", (key,))
    if cached:
        result.cached = True
        return NoteAnalysis.model_validate_json(cached), refs
    meta = {"note_title": note["title"], "note_id": note["id"]}
    raw = ctx.provider.complete_json(system, user, meta)
    result.llm_called = True
    try:
        analysis = NoteAnalysis.model_validate_json(_strip_fences(raw))
    except ValidationError as e:
        log.info("invalid model output for %s, asking once more: %s", note["title"], e.errors()[:3])
        raw = ctx.provider.complete_json(system, user + f"\n\nYour previous reply was invalid ({str(e)[:400]}). "
                                         "Return only valid JSON in the required shape.", meta)
        try:
            analysis = NoteAnalysis.model_validate_json(_strip_fences(raw))
        except ValidationError as e2:
            raise ProviderError(f"model output failed validation: {str(e2)[:300]}") from e2
    with ctx.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO llm_cache(key, response, created_at) VALUES (?,?,?)",
                  (key, analysis.model_dump_json(), now()))
    return analysis, refs


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    return m.group(1) if m else raw


# --------------------------------------------------------------------------- resolution

def _evidence_for(text: str, ent: ExtractedEntity) -> str | None:
    """Verbatim excerpt from the note supporting this entity, or None if it is not in the note."""
    for needle in (ent.evidence, ent.surface, ent.name, *ent.aliases):
        if needle and (ex := find_excerpt(text, needle)):
            if needle == ent.evidence:
                return needle if len(needle) <= 400 else ex
            return ex
    return None


def apply_candidate(db: Database, note_id: str, note_eid: str, cand: dict, entity_id: str, cfg: AppConfig) -> int:
    """Record that `note_id` mentions/is about `entity_id`, plus the parent link. Returns relationships written."""
    n = 0
    rel_type = "about" if cand.get("primary_subject") else "mentions"
    if reg.upsert_relationship(db, note_eid, entity_id, rel_type, note_id, cand.get("evidence"),
                               cand.get("confidence"), "accepted", cand.get("origin", "llm")):
        n += 1
    t = ENTITY_TYPES.get(cand["type"])
    parent = cand.get("parent")
    if t and t.parent_type and parent and cfg.intelligence.enabled_types.get(
            t.parent_type, ENTITY_TYPES[t.parent_type].enabled_by_default):
        m = reg.find_match(db, t.parent_type, [parent], cfg.intelligence.fuzzy_auto_merge)
        pid = m.entity_id or reg.create_entity(db, t.parent_type, parent)
        if reg.upsert_relationship(db, entity_id, pid, "belongs_to", note_id, cand.get("evidence"),
                                   cand.get("confidence"), "accepted", "llm"):
            n += 1
    return n


def _maybe_alias(db: Database, entity_id: str, ent: ExtractedEntity) -> None:
    canonical = norm(ent.name)
    reg.add_alias(db, entity_id, ent.name)
    for a in ent.aliases:
        reg.add_alias(db, entity_id, a)
    # a surface form becomes an alias only when it is clearly the same name; a bare
    # "Flow" must not become an alias of "Finding Flow"
    if ent.surface and similarity(norm(ent.surface), canonical) >= 0.93:
        reg.add_alias(db, entity_id, ent.surface)


def resolve_entity(ctx: Context, note_id: str, note_eid: str, ent: ExtractedEntity, evidence: str,
                   result: NoteResult) -> str | None:
    db, ic = ctx.db, ctx.cfg.intelligence
    cand = ent.model_dump() | {"evidence": evidence}
    names = [ent.name, *ent.aliases] + ([ent.surface] if ent.surface else [])
    ambig_key = f"ambig:{note_id}:{ent.type}:{norm(ent.name)}"
    decision = reg.get_decision(db, ambig_key)
    if decision == "dismiss":
        return None
    if decision and decision.startswith("link:"):
        eid = reg.resolve_id(db, decision[5:])
        _maybe_alias(db, eid, ent)
        result.relationships += apply_candidate(db, note_id, note_eid, cand, eid, ctx.cfg)
        result.entities_reused.append(eid)
        return eid

    m = reg.find_match(db, ent.type, names, ic.fuzzy_review)
    if m.entity_id and (m.kind == "exact" or m.score >= ic.fuzzy_auto_merge):
        _maybe_alias(db, m.entity_id, ent)
        result.relationships += apply_candidate(db, note_id, note_eid, cand, m.entity_id, ctx.cfg)
        result.entities_reused.append(m.entity_id)
        return m.entity_id

    if m.entity_id:  # fuzzy: maybe the same thing
        mkey = f"match:{ent.type}:{norm(ent.name)}:{m.entity_id}"
        d = reg.get_decision(db, mkey)
        if d == "same":
            _maybe_alias(db, m.entity_id, ent)
            result.relationships += apply_candidate(db, note_id, note_eid, cand, m.entity_id, ctx.cfg)
            result.entities_reused.append(m.entity_id)
            return m.entity_id
        if d != "different":
            existing = db.one("SELECT name FROM entities WHERE id=?", (m.entity_id,))
            reg.add_review(db, "entity_match", note_id, {
                "candidate": cand, "note_entity_id": note_eid, "existing_id": m.entity_id,
                "existing_name": existing["name"], "score": round(m.score, 3)}, f"{mkey}:{note_id}")
            result.reviews += 1
            return None

    single_token_person = ent.type == "person" and len(ent.name.split()) < 2
    if decision is None and (ent.ambiguous or single_token_person or ent.confidence < ic.auto_accept_confidence):
        options = []
        for r in db.q("SELECT e.id, e.name, a.norm FROM aliases a JOIN entities e ON e.id=a.entity_id "
                      "WHERE e.type=? AND e.status='active'", (ent.type,)):
            s = max(similarity(norm(ent.name), r["norm"]),
                    0.7 if norm(ent.name) in r["norm"].split() else 0.0)
            if s >= 0.5:
                options.append((s, r["id"], r["name"]))
        options = sorted({o[1]: o for o in sorted(options)}.values(), reverse=True)[:5]
        reg.add_review(db, "ambiguous_entity", note_id, {
            "candidate": cand, "note_entity_id": note_eid,
            "options": [{"id": i, "name": n, "score": round(s, 2)} for s, i, n in options]}, ambig_key)
        result.reviews += 1
        return None

    eid = reg.create_entity(db, ent.type, ent.name, ent.aliases)
    if ent.surface and similarity(norm(ent.surface), norm(ent.name)) >= 0.93:
        reg.add_alias(db, eid, ent.surface)
    result.relationships += apply_candidate(db, note_id, note_eid, cand, eid, ctx.cfg)
    result.entities_created.append(eid)
    return eid


def _rule_mentions(ctx: Context, note_id: str, note_eid: str, text: str, already: set[str],
                   result: NoteResult) -> None:
    """Deterministic pass: existing canonical names/aliases appearing verbatim in the note."""
    enabled = {t.key for t in enabled_types(ctx.cfg.intelligence.enabled_types)}
    for r in ctx.db.q("SELECT a.entity_id, a.alias, e.type FROM aliases a JOIN entities e ON e.id=a.entity_id "
                      "WHERE e.status='active'"):
        if r["type"] not in enabled or r["entity_id"] in already:
            continue
        alias = r["alias"]
        if len(alias.split()) < 2 and len(alias) < 6:
            continue  # too short to match safely without the model
        if not re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I):
            continue
        cand = {"type": r["type"], "evidence": find_excerpt(text, alias), "confidence": 0.9, "origin": "rule"}
        result.relationships += apply_candidate(ctx.db, note_id, note_eid, cand, r["entity_id"], ctx.cfg)
        already.add(r["entity_id"])
        result.entities_reused.append(r["entity_id"])


def _note_references(ctx: Context, note_id: str, note_eid: str, text: str, result: NoteResult) -> None:
    """A note that names another note's title verbatim references it."""
    for nid, (title, _) in ctx.all_texts().items():
        if nid == note_id or len(title) < 8 or len(title.split()) < 2:
            continue
        if re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", text, re.I):
            tgt = reg.note_entity(ctx.db, nid, title)
            if reg.upsert_relationship(ctx.db, note_eid, tgt, "references", note_id, find_excerpt(text, title),
                                       1.0, "accepted", "rule"):
                result.relationships += 1


def process_note(ctx: Context, note_id: str) -> NoteResult:
    db = ctx.db
    note = dict(db.one("SELECT * FROM notes WHERE id=?", (note_id,)))
    result = NoteResult(note_id, note["title"])
    text = note_text(ctx, note)
    before = note["mirror_hash"]

    analysis, refs = analyse(ctx, note, text, result)  # network call happens outside the DB transaction
    ic = ctx.cfg.intelligence
    enabled = {t.key for t in enabled_types(ic.enabled_types) if t.extractable}

    with db.tx() as c:
        note_eid = reg.note_entity(db, note_id, note["title"])
        # Reprocessing replaces everything this note previously contributed.
        c.execute("DELETE FROM relationships WHERE note_id=? AND origin != 'user'", (note_id,))
        c.execute("UPDATE reviews SET status='superseded' WHERE note_id=? AND status='pending'", (note_id,))

        name_to_id: dict[str, str] = {}
        mentioned: set[str] = set()
        for ent in analysis.entities:
            if ent.type not in enabled:
                if ent.type not in ENTITY_TYPES:
                    result.warnings.append(f"ignored unknown entity type '{ent.type}' for {ent.name}")
                continue
            evidence = _evidence_for(text, ent)
            if evidence is None:
                result.warnings.append(f"dropped '{ent.name}': not found in the note text")
                continue
            eid = resolve_entity(ctx, note_id, note_eid, ent, evidence, result)
            if eid:
                mentioned.add(eid)
                for n in (ent.name, ent.surface, *ent.aliases):
                    if n:
                        name_to_id[norm(n)] = eid

        for rel in analysis.relations:
            s, t = name_to_id.get(norm(rel.source)), name_to_id.get(norm(rel.target))
            if not s or not t:
                continue
            ev = find_excerpt(text, rel.evidence) if rel.evidence else None
            ok = ev is not None and rel.confidence >= ic.auto_accept_confidence
            rid = reg.upsert_relationship(db, s, t, rel.type, note_id, ev or rel.evidence, rel.confidence,
                                          "accepted" if ok else "pending", "llm", rel.label or None)
            if rid:
                result.relationships += 1
                if not ok and db.scalar("SELECT status FROM relationships WHERE id=?", (rid,)) == "pending":
                    reg.add_review(db, "relationship", note_id, {"relationship_id": rid}, f"rel:{rid}")
                    result.reviews += 1

        for rn in analysis.related_notes:
            other = refs.get(rn.note_ref)
            if not other:
                continue
            tgt = reg.note_entity(db, other, ctx.all_texts()[other][0])
            auto = ic.semantic_auto_accept and rn.confidence >= ic.auto_accept_confidence
            rid = reg.upsert_relationship(db, note_eid, tgt, "related_to", note_id, rn.evidence or rn.reason,
                                          rn.confidence, "accepted" if auto else "pending", "semantic",
                                          rn.reason[:60] or None)
            if rid:
                result.relationships += 1
                if db.scalar("SELECT status FROM relationships WHERE id=?", (rid,)) == "pending":
                    reg.add_review(db, "relationship", note_id, {"relationship_id": rid}, f"rel:{rid}")
                    result.reviews += 1

        _rule_mentions(ctx, note_id, note_eid, text, mentioned, result)
        _note_references(ctx, note_id, note_eid, text, result)

        coll = CATEGORY_COLLECTION.get(analysis.note_category)
        if coll:
            reg.upsert_relationship(db, note_eid, reg.collection_id(coll), "belongs_to", note_id, None, None,
                                    "accepted", "rule")
        reg.refresh_structure(db)
        c.execute("UPDATE notes SET processed_hash=source_hash, processed_key=?, processed_at=?, category=?,"
                  " last_error=NULL WHERE id=?", (ctx.processing_key(), now(), analysis.note_category, note_id))

    # Step 11: the mirror must be byte-identical after processing.
    if file_sha256(ctx.cfg.vault() / note["mirror_path"]) != before:
        raise IntegrityError(f"mirror changed during processing: {note['mirror_path']}")
    result.entities_reused = sorted(set(result.entities_reused) - set(result.entities_created))
    return result
