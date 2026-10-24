"""Apple Notes connector using Apple's supported scripting interface.

Why JXA via `osascript` (see docs/APPLE_NOTES.md for the full evaluation):
- It is Apple's supported automation interface for Notes.app and exposes the
  stable note id (x-coredata://…/ICNote/p123), title, HTML body, dates,
  password-protection flag and attachments.
- Bulk property reads make listing a folder cheap, so incremental sync only
  fetches bodies for notes whose modification date changed.
- Reading the NoteStore.sqlite database directly is undocumented, needs Full
  Disk Access and breaks between macOS versions; Shortcuts cannot be driven
  headlessly with structured output as reliably.

The first call triggers macOS's Automation permission prompt
("Knowledge Bridge wants to control Notes"). Nothing in this module writes to
Notes: the script only reads properties.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Connector, ConnectorError, FolderInfo, NoteSummary, SourceNote

SCRIPT = Path(__file__).with_name("notes.jxa.js")
FETCH_BATCH = 20


class AppleNotesConnector(Connector):
    name = "apple_notes"

    def __init__(self, account: str, folder: str, include_subfolders: bool = False,
                 export_attachments: bool = True, timeout: float = 300):
        self.account = account
        self.folder = folder
        self.include_subfolders = include_subfolders
        self.export_attachments = export_attachments
        self.timeout = timeout

    def _run(self, req: dict, timeout: float | None = None):
        if sys.platform != "darwin":
            raise ConnectorError("Apple Notes is only available on macOS.")
        if not shutil.which("osascript"):
            raise ConnectorError("osascript not found.")
        try:
            proc = subprocess.run(
                ["osascript", "-l", "JavaScript", str(SCRIPT), json.dumps(req)],
                capture_output=True, text=True, timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise ConnectorError("Apple Notes did not respond in time.") from e
        if proc.returncode != 0:
            err = proc.stderr.strip()
            if "-1743" in err or "Not authorized" in err:
                raise ConnectorError(
                    "macOS blocked access to Notes. Allow it in System Settings → Privacy & Security → "
                    "Automation → Knowledge Bridge (or Terminal) → Notes."
                )
            raise ConnectorError(f"Apple Notes script failed: {err or 'unknown error'}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ConnectorError("Apple Notes returned unreadable output.") from e

    def health(self) -> tuple[bool, str]:
        try:
            res = self._run({"op": "ping"}, timeout=30)
            return True, f"Connected to Notes ({', '.join(res.get('accounts', [])) or 'no accounts'})"
        except ConnectorError as e:
            return False, str(e)

    def list_folders(self) -> list[FolderInfo]:
        return [FolderInfo(**f) for f in self._run({"op": "folders"}, timeout=120)]

    def list_notes(self) -> list[NoteSummary]:
        req = {"op": "list", "account": self.account, "folder": self.folder,
               "include_subfolders": self.include_subfolders}
        return [NoteSummary(**n) for n in self._run(req)]

    def fetch(self, ids: list[str]) -> list[SourceNote]:
        out: list[SourceNote] = []
        for i in range(0, len(ids), FETCH_BATCH):
            batch = ids[i:i + FETCH_BATCH]
            with tempfile.TemporaryDirectory(prefix="kb-att-") as tmp:
                req = {"op": "fetch", "ids": batch, "attachments": self.export_attachments,
                       "attachment_dir": tmp if self.export_attachments else ""}
                for rec in self._run(req):
                    if rec.get("error"):
                        raise ConnectorError(f"Could not read note {rec.get('title') or rec['id']}: {rec['error']}")
                    out.append(SourceNote(**rec))
        return out
