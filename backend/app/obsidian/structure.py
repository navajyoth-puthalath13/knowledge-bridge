"""Create the vault folder layout up front, so it is visible in Obsidian before the first sync.

    <vault>/
      Apple Notes/<your Notes folder>/   plain copies of your notes
      Knowledge Bridge/
        Books/  Podcasts/  Events/       one page (graph node) per main topic

Only folders are created here; pages come from the renderer. Nothing existing is touched.
"""
from __future__ import annotations

from ..config import AppConfig
from ..entities.types import enabled_types
from .writer import ScopedWriter


def generated_folders(cfg: AppConfig) -> list[str]:
    """One folder per enabled main-topic type, e.g. Books, Podcasts, Events."""
    return sorted({t.folder for t in enabled_types(cfg.intelligence.enabled_types)})


def ensure_structure(cfg: AppConfig) -> list[str]:
    """Create any missing folders. Returns the vault-relative folders that were created."""
    created = []
    cfg.mirror_root().mkdir(parents=True, exist_ok=True)
    gen = ScopedWriter(cfg.generated_root())  # confined to the generated folder
    for rel in generated_folders(cfg):
        path = gen.resolve(rel)
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(f"{cfg.generated_dir}/{rel}")
    return created
