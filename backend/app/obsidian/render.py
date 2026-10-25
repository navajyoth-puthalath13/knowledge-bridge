"""Render the generated knowledge layer from the SQLite registry.

The graph is kept deliberately simple, so Obsidian's Graph View shows only what matters:

    your note ── Book ── another note
    your note ── Podcast
    your note ── Event

- One page per main topic (book, podcast, event). Each page links to every note that
  mentions it, with the sentence that mentions it. No hub pages (collections, indexes,
  logs) appear as nodes, and people's names are not nodes unless that type is enabled.
- Pages live in one folder per type: <generated>/Books, <generated>/Podcasts, <generated>/Events.
- Bookkeeping (JSON index, backups) goes to <generated>/.kb, which Obsidian hides.

Everything is written through a ScopedWriter bound to the generated folder, so this
module cannot touch the copies of your notes. Pages are fully derived from the registry.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ..config import AppConfig
from ..entities.types import ENTITY_TYPES, NOTE_TYPE, enabled_types
from ..intelligence.normalize import safe_filename
from ..storage.db import Database
from .writer import ScopedWriter, file_sha256, sha256_text

log = logging.getLogger(__name__)
HIDDEN = ".kb"  # Obsidian ignores folders starting with a dot


@dataclass
class RenderStats:
    written: int = 0
    unchanged: int = 0
    removed: int = 0
    backed_up: int = 0


def _disp(s: str) -> str:
    return s.replace("|", "-").replace("[", "(").replace("]", ")").replace("\n", " ")


def _quote(s: str | None, n: int = 220) -> str:
    if not s:
        return ""
    s = " ".join(s.split())
    return " — “" + (s if len(s) <= n else s[:n].rstrip() + "…") + "”"


class Renderer:
    def __init__(self, db: Database, cfg: AppConfig):
        self.db = db
        self.cfg = cfg
        self.gen = cfg.generated_dir
        self.writer = ScopedWriter(cfg.generated_root())
        self.types = {t.key for t in enabled_types(cfg.intelligence.enabled_types)}
        self.entities = {r["id"]: dict(r) for r in db.q("SELECT * FROM entities WHERE status='active'")}
        self.notes = {r["id"]: dict(r) for r in db.q("SELECT * FROM notes")}
        self.aliases: dict[str, list[str]] = defaultdict(list)
        for r in db.q("SELECT entity_id, alias, source FROM aliases ORDER BY source='canonical' DESC, alias"):
            if r["alias"] not in self.aliases[r["entity_id"]]:
                self.aliases[r["entity_id"]].append(r["alias"])
        self.rels = [dict(r) for r in db.q("SELECT * FROM relationships WHERE status='accepted' ORDER BY id")]
        self.paths: dict[str, str] = {}  # entity id -> path relative to generated root (no .md)

    def shown(self, eid: str) -> bool:
        e = self.entities.get(eid)
        return bool(e) and e["type"] in self.types and e["type"] in ENTITY_TYPES

    # ---------------------------------------------------------------- links
    def link(self, eid: str) -> str:
        e = self.entities.get(eid)
        if not e:
            return ""
        if e["type"] == NOTE_TYPE:
            n = self.notes.get(e["note_id"])
            if not n or not n["mirror_path"]:
                return _disp(e["name"])
            target = n["mirror_path"].removesuffix(".md")
            suffix = " (removed from Apple Notes)" if n["status"] == "missing" else ""
            return f"[[{target}|{_disp(n['title'])}]]{suffix}"
        if eid in self.paths:
            return f"[[{self.gen}/{self.paths[eid]}|{_disp(e['name'])}]]"
        return _disp(e["name"])

    def assign_paths(self) -> None:
        used: set[str] = set()
        for e in sorted(self.entities.values(), key=lambda e: (e["created_at"], e["id"])):
            if not self.shown(e["id"]):
                continue
            t = ENTITY_TYPES[e["type"]]
            stem = safe_filename(e["name"])
            cands = [f"{t.folder}/{stem}", f"{t.folder}/{stem} ({t.label})"] + \
                    [f"{t.folder}/{stem} ({t.label} {i})" for i in range(2, 50)]
            prev = (e.get("page_path") or "").removeprefix(self.gen + "/").removesuffix(".md")
            chosen = prev if prev in cands and prev.casefold() not in used else \
                next(c for c in cands if c.casefold() not in used)
            used.add(chosen.casefold())
            self.paths[e["id"]] = chosen

    # ---------------------------------------------------------------- pages
    def page(self, e: dict) -> str:
        t = ENTITY_TYPES[e["type"]]
        eid = e["id"]
        lines = ["---", f"kb_id: {json.dumps(eid)}", f"kb_type: {json.dumps(e['type'])}",
                 f"tags: [{t.key.replace('_', '-')}]"]
        aliases = [a for a in self.aliases.get(eid, []) if a != e["name"]]
        if aliases:
            lines.append("aliases:")
            lines += [f"  - {json.dumps(a, ensure_ascii=False)}" for a in aliases]
        lines += ["---", f"# {e['name']}", "", f"*{t.label} · page maintained by Knowledge Bridge*", ""]

        mentions: dict[str, list[dict]] = defaultdict(list)
        for r in self.rels:
            if r["tgt_id"] == eid and r["type"] in ("mentions", "about") \
                    and self.entities.get(r["src_id"], {}).get("type") == NOTE_TYPE:
                mentions[r["src_id"]].append(r)
        lines.append("## Mentioned in")
        lines.append("")
        if mentions:
            for nid, rs in sorted(mentions.items(), key=lambda kv: self.entities[kv[0]]["name"].casefold()):
                ev = next((r["evidence"] for r in rs if r["evidence"]), None)
                lines.append(f"- {self.link(nid)}{_quote(ev)}")
        else:
            lines.append("_No notes mention this at the moment._")

        # direct links between main topics (e.g. a book discussed on a podcast), only when stated in a note
        related = []
        for r in self.rels:
            if r["type"] in ("related_to", "belongs_to") and eid in (r["src_id"], r["tgt_id"]):
                other = r["tgt_id"] if r["src_id"] == eid else r["src_id"]
                if self.shown(other) and other != eid:
                    related.append((other, r))
        if related:
            lines += ["", "## Related", ""]
            seen = set()
            for other, r in related:
                if other in seen:
                    continue
                seen.add(other)
                label = r["label"] or ("part of" if r["type"] == "belongs_to" else "related to")
                lines.append(f"- {self.link(other)} — {label}{_quote(r['evidence'])}")
        return "\n".join(lines).rstrip() + "\n"

    def build(self) -> dict[str, str]:
        self.assign_paths()
        out = {f"{p}.md": self.page(self.entities[eid]) for eid, p in self.paths.items()}
        ents = [{"id": e["id"], "type": e["type"], "name": e["name"], "aliases": self.aliases.get(e["id"], []),
                 "page": f"{self.gen}/{self.paths[e['id']]}.md" if e["id"] in self.paths else None,
                 "note_id": e["note_id"]} for e in sorted(self.entities.values(), key=lambda e: e["id"])]
        rels = [{k: r[k] for k in ("src_id", "tgt_id", "type", "label", "evidence", "note_id", "confidence", "origin",
                                   "status")} for r in self.rels]
        out[f"{HIDDEN}/entities.json"] = json.dumps(ents, indent=2, ensure_ascii=False) + "\n"
        out[f"{HIDDEN}/relationships.json"] = json.dumps(rels, indent=2, ensure_ascii=False) + "\n"
        return out


def _remove_empty_dirs(root, keep: set[str]) -> None:
    """Remove folders left empty by old layouts; keep the per-type folders (Books, Podcasts, ...)."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        rel = os.path.relpath(dirpath, root)
        if dirpath != str(root) and rel not in keep and not os.listdir(dirpath) \
                and not os.path.basename(dirpath).startswith("."):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def render_all(db: Database, cfg: AppConfig) -> RenderStats:
    r = Renderer(db, cfg)
    desired = r.build()
    stats = RenderStats()
    manifest: dict[str, str] = json.loads(db.scalar("SELECT value FROM meta WHERE key='render_manifest'") or "{}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = cfg.generated_root()

    def backup(rel: str) -> None:
        r.writer.write_bytes(f"{HIDDEN}/backups/{stamp}/{rel}", (root / rel).read_bytes())
        stats.backed_up += 1

    new_manifest: dict[str, str] = {}
    for rel, content in desired.items():
        h = sha256_text(content)
        disk = file_sha256(root / rel)
        if disk == h:
            stats.unchanged += 1
        else:
            if disk is not None and manifest.get(rel) != disk:
                backup(rel)  # someone edited a generated page; keep their copy
            r.writer.write_text(rel, content)
            stats.written += 1
        new_manifest[rel] = h
    for rel, old_hash in manifest.items():
        if rel in desired:
            continue
        disk = file_sha256(root / rel)
        if disk is None:
            continue
        if disk != old_hash:
            backup(rel)
        r.writer.delete(rel)
        stats.removed += 1
    if root.is_dir():
        _remove_empty_dirs(root, {t.folder for t in enabled_types(cfg.intelligence.enabled_types)})
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('render_manifest', ?)", (json.dumps(new_manifest),))
        c.execute("UPDATE entities SET page_path=NULL")
        for eid, p in r.paths.items():
            c.execute("UPDATE entities SET page_path=? WHERE id=?", (f"{cfg.generated_dir}/{p}.md", eid))
    try:
        from .graph_colors import apply_graph_colors

        apply_graph_colors(cfg)
    except Exception as e:  # colours are cosmetic; never fail a sync over them
        log.warning("could not set graph colours: %s", e)
    return stats
