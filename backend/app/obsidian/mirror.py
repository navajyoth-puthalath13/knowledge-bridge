"""Faithful Markdown mirror of a source note.

The mirror body is a deterministic conversion of the note's content. The only
addition is an optional YAML frontmatter block with sync metadata (source id,
dates, attachments) — produced by code, never by the AI, and kept apart from
the body so the body is exactly the note.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..connectors.base import SourceNote
from ..connectors.html_to_md import notes_html_to_markdown

ATTACHMENTS_DIR = "_attachments"


@dataclass
class MirrorRender:
    text: str
    files: list[tuple[str, bytes]] = field(default_factory=list)  # paths relative to the mirror root
    warnings: list[str] = field(default_factory=list)


def source_hash(note: SourceNote) -> str:
    h = hashlib.sha256()
    h.update(note.title.encode())
    h.update(b"\0")
    h.update(note.body.encode())
    for a in note.attachments:
        h.update(b"\0" + a.name.encode() + b"\0" + (a.data_b64 or a.url or "").encode())
    return h.hexdigest()


def attachment_dir(note_id: str) -> str:
    # keyed by note id, not title, so renames do not move attachments
    return f"{ATTACHMENTS_DIR}/{hashlib.sha256(note_id.encode()).hexdigest()[:12]}"


def render_mirror(note: SourceNote, mirror_dir: str, with_frontmatter: bool = True) -> MirrorRender:
    att_rel = attachment_dir(note.id)
    att_vault = f"{mirror_dir}/{att_rel}"
    out = MirrorRender(text="")
    if note.body_format == "html":
        conv = notes_html_to_markdown(note.body, image_dir=att_vault)
        body = conv.markdown
        out.warnings += conv.warnings
        out.files += [(f"{att_rel}/{name}", data) for name, data in conv.images]
    else:
        body = note.body if note.body.endswith("\n") else note.body + "\n"

    att_meta = []
    for i, a in enumerate(note.attachments):
        entry = {"name": a.name}
        if a.url:
            entry["url"] = a.url
        if a.data_b64:
            safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in a.name).strip() or f"attachment-{i}"
            rel = f"{att_rel}/{i}-{safe}"
            out.files.append((rel, base64.b64decode(a.data_b64)))
            entry["file"] = f"{mirror_dir}/{rel}"
        elif a.error:
            entry["error"] = a.error
            out.warnings.append(f"attachment '{a.name}' not exported: {a.error}")
        att_meta.append(entry)

    if with_frontmatter:
        fm = ["---", f"apple_notes_id: {json.dumps(note.id)}"]
        if note.created_at:
            fm.append(f"created: {json.dumps(note.created_at)}")
        if note.modified_at:
            fm.append(f"modified: {json.dumps(note.modified_at)}")
        if note.folder:
            fm.append(f"source_folder: {json.dumps(note.folder)}")
        if att_meta:
            fm.append("attachments:")
            for e in att_meta:
                fm.append(f"  - {json.dumps(e, ensure_ascii=False)}")
        fm.append("kb_mirror: true")
        fm.append("---")
        out.text = "\n".join(fm) + "\n" + body
    else:
        out.text = body
    return out


def split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[: end + 5], text[end + 5:]
    return "", text


def read_mirror_body(path: Path) -> str:
    return split_frontmatter(path.read_text("utf-8"))[1]
