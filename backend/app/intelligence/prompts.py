"""Extraction prompt. Bump PROMPT_VERSION when the prompt or schema changes so
cached results are invalidated."""
from __future__ import annotations

import json

from ..entities.types import EntityType

PROMPT_VERSION = "2026-10-1"

SYSTEM = """You analyse one personal note and return JSON describing the named things it mentions.
You never rewrite, summarise or improve the note. You only identify what is explicitly there.

Rules:
- Extract only entities that are explicitly named in the note text (anywhere, including mid-paragraph).
- Use only these entity types: {type_keys}. Skip anything that does not fit.
- "surface" must be copied exactly from the note. "evidence" must be one sentence copied verbatim from the note.
- "name" is the canonical, properly capitalised name (e.g. the full book title if you are certain of it).
  If a known entity listed below is the same thing, use its exact name.
- Never invent authors, dates, hosts or facts that are not in the note or in the known-entity list.
- Set "ambiguous": true when a reference could refer to several things (e.g. a first name only, or a word that
  may or may not be a book title). Lower "confidence" accordingly.
- "primary_subject": true for the one entity the note is mainly about (e.g. the episode or event the note documents).
- For podcast_episode set "parent" to the show name when the note states it; for event set "parent" to the
  event series when stated.
- "relations" only for relationships the note states (e.g. a person is the author of a book, a guest on an
  episode, a speaker at an event). Use type "related_to" with a short "label".
- "related_notes": only pick from the candidate notes listed, and only when the note and the candidate
  clearly share a specific subject (same book, person, event, or an explicit idea). Do not link notes merely
  because they share a broad theme word. Empty list is fine.
- note_category: "podcast" (notes on a podcast/episode), "book" (notes on a book), "event" (notes on an
  event/meetup/talk), "personal" (the author's own reflections), or "other".

Return exactly this JSON shape:
{{"note_category": "...",
  "entities": [{{"name": "...", "type": "...", "surface": "...", "evidence": "...", "parent": null,
                 "aliases": [], "confidence": 0.0, "ambiguous": false, "primary_subject": false}}],
  "relations": [{{"source": "<entity name>", "target": "<entity name>", "type": "related_to", "label": "...",
                  "evidence": "...", "confidence": 0.0}}],
  "related_notes": [{{"note_ref": "...", "reason": "...", "evidence": "...", "confidence": 0.0}}]}}
"""


def build_messages(title: str, text: str, types: list[EntityType], known: list[tuple[str, str]],
                   candidates: list[tuple[str, str, str]]) -> tuple[str, str]:
    system = SYSTEM.format(type_keys=", ".join(t.key for t in types))
    type_lines = "\n".join(f"- {t.key}: {t.description}" for t in types)
    known_lines = "\n".join(f"- [{t}] {n}" for t, n in known) or "(none)"
    cand_lines = "\n".join(f"- ref={ref} title={json.dumps(ttl)} preview={json.dumps(prev)}"
                           for ref, ttl, prev in candidates) or "(none)"
    user = (
        f"Entity types:\n{type_lines}\n\n"
        f"Known entities that may appear in this note:\n{known_lines}\n\n"
        f"Candidate related notes:\n{cand_lines}\n\n"
        f"NOTE TITLE: {title}\n"
        f"NOTE TEXT:\n<<<\n{text}\n>>>"
    )
    return system, user
