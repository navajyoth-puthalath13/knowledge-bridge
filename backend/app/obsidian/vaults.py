"""The vaults Obsidian itself knows about.

Obsidian keeps its vault list in ~/Library/Application Support/obsidian/obsidian.json
({"vaults": {"<id>": {"path": "...", "ts": ..., "open": true}}}). Reading it lets Setup offer
the user's real vaults and warn when the chosen folder is not one Obsidian has opened.
Read-only; the file is never modified.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def obsidian_config_path() -> Path:
    override = os.environ.get("KB_OBSIDIAN_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    return Path.home() / ".config" / "obsidian" / "obsidian.json"


def known_vaults() -> list[dict] | None:
    """[{name, path, open}] sorted with the currently open vault first; None if Obsidian's list is unavailable."""
    p = obsidian_config_path()
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    out = []
    for v in (data.get("vaults") or {}).values():
        path = v.get("path")
        if path and Path(path).is_dir():
            out.append({"name": Path(path).name, "path": str(Path(path)), "open": bool(v.get("open")),
                        "ts": v.get("ts") or 0})
    out.sort(key=lambda v: (not v["open"], -v["ts"]))
    for v in out:
        v.pop("ts")
    return out


def is_known(vault: Path) -> bool | None:
    vaults = known_vaults()
    if vaults is None:
        return None
    target = vault.resolve()
    return any(Path(v["path"]).resolve() == target for v in vaults)
