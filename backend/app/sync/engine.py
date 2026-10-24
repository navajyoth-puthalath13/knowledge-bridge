"""Sync engine: Apple Notes -> source mirror -> intelligence -> generated pages.

Guarantees
- The source (Apple Notes) is only read.
- Mirror files are written atomically, through a writer confined to the mirror
  folder, and only when the file on disk is exactly what we last wrote.
  Otherwise the write stops and a conflict is recorded for the user to resolve.
- Every mirror write is journalled first, so an interrupted run is recovered
  on the next start instead of being misreported as a conflict.
- Notes that vanish from the source are marked missing; their mirror files and
  the entities they mention are kept.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..config import AppConfig
from ..connectors.base import Connector, ConnectorError, NoteSummary, SourceNote
from ..intelligence.normalize import safe_filename
from ..intelligence.pipeline import Context, IntegrityError, process_note
from ..obsidian.mirror import render_mirror, source_hash
from ..obsidian.render import render_all
from ..obsidian.writer import ScopedWriter, file_sha256, sha256_text
from ..providers.base import LLMProvider, ProviderError
from ..storage.db import Database, now

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int, str], None]


@dataclass
class SyncStats:
    discovered: int = 0
    created: int = 0
    updated: int = 0
    renamed: int = 0
    unchanged: int = 0
    skipped: int = 0
    missing: int = 0
    locked: int = 0
    conflicts: int = 0
    processed: int = 0
    llm_calls: int = 0
    entities_created: int = 0
    entities_reused: int = 0
    relationships: int = 0
    reviews: int = 0
    pages_written: int = 0
    errors: int = 0

    @property
    def mirrored(self) -> int:
        return self.created + self.updated + self.renamed


@dataclass
class RunReport:
    run_id: int | None
    dry_run: bool
    status: str = "ok"
    stats: SyncStats = field(default_factory=SyncStats)
    errors: list[dict] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stats"]["mirrored"] = self.stats.mirrored
        return d


class SyncEngine:
    def __init__(self, db: Database, cfg: AppConfig, connector: Connector, provider: LLMProvider | None,
                 progress: Progress | None = None, cancel: threading.Event | None = None):
        self.db = db
        self.cfg = cfg
        self.connector = connector
        self.provider = provider
        self.progress = progress or (lambda *a: None)
        self.cancel = cancel or threading.Event()
        self.refresh: set[str] = set()  # note ids whose cached AI analysis must be ignored

    # ------------------------------------------------------------------ helpers
    @property
    def vault(self) -> Path:
        return self.cfg.vault()

    def mirror_writer(self) -> ScopedWriter:
        return ScopedWriter(self.cfg.mirror_root())

    def _rel_to_mirror(self, vault_rel: str) -> str:
        return str(Path(vault_rel).relative_to(self.cfg.mirror_dir))

    def _subfolder(self, s: NoteSummary) -> str:
        base = self.cfg.source.folder.strip("/")
        f = (s.folder or "").strip("/")
        if self.cfg.source.kind == "apple_notes" and base and f.startswith(base + "/"):
            return "/".join(safe_filename(p) for p in f[len(base) + 1:].split("/"))
        if self.cfg.source.kind == "folder" and f:
            return "/".join(safe_filename(p) for p in f.split("/"))
        return ""

    def _desired_path(self, note_id: str, title: str, sub: str, current: str | None) -> str:
        base_dir = f"{self.cfg.mirror_dir}/{sub}" if sub else self.cfg.mirror_dir
        stem = safe_filename(title)
        if current and Path(current).parent.as_posix() == base_dir:
            cur_stem = Path(current).stem
            if cur_stem == stem or (cur_stem.startswith(stem + " (") and cur_stem.endswith(")")):
                return current  # keep an existing disambiguated name stable
        taken = {r["mirror_path"].casefold() for r in self.db.q(
            "SELECT mirror_path FROM notes WHERE id != ? AND mirror_path IS NOT NULL", (note_id,))}
        n = 1
        while True:
            cand = f"{base_dir}/{stem}.md" if n == 1 else f"{base_dir}/{stem} ({n}).md"
            on_disk = (self.vault / cand).exists() and cand != current
            if cand.casefold() not in taken and not on_disk:
                return cand
            n += 1

    def _err(self, report: RunReport, note: str, message: str, kind: str = "error", note_id: str | None = None):
        log.warning("%s: %s", note, message)
        report.errors.append({"note": note, "note_id": note_id, "message": message, "kind": kind})
        report.stats.errors += 1

    # ------------------------------------------------------------------ recovery
    def recover(self) -> int:
        """Finish or roll back writes interrupted by a crash or power loss."""
        rows = self.db.q("SELECT * FROM pending_writes ORDER BY id")
        with self.db.tx() as c:
            for r in rows:
                if r["op"] == "write" and file_sha256(self.vault / r["path"]) == r["content_hash"]:
                    # The file was fully written; adopt it. A first-time write may have no notes row yet:
                    # create a placeholder (source_hash NULL) so the next sync refreshes it in place.
                    c.execute("INSERT OR IGNORE INTO notes(id, title, status, first_seen_at, updated_at)"
                              " VALUES (?,?,?,?,?)", (r["note_id"], Path(r["path"]).stem, "active", now(), now()))
                    c.execute("UPDATE notes SET mirror_path=?, mirror_hash=? WHERE id=?",
                              (r["path"], r["content_hash"], r["note_id"]))
                elif r["op"] == "rename" and (self.vault / r["path"]).exists() and not (self.vault / r["old_path"]).exists():
                    c.execute("UPDATE notes SET mirror_path=? WHERE id=?", (r["path"], r["note_id"]))
                # otherwise the write never happened; the note is redone on the next sync
            c.execute("DELETE FROM pending_writes")
            c.execute("UPDATE sync_runs SET status='interrupted', finished_at=? WHERE status='running'", (now(),))
        if rows:
            log.info("recovered %d interrupted writes", len(rows))
        return len(rows)

    # ------------------------------------------------------------------ mirror
    def _open_conflict(self, note_id: str, path: str, expected: str | None, found: str | None, incoming: str,
                       kind: str) -> None:
        with self.db.tx() as c:
            existing = c.execute("SELECT id FROM conflicts WHERE note_id=? AND status='open'", (note_id,)).fetchone()
            if existing:
                c.execute("UPDATE conflicts SET incoming_text=?, incoming_hash=?, found_hash=? WHERE id=?",
                          (incoming, sha256_text(incoming), found, existing["id"]))
            else:
                c.execute("INSERT INTO conflicts(note_id, mirror_path, expected_hash, found_hash, incoming_hash,"
                          " incoming_text, kind, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (note_id, path, expected, found, sha256_text(incoming), incoming, kind, now()))
            c.execute("UPDATE notes SET status='conflict', updated_at=? WHERE id=?", (now(), note_id))

    def write_mirror(self, note_id: str, path: str, text: str) -> str:
        h = sha256_text(text)
        with self.db.tx() as c:
            c.execute("INSERT INTO pending_writes(note_id, op, path, content_hash, created_at) VALUES (?,?,?,?,?)",
                      (note_id, "write", path, h, now()))
        self.mirror_writer().write_text(self._rel_to_mirror(path), text)
        return h

    def _mirror_note(self, sn: SourceNote, summary: NoteSummary, row, report: RunReport, dry: bool) -> None:
        st = report.stats
        title = sn.title or summary.title or "Untitled"
        if sn.locked:
            st.locked += 1
            report.plan.append({"note": title, "action": "skip", "detail": "password-protected note cannot be read"})
            if not dry:
                self._upsert_note_row(sn, summary, row, status="locked")
            return
        shash = source_hash(sn)
        render = render_mirror(sn, self.cfg.mirror_dir, self.cfg.mirror_frontmatter)
        current = row["mirror_path"] if row else None
        desired = self._desired_path(sn.id, title, self._subfolder(summary), current)

        if row and row["status"] == "detached":
            st.skipped += 1
            report.plan.append({"note": title, "action": "skip", "detail": "detached: Obsidian copy is kept"})
            return

        if row and current:
            disk = file_sha256(self.vault / current)
            if disk != row["mirror_hash"] and disk is not None and row["source_hash"] == shash:
                # Edited in Obsidian, but nothing new from Apple Notes: leave it alone.
                st.skipped += 1
                report.plan.append({"note": title, "action": "skip", "path": current,
                                    "detail": "mirror edited in Obsidian; Apple Note unchanged"})
                if not dry:
                    report.errors.append({"note": title, "note_id": sn.id, "kind": "warning",
                                          "message": "mirror was edited in Obsidian; it will be flagged as a conflict "
                                                     "if the Apple Note changes"})
                return
            if disk != row["mirror_hash"]:
                kind = "deleted_in_obsidian" if disk is None else "edited_in_obsidian"
                st.conflicts += 1
                report.plan.append({"note": title, "action": "conflict", "path": current, "detail": kind})
                if not dry:
                    self._open_conflict(sn.id, current, row["mirror_hash"], disk, render.text, kind)
                    self._err(report, title, f"conflict ({kind.replace('_', ' ')}); write stopped", "conflict", sn.id)
                return
            if row["source_hash"] == shash and current == desired and row["status"] == "active" \
                    and disk == sha256_text(render.text):
                st.unchanged += 1
                if not dry:
                    self._upsert_note_row(sn, summary, row, status="active")
                return

        action = "create" if not current else ("rename" if current != desired else "update")
        report.plan.append({"note": title, "action": action, "path": desired,
                            **({"from": current} if action == "rename" else {}),
                            **({"warnings": render.warnings} if render.warnings else {})})
        if dry:
            setattr(st, {"create": "created", "update": "updated", "rename": "renamed"}[action],
                    getattr(st, {"create": "created", "update": "updated", "rename": "renamed"}[action]) + 1)
            return

        mw = self.mirror_writer()
        for rel, data in render.files:
            target = mw.resolve(rel)
            if not target.exists():
                mw.write_bytes(rel, data)
        if action == "rename":
            with self.db.tx() as c:
                c.execute("INSERT INTO pending_writes(note_id, op, path, old_path, created_at) VALUES (?,?,?,?,?)",
                          (sn.id, "rename", desired, current, now()))
            mw.move(self._rel_to_mirror(current), self._rel_to_mirror(desired))
            with self.db.tx() as c:
                c.execute("UPDATE notes SET mirror_path=? WHERE id=?", (desired, sn.id))
            st.renamed += 1
        h = self.write_mirror(sn.id, desired, render.text)
        with self.db.tx() as c:
            self._upsert_note_row(sn, summary, row, status="active", mirror_path=desired, mirror_hash=h,
                                  source_hash=shash)
            c.execute("DELETE FROM pending_writes WHERE note_id=?", (sn.id,))
        if action == "create":
            st.created += 1
        elif action == "update":
            st.updated += 1
        for w in render.warnings:
            report.errors.append({"note": title, "note_id": sn.id, "message": w, "kind": "warning"})

    def _upsert_note_row(self, sn: SourceNote | NoteSummary, summary: NoteSummary, row, status: str,
                         mirror_path: str | None = None, mirror_hash: str | None = None,
                         source_hash: str | None = None) -> None:
        title = sn.title or summary.title
        with self.db.tx() as c:
            if row is None:
                c.execute("INSERT INTO notes(id, title, folder, created_at, modified_at, source_hash, mirror_path,"
                          " mirror_hash, status, first_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (sn.id, title, summary.folder, sn.created_at or summary.created_at,
                           summary.modified_at, source_hash, mirror_path, mirror_hash, status, now(), now()))
            else:
                c.execute("UPDATE notes SET title=?, folder=?, modified_at=?, status=?, updated_at=?,"
                          " source_hash=COALESCE(?, source_hash), mirror_path=COALESCE(?, mirror_path),"
                          " mirror_hash=COALESCE(?, mirror_hash) WHERE id=?",
                          (title, summary.folder, summary.modified_at, status, now(), source_hash, mirror_path,
                           mirror_hash, sn.id))

    # ------------------------------------------------------------------ run
    def run(self, dry_run: bool = False, trigger: str = "manual", reprocess: list[str] | None = None,
            mirror: bool = True) -> RunReport:
        report = RunReport(run_id=None, dry_run=dry_run)
        self.refresh |= set(reprocess or [])  # explicit reprocessing asks the model again
        problems = self.cfg.problems()
        if problems:
            report.status = "failed"
            for p in problems:
                self._err(report, "configuration", p)
            return report
        if not dry_run:
            self.recover()
            with self.db.tx() as c:
                report.run_id = c.execute("INSERT INTO sync_runs(started_at, dry_run, trigger) VALUES (?,?,?)",
                                          (now(), 0, trigger)).lastrowid
        try:
            if mirror:
                self._mirror_phase(report, dry_run)
            if not dry_run and report.status != "failed":
                self._intelligence_phase(report, reprocess or [])
                self.progress("render", 0, 1, "Writing knowledge pages")
                report.stats.pages_written = render_all(self.db, self.cfg).written
        except Exception as e:  # unexpected: record and surface, never half-silently continue
            log.exception("sync failed")
            report.status = "failed"
            self._err(report, "sync", f"{type(e).__name__}: {e}")
        if report.status != "failed" and any(e["kind"] in ("error", "conflict") for e in report.errors):
            report.status = "partial"
        if not dry_run and report.run_id:
            with self.db.tx() as c:
                c.execute("UPDATE sync_runs SET finished_at=?, status=?, stats=?, errors=? WHERE id=?",
                          (now(), report.status, json.dumps(report.to_dict()["stats"]), json.dumps(report.errors),
                           report.run_id))
            if report.status != "failed":
                render_all(self.db, self.cfg)  # refresh Logs/Sync history.md with this run
        self.progress("done", 1, 1, report.status)
        return report

    def _mirror_phase(self, report: RunReport, dry: bool) -> None:
        st = report.stats
        self.progress("discover", 0, 0, "Reading Apple Notes folder")
        try:
            summaries = self.connector.list_notes()
        except ConnectorError as e:
            report.status = "failed"
            self._err(report, "connector", str(e))
            return
        st.discovered = len(summaries)
        rows = {r["id"]: r for r in self.db.q("SELECT * FROM notes")}
        active = sum(1 for r in rows.values() if r["status"] == "active")
        if not summaries and active:
            report.status = "failed"
            self._err(report, "connector", f"The source folder returned no notes but {active} were synced before. "
                      "Refusing to mark them all missing; check the folder selection.")
            return

        to_fetch: list[NoteSummary] = []
        for s in summaries:
            r = rows.get(s.id)
            mirror_ok = r is not None and r["mirror_path"] and file_sha256(self.vault / r["mirror_path"]) == r["mirror_hash"]
            if r and r["modified_at"] == s.modified_at and r["title"] == s.title and r["status"] in ("active", "locked") \
                    and (mirror_ok or r["status"] == "locked"):
                st.unchanged += 1
                continue
            to_fetch.append(s)

        by_id = {s.id: s for s in to_fetch}
        for i in range(0, len(to_fetch), 20):
            if self.cancel.is_set():
                break
            batch = to_fetch[i:i + 20]
            self.progress("mirror", i, len(to_fetch), f"Reading {len(batch)} notes")
            try:
                fetched = self.connector.fetch([s.id for s in batch])
            except ConnectorError as e:
                for s in batch:
                    self._err(report, s.title, str(e), note_id=s.id)
                continue
            for sn in fetched:
                try:
                    self._mirror_note(sn, by_id[sn.id], rows.get(sn.id), report, dry)
                except Exception as e:
                    log.exception("mirror failed for %s", sn.title)
                    self._err(report, sn.title, f"{type(e).__name__}: {e}", note_id=sn.id)

        seen = {s.id for s in summaries}
        for nid, r in rows.items():
            if nid not in seen and r["status"] not in ("missing",):
                st.missing += 1
                report.plan.append({"note": r["title"], "action": "missing",
                                    "detail": "no longer in the source folder; mirror and entities are kept"})
                if not dry:
                    with self.db.tx() as c:
                        c.execute("UPDATE notes SET status='missing', updated_at=? WHERE id=?", (now(), nid))

        if dry:
            pending = [r["title"] for r in rows.values() if r["status"] == "active"
                       and r["processed_hash"] != r["source_hash"]]
            new_or_changed = [p["note"] for p in report.plan if p["action"] in ("create", "update", "rename")]
            names = sorted(set(pending) | set(new_or_changed))
            where = ("would be sent to " + self.provider.name) if self.provider and self.provider.sends_data_off_device \
                else "would be analysed locally"
            for n in names:
                report.plan.append({"note": n, "action": "analyse", "detail": where})

    def _intelligence_phase(self, report: RunReport, reprocess: list[str]) -> None:
        st = report.stats
        ctx = Context(self.db, self.cfg, self.provider, refresh=self.refresh)
        rows = self.db.q("SELECT id, title FROM notes WHERE status='active' AND mirror_path IS NOT NULL AND "
                         "(processed_hash IS NULL OR processed_hash != source_hash OR last_error IS NOT NULL)")
        ids = [r["id"] for r in rows] + [i for i in reprocess if i not in {r["id"] for r in rows}]
        for n, nid in enumerate(ids):
            if self.cancel.is_set():
                break
            title = self.db.scalar("SELECT title FROM notes WHERE id=?", (nid,)) or nid
            self.progress("analyse", n, len(ids), f"Analysing “{title}”")
            try:
                res = process_note(ctx, nid)
            except (ProviderError, IntegrityError) as e:
                with self.db.tx() as c:
                    c.execute("UPDATE notes SET last_error=? WHERE id=?", (str(e), nid))
                self._err(report, title, str(e), note_id=nid)
                continue
            except Exception as e:
                log.exception("processing failed for %s", title)
                with self.db.tx() as c:
                    c.execute("UPDATE notes SET last_error=? WHERE id=?", (f"{type(e).__name__}: {e}", nid))
                self._err(report, title, f"{type(e).__name__}: {e}", note_id=nid)
                continue
            st.processed += 1
            st.llm_calls += int(res.llm_called)
            st.entities_created += len(res.entities_created)
            st.entities_reused += len(res.entities_reused)
            st.relationships += res.relationships
            st.reviews += res.reviews
            for w in res.warnings:
                report.errors.append({"note": title, "note_id": nid, "message": w, "kind": "warning"})
