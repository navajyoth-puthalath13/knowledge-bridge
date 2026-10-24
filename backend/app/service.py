"""Long-lived service state: configuration, database, the sync job and the scheduler."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .config import CLOUD_PROVIDERS, FREE_PROVIDERS, AppConfig, data_dir, load_config, save_config
from .connectors import ConnectorError, make_connector
from .connectors.apple_notes import AppleNotesConnector
from .entities.types import COLLECTION_TYPE, NOTE_TYPE
from .intelligence.registry import ensure_collections
from .obsidian.render import render_all
from .obsidian.structure import ensure_structure
from .obsidian.writer import ScopedWriter, file_sha256
from .providers import make_provider
from .security.secrets import get_secret, storage_backend
from .storage.db import Database, now
from .sync.engine import SyncEngine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, data: Path | None = None):
        self.data = data or data_dir()
        self.cfg = load_config()
        # Rules-only was the old default; with a Groq key available, use Groq (free tier).
        if self.cfg.provider.name == "none" and get_secret("groq"):
            self.cfg.provider.name = "groq"
            save_config(self.cfg)
            log.info("Groq key found: switched AI provider from rules-only to Groq")
        self.db = Database(self.data / "knowledge-bridge.sqlite3")
        ensure_collections(self.db)
        self.job_lock = threading.Lock()
        self.cancel = threading.Event()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.activity: dict = {"running": False}
        self.last_report: dict | None = None
        self.next_run_at: datetime | None = None
        self._health_cache: tuple[float, tuple[bool, str]] | None = None
        self.scheduler = threading.Thread(target=self._scheduler_loop, name="kb-scheduler", daemon=True)
        if self.cfg.vault() and not self.cfg.problems():
            SyncEngine(self.db, self.cfg, make_connector(self.cfg), None).recover()
        self._schedule_next(initial=True)
        self.scheduler.start()

    # ------------------------------------------------------------------ config
    def update_config(self, cfg: AppConfig) -> list[str]:
        source_changed = cfg.source != self.cfg.source
        self.cfg = cfg
        save_config(cfg)
        if source_changed:
            self._health_cache = None
        created = self.prepare_vault()
        self._schedule_next()
        return created

    def prepare_vault(self) -> list[str]:
        """Create the folder structure and collection pages in the vault (safe to repeat)."""
        if self.cfg.problems() or self.job_lock.locked():
            return []
        created = ensure_structure(self.cfg)
        render_all(self.db, self.cfg)
        if created:
            log.info("created vault folders: %s", ", ".join(created))
        return created

    # ------------------------------------------------------------------ jobs
    def _progress(self, phase: str, cur: int, total: int, msg: str) -> None:
        self.activity.update(phase=phase, current=cur, total=total, message=msg)

    def start_sync(self, trigger: str = "manual", dry_run: bool = False, reprocess: list[str] | None = None,
                   mirror: bool = True) -> bool:
        if not self.job_lock.acquire(blocking=False):
            return False
        self.cancel.clear()
        self.activity = {"running": True, "trigger": trigger, "dry_run": dry_run, "phase": "starting",
                         "current": 0, "total": 0, "message": "Starting", "started_at": now()}

        def work():
            try:
                engine = SyncEngine(self.db, self.cfg, make_connector(self.cfg), make_provider(self.cfg.provider),
                                    self._progress, self.cancel)
                report = engine.run(dry_run=dry_run, trigger=trigger, reprocess=reprocess, mirror=mirror)
                self.last_report = report.to_dict()
            except Exception as e:  # pragma: no cover - engine already catches; belt and braces
                log.exception("sync job crashed")
                self.last_report = {"status": "failed", "errors": [{"note": "sync", "message": str(e), "kind": "error"}]}
            finally:
                self.activity = {"running": False, "finished_at": now()}
                self.job_lock.release()
                self._schedule_next()

        self.thread = threading.Thread(target=work, name="kb-sync", daemon=True)
        self.thread.start()
        return True

    def wait(self, timeout: float = 120) -> None:
        if self.thread:
            self.thread.join(timeout)

    def rebuild(self) -> dict:
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("a sync is running")
        try:
            return render_all(self.db, self.cfg).__dict__
        finally:
            self.job_lock.release()

    # ------------------------------------------------------------------ scheduler
    def _schedule_next(self, initial: bool = False) -> None:
        if not self.cfg.automation_enabled:
            self.next_run_at = None
            return
        delay = timedelta(seconds=30) if initial else timedelta(minutes=self.cfg.sync_interval_minutes)
        self.next_run_at = datetime.now(timezone.utc) + delay

    def set_automation(self, enabled: bool) -> None:
        self.cfg.automation_enabled = enabled
        save_config(self.cfg)
        self._schedule_next(initial=enabled)

    def _scheduler_loop(self) -> None:
        while not self.stop.wait(5):
            try:
                if self.next_run_at and datetime.now(timezone.utc) >= self.next_run_at and not self.cfg.problems():
                    if not self.start_sync("schedule"):
                        self.next_run_at = datetime.now(timezone.utc) + timedelta(minutes=1)
            except Exception:  # pragma: no cover
                log.exception("scheduler error")

    def shutdown(self) -> None:
        self.stop.set()
        self.cancel.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(30)
        self.db.close()

    # ------------------------------------------------------------------ status
    def connector_health(self, force: bool = False) -> tuple[bool, str]:
        if self._health_cache and not force and time.time() - self._health_cache[0] < 60:
            return self._health_cache[1]
        try:
            res = make_connector(self.cfg).health()
        except Exception as e:
            res = (False, str(e))
        self._health_cache = (time.time(), res)
        return res

    def provider_status(self) -> dict:
        p = self.cfg.provider
        key_needed = p.name in CLOUD_PROVIDERS
        return {"name": p.name, "model": p.resolved_model(), "cloud": p.is_cloud(), "free": p.name in FREE_PROVIDERS,
                "key_set": bool(get_secret(p.name)) if key_needed else None,
                "key_storage": storage_backend(),
                "ready": p.name in ("none", "mock", "ollama") or bool(get_secret(p.name))}

    def status(self) -> dict:
        db = self.db
        v = self.cfg.vault()
        ok, msg = self.connector_health()
        last_ok = db.one("SELECT finished_at, status FROM sync_runs WHERE dry_run=0 AND status IN ('ok','partial') "
                         "ORDER BY id DESC LIMIT 1")
        return {
            "version": __version__,
            "connector": {"ok": ok, "message": msg, "kind": self.cfg.source.kind,
                          "folder": self.cfg.source.folder or self.cfg.source.folder_path},
            "vault": {"ok": bool(v and v.is_dir()), "path": str(v) if v else None,
                      "mirror": self.cfg.mirror_dir, "generated": self.cfg.generated_dir},
            "provider": self.provider_status(),
            "problems": self.cfg.problems(),
            "counts": {
                "notes": db.scalar("SELECT COUNT(*) FROM notes WHERE status='active'"),
                "notes_total": db.scalar("SELECT COUNT(*) FROM notes"),
                "entities": db.scalar("SELECT COUNT(*) FROM entities WHERE status='active' AND type NOT IN (?,?)",
                                      (NOTE_TYPE, COLLECTION_TYPE)),
                "relationships": db.scalar("SELECT COUNT(*) FROM relationships WHERE status='accepted'"),
                "reviews": db.scalar("SELECT COUNT(*) FROM reviews WHERE status='pending'"),
                "conflicts": db.scalar("SELECT COUNT(*) FROM conflicts WHERE status='open'"),
                "errors": db.scalar("SELECT COUNT(*) FROM notes WHERE last_error IS NOT NULL"),
            },
            "last_sync": dict(last_ok) if last_ok else None,
            "activity": self.activity,
            "last_report": self.last_report,
            "automation": {"enabled": self.cfg.automation_enabled, "interval_minutes": self.cfg.sync_interval_minutes,
                           "next_run_at": self.next_run_at.isoformat(timespec="seconds") if self.next_run_at else None,
                           "scheduler_alive": self.scheduler.is_alive()},
        }

    # ------------------------------------------------------------------ diagnostics
    def integrity_report(self) -> dict:
        v = self.cfg.vault()
        out = {"ok": 0, "modified": [], "missing": []}
        if not v:
            return out
        for r in self.db.q("SELECT id, title, mirror_path, mirror_hash, status FROM notes WHERE mirror_path IS NOT NULL"):
            h = file_sha256(v / r["mirror_path"])
            if h == r["mirror_hash"]:
                out["ok"] += 1
            elif h is None:
                out["missing"].append({"id": r["id"], "title": r["title"], "path": r["mirror_path"]})
            else:
                out["modified"].append({"id": r["id"], "title": r["title"], "path": r["mirror_path"],
                                        "status": r["status"]})
        return out

    def diagnostics(self) -> dict:
        ok, msg = self.connector_health(force=True)
        log_file = self.data / "logs" / "knowledge-bridge.log"
        lines = log_file.read_text("utf-8", errors="replace").splitlines()[-200:] if log_file.exists() else []
        return {
            "connector": {"ok": ok, "message": msg},
            "provider": self.provider_status(),
            "integrity": self.integrity_report(),
            "database": {"path": str(self.db.path), "problems": self.db.integrity_check(),
                         "pending_writes": self.db.scalar("SELECT COUNT(*) FROM pending_writes"),
                         "schema_version": self.db.scalar("SELECT value FROM meta WHERE key='schema_version'")},
            "data_dir": str(self.data),
            "log": lines,
        }

    # ------------------------------------------------------------------ conflicts
    def resolve_conflict(self, conflict_id: int, action: str) -> None:
        c = self.db.one("SELECT * FROM conflicts WHERE id=? AND status='open'", (conflict_id,))
        if not c:
            raise LookupError("conflict not found or already resolved")
        if action not in ("use_apple_notes", "keep_obsidian"):
            raise ValueError("action must be use_apple_notes or keep_obsidian")
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("a sync is running")
        try:
            v = self.cfg.vault()
            if action == "use_apple_notes":
                self._backup_mirror(c["mirror_path"])
                engine = SyncEngine(self.db, self.cfg, make_connector(self.cfg), None)
                h = engine.write_mirror(c["note_id"], c["mirror_path"], c["incoming_text"])
                with self.db.tx() as tx:
                    tx.execute("UPDATE notes SET mirror_hash=?, status='active', modified_at=NULL, updated_at=? "
                               "WHERE id=?", (h, now(), c["note_id"]))
                    tx.execute("DELETE FROM pending_writes WHERE note_id=?", (c["note_id"],))
            else:
                with self.db.tx() as tx:
                    tx.execute("UPDATE notes SET status='detached', updated_at=? WHERE id=?", (now(), c["note_id"]))
            with self.db.tx() as tx:
                tx.execute("UPDATE conflicts SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
                           (action, now(), conflict_id))
        finally:
            self.job_lock.release()

    def reattach(self, note_id: str) -> None:
        """Resume syncing a detached note; the Obsidian copy is backed up first."""
        r = self.db.one("SELECT * FROM notes WHERE id=? AND status='detached'", (note_id,))
        if not r:
            raise LookupError("note is not detached")
        self._backup_mirror(r["mirror_path"])
        h = file_sha256(self.cfg.vault() / r["mirror_path"])
        with self.db.tx() as tx:
            tx.execute("UPDATE notes SET status='active', mirror_hash=?, source_hash=NULL, modified_at=NULL "
                       "WHERE id=?", (h, note_id))

    def _backup_mirror(self, mirror_path: str) -> None:
        src = self.cfg.vault() / mirror_path
        if src.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            ScopedWriter(self.cfg.generated_root()).write_bytes(f"Logs/backups/{stamp}/{mirror_path}", src.read_bytes())

    # ------------------------------------------------------------------ provider test
    def test_provider(self) -> tuple[bool, str]:
        p = make_provider(self.cfg.provider)
        if p is None:
            return True, "Rules-only mode: no AI provider is called; only already-known entities are recognised."
        return p.test()

    def list_source_folders(self) -> list[dict]:
        if self.cfg.source.kind == "folder":
            return [f.model_dump() for f in make_connector(self.cfg).list_folders()]
        return [f.model_dump() for f in AppleNotesConnector("", "").list_folders()]
