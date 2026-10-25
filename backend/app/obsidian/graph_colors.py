"""Colour the Graph View by kind of node: your notes, books, podcasts, events.

Obsidian stores Graph View colour groups in <vault>/.obsidian/graph.json under
"colorGroups". Only the groups Knowledge Bridge owns (matched by their query) are
replaced; your other graph settings and colour groups are left as they are.
Obsidian reads this file when Graph View opens; reopen Graph View to see new colours.
"""
from __future__ import annotations

import json

from ..config import AppConfig
from ..entities.types import ENTITY_TYPES, enabled_types
from .writer import atomic_write_text

# 0xRRGGBB
NOTE_COLOR = 0x5B8DEF  # blue: your notes from Apple Notes
TYPE_COLORS = {
    "book": 0xF2A33A,  # amber
    "podcast_show": 0xA463F2,  # purple
    "podcast_episode": 0xC79BFA,  # light purple
    "event_series": 0x2FB57C,  # green
    "event": 0x7FD6AE,  # light green
    "person": 0xE5677B,  # rose
    "topic": 0x8C9AAE,  # slate
}


def desired_groups(cfg: AppConfig) -> list[dict]:
    groups = [{"query": f'path:"{cfg.mirror_dir}/"', "color": {"a": 1, "rgb": NOTE_COLOR}}]
    folders_done = set()
    for t in enabled_types(cfg.intelligence.enabled_types):
        if t.folder in folders_done:
            continue
        folders_done.add(t.folder)
        groups.append({"query": f'path:"{cfg.generated_dir}/{t.folder}/"',
                       "color": {"a": 1, "rgb": TYPE_COLORS.get(t.key, 0x8C9AAE)}})
    return groups


def _ours(query: str, cfg: AppConfig) -> bool:
    return query.startswith(f'path:"{cfg.mirror_dir}/') or query.startswith(f'path:"{cfg.generated_dir}/')


def apply_graph_colors(cfg: AppConfig) -> bool:
    """Returns True when graph.json was changed."""
    vault = cfg.vault()
    if not vault or not (vault / ".obsidian").is_dir():
        return False
    path = vault / ".obsidian" / "graph.json"
    try:
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except ValueError:
        return False  # leave a file we cannot parse alone
    if not isinstance(data, dict):
        return False
    existing = [g for g in data.get("colorGroups", []) if isinstance(g, dict)]
    mine = desired_groups(cfg)
    merged = mine + [g for g in existing if not _ours(str(g.get("query", "")), cfg)]
    if existing == merged:
        return False
    data["colorGroups"] = merged
    atomic_write_text(path, json.dumps(data, indent=2))
    return True


def legend(cfg: AppConfig) -> list[dict]:
    """For the app UI: which colour means what."""
    out = [{"label": "Your notes", "color": f"#{NOTE_COLOR:06x}"}]
    seen = set()
    for t in enabled_types(cfg.intelligence.enabled_types):
        if t.folder not in seen:
            seen.add(t.folder)
            out.append({"label": ENTITY_TYPES[t.key].plural_label, "color": f"#{TYPE_COLORS.get(t.key, 0x8C9AAE):06x}"})
    return out
