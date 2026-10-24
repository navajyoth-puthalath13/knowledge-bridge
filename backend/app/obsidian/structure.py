"""Create the vault folder layout up front, so it is visible in Obsidian before the first sync.

    <vault>/
      Apple Notes/                      faithful copies of your notes
      Knowledge Bridge/
        Collections/                    Books, Podcasts, Personal Thinking, Design Events
        Entities/
          Books/  Podcast Shows/  Podcast Episodes/  People/  Events/  Topics/  Personal Thinking/
        Index/                          entities.json, relationships.json
        Logs/                           sync history and backups

Only folders are created here; pages come from the renderer. Nothing existing is touched.
"""
from __future__ import annotations

from ..config import AppConfig
from ..entities.types import ENTITY_TYPES
from .writer import ScopedWriter


def generated_folders() -> list[str]:
    entity_folders = sorted({t.folder for t in ENTITY_TYPES.values()} | {"Personal Thinking"})
    return ["Collections", "Index", "Logs"] + [f"Entities/{f}" for f in entity_folders]


def ensure_structure(cfg: AppConfig) -> list[str]:
    """Create any missing folders. Returns the vault-relative folders that were created."""
    created = []
    cfg.mirror_root().mkdir(parents=True, exist_ok=True)
    gen = ScopedWriter(cfg.generated_root())  # confined to the generated folder
    for rel in generated_folders():
        path = gen.resolve(rel)
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(f"{cfg.generated_dir}/{rel}")
    return created
