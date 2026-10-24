"""Folder connector: reads notes from a directory of files.

Used for automated tests (fixtures) and as a fallback when Notes.app cannot be
scripted: export notes as HTML/Markdown/text into a folder and point Knowledge
Bridge at it. The connector only reads.

A note's identity is its path relative to the folder unless a sidecar
`<name>.meta.json` provides {"id": ..., "title": ..., "created_at": ..., "modified_at": ...};
the sidecar lets fixtures simulate renames (same id, new title/filename).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import Connector, ConnectorError, FolderInfo, NoteSummary, SourceNote

FORMATS = {".html": "html", ".htm": "html", ".md": "markdown", ".txt": "text"}


class FolderConnector(Connector):
    name = "folder"

    def __init__(self, path: str | Path):
        self.root = Path(path).expanduser()

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            raise ConnectorError(f"Source folder not found: {self.root}")
        return sorted(p for p in self.root.rglob("*") if p.is_file() and p.suffix.lower() in FORMATS
                      and not p.name.startswith("."))

    def _summary(self, p: Path) -> tuple[NoteSummary, dict]:
        meta_path = p.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
        st = p.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
        rel = p.relative_to(self.root)
        return NoteSummary(
            id=meta.get("id") or f"file:{rel.as_posix()}",
            title=meta.get("title") or p.stem,
            folder=rel.parent.as_posix() if rel.parent != Path(".") else "",
            modified_at=meta.get("modified_at") or mtime,
            created_at=meta.get("created_at") or mtime,
        ), meta

    def health(self) -> tuple[bool, str]:
        try:
            n = len(self._files())
            return True, f"Reading {n} files from {self.root}"
        except ConnectorError as e:
            return False, str(e)

    def list_folders(self) -> list[FolderInfo]:
        return [FolderInfo(account="Folder", path=str(self.root), note_count=len(self._files()))]

    def list_notes(self) -> list[NoteSummary]:
        return [self._summary(p)[0] for p in self._files()]

    def fetch(self, ids: list[str]) -> list[SourceNote]:
        wanted = set(ids)
        out = []
        for p in self._files():
            s, meta = self._summary(p)
            if s.id in wanted:
                out.append(SourceNote(**s.model_dump(), body=p.read_text("utf-8"),
                                      body_format=FORMATS[p.suffix.lower()],
                                      locked=bool(meta.get("locked")),
                                      attachments=meta.get("attachments", [])))
        return out
