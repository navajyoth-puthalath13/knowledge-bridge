"""Validate Apple Notes access and conversion fidelity on this Mac (read-only).

    python -m app.tools.validate_apple_notes "Knowledge Bridge Test" [account]

Writes a report folder with, for every note, the raw HTML that Notes.app
returned and the Markdown the mirror would contain, plus report.md listing
detected rich-text features and anything that could not be preserved.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from ..config import data_dir
from ..connectors.base import Connector, ConnectorError
from ..intelligence.normalize import safe_filename
from ..obsidian.mirror import render_mirror

FEATURES = {
    "headings": r"<h[1-6]\b",
    "bulleted lists": r"<ul\b",
    "numbered lists": r"<ol\b",
    "tables": r"<table\b",
    "links": r"<a\s[^>]*href=",
    "inline images (data URI)": r"<img\s[^>]*src=\"data:",
    "other images": r"<img\s(?![^>]*src=\"data:)",
    "bold": r"<b\b|<strong\b",
    "italic": r"<i\b|<em\b",
    "underline": r"<u\b",
    "strikethrough": r"<strike\b|<s\b|<del\b",
    "monospace": r"<tt\b|<pre\b|<code\b",
    "checklists (state not exposed)": r"checklist|<input[^>]*checkbox",
}


def validate(conn: Connector, out: Path) -> tuple[bool, str]:
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# Apple Notes validation — {datetime.now():%Y-%m-%d %H:%M}", ""]
    ok, msg = conn.health()
    lines += [f"- Connection: {'OK' if ok else 'FAILED'} — {msg}"]
    if not ok:
        (out / "report.md").write_text("\n".join(lines) + "\n", "utf-8")
        return False, msg
    first = conn.list_notes()
    second = conn.list_notes()
    stable = [n.id for n in first] == [n.id for n in second]
    lines += [f"- Notes in folder: {len(first)}", f"- Note ids stable between listings: {'yes' if stable else 'NO'}", ""]
    if not first:
        lines.append("The folder is empty. Add a few test notes (see docs/APPLE_NOTES.md) and run again.")
    notes = conn.fetch([n.id for n in first])
    totals: dict[str, int] = {}
    lines += ["| Note | Locked | Features | Attachments | Warnings |", "| --- | --- | --- | --- | --- |"]
    used: set[str] = set()
    for n in notes:
        stem = safe_filename(n.title)[:60]
        base, i = stem, 2
        while stem.casefold() in used:  # notes with the same title must not overwrite each other
            stem = f"{base} ({i})"
            i += 1
        used.add(stem.casefold())
        feats = [k for k, rx in FEATURES.items() if re.search(rx, n.body or "", re.I)]
        for f in feats:
            totals[f] = totals.get(f, 0) + 1
        render = render_mirror(n, "Apple Notes")
        (out / f"{stem}.source.html").write_text(n.body or "", "utf-8")
        (out / f"{stem}.mirror.md").write_text(render.text, "utf-8")
        atts = f"{sum(1 for a in n.attachments if a.data_b64)}/{len(n.attachments)} exported" if n.attachments else "—"
        lines.append(f"| {n.title} | {'yes' if n.locked else ''} | {', '.join(feats) or '—'} | {atts} | "
                     f"{'; '.join(render.warnings) or '—'} |")
    lines += ["", "## Feature coverage", ""]
    lines += [f"- {k}: {v} note(s)" for k, v in sorted(totals.items())] or ["- none detected"]
    lines += ["", "Compare each `*.source.html` with its `*.mirror.md`. Text and order must match exactly.",
              "Checklist tick state, drawings, scanned documents and some attachment types are not exposed by",
              "Notes' scripting interface; see docs/FIDELITY.md."]
    (out / "report.md").write_text("\n".join(lines) + "\n", "utf-8")
    return True, f"{len(notes)} notes checked"


def main(argv: list[str]) -> int:
    from ..connectors.apple_notes import AppleNotesConnector

    if len(argv) < 1:
        print(__doc__)
        conn = AppleNotesConnector("", "")
        try:
            print("Folders:")
            for f in conn.list_folders():
                print(f"  {f.account} › {f.path} ({f.note_count})")
        except ConnectorError as e:
            print("Error:", e)
            return 1
        return 0
    folder, account = argv[0], (argv[1] if len(argv) > 1 else "")
    out = data_dir() / f"validation-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        ok, msg = validate(AppleNotesConnector(account, folder), out)
    except ConnectorError as e:
        print("Error:", e)
        return 1
    print(("✓ " if ok else "✗ ") + msg)
    print("Report:", out / "report.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
