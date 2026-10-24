"""Application configuration.

Configuration, the SQLite database and logs live in a per-user data directory
outside the repository and outside the Obsidian vault:

  macOS:  ~/Library/Application Support/Knowledge Bridge
  other:  ~/.config/knowledge-bridge

`KB_DATA_DIR` overrides the location (used by tests).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .obsidian.writer import atomic_write_text


def data_dir() -> Path:
    override = os.environ.get("KB_DATA_DIR")
    if override:
        p = Path(override).expanduser()
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "Knowledge Bridge"
    else:
        p = Path.home() / ".config" / "knowledge-bridge"
    p.mkdir(parents=True, exist_ok=True)
    return p


ProviderName = Literal["none", "ollama", "gemini", "groq", "openai", "xai", "mock"]
# Cloud providers need an API key. gemini and groq have free tiers; openai and xai are paid.
CLOUD_PROVIDERS = ("gemini", "groq", "openai", "xai")
FREE_PROVIDERS = ("none", "ollama", "gemini", "groq")


class ProviderConfig(BaseModel):
    # "none" = rules-only (no note content leaves the Mac, but only already-known
    # entities are recognised). "ollama" = local model, also fully local.
    name: ProviderName = "none"
    model: str = ""
    base_url: str = ""  # optional override, e.g. a different Ollama host
    temperature: float = 0.0
    timeout_seconds: float = 90.0

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return {"ollama": "llama3.1:8b", "gemini": "gemini-2.5-flash", "groq": "llama-3.3-70b-versatile",
                "openai": "gpt-4.1-mini", "xai": "grok-3-mini", "mock": "mock-1"}.get(self.name, "")

    def is_cloud(self) -> bool:
        return self.name in CLOUD_PROVIDERS


class SourceConfig(BaseModel):
    # "apple_notes" reads Notes.app through Apple's scripting interface (JXA).
    # "folder" reads .html/.md/.txt files from a directory (exports, fixtures).
    kind: Literal["apple_notes", "folder"] = "apple_notes"
    account: str = ""  # Apple Notes account name ("iCloud", "On My Mac"); empty = any
    folder: str = ""  # Apple Notes folder path, e.g. "Knowledge" or "Knowledge/Podcasts"
    include_subfolders: bool = False
    folder_path: str = ""  # for kind == "folder"
    export_attachments: bool = True


class IntelligenceConfig(BaseModel):
    auto_accept_confidence: float = 0.8  # entities/relations at or above this are accepted
    fuzzy_auto_merge: float = 0.93  # name similarity for automatic alias merge
    fuzzy_review: float = 0.80  # similarity range [review, auto_merge) goes to review
    semantic_suggestions: bool = True
    semantic_auto_accept: bool = False  # semantic links always go to review by default
    enabled_types: dict[str, bool] = Field(default_factory=dict)  # overrides per type key


class AppConfig(BaseModel):
    vault_path: str = ""
    mirror_dir: str = "Apple Notes"  # relative to vault
    generated_dir: str = "Knowledge Bridge"  # relative to vault
    obsidian_vault_name: str = ""  # for obsidian:// links; defaults to vault folder name
    source: SourceConfig = Field(default_factory=SourceConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    intelligence: IntelligenceConfig = Field(default_factory=IntelligenceConfig)
    sync_interval_minutes: int = 15
    automation_enabled: bool = False
    mirror_frontmatter: bool = True  # deterministic sync metadata (never AI output)

    @field_validator("mirror_dir", "generated_dir")
    @classmethod
    def _relative(cls, v: str) -> str:
        v = v.strip().strip("/")
        if not v or ".." in Path(v).parts or Path(v).is_absolute():
            raise ValueError("must be a non-empty path relative to the vault, without '..'")
        return v

    @field_validator("sync_interval_minutes")
    @classmethod
    def _interval(cls, v: int) -> int:
        if v < 1 or v > 24 * 60:
            raise ValueError("sync interval must be between 1 and 1440 minutes")
        return v

    # ---- derived paths -------------------------------------------------
    def vault(self) -> Path | None:
        return Path(self.vault_path).expanduser().resolve() if self.vault_path else None

    def mirror_root(self) -> Path:
        v = self.vault()
        assert v is not None, "vault not configured"
        return v / self.mirror_dir

    def generated_root(self) -> Path:
        v = self.vault()
        assert v is not None, "vault not configured"
        return v / self.generated_dir

    def vault_name(self) -> str:
        if self.obsidian_vault_name:
            return self.obsidian_vault_name
        v = self.vault()
        return v.name if v else ""

    def problems(self) -> list[str]:
        """Configuration errors that block syncing."""
        out: list[str] = []
        v = self.vault()
        if not v:
            out.append("Obsidian vault is not selected.")
        elif not v.is_dir():
            out.append(f"Obsidian vault folder does not exist: {v}")
        else:
            m, g = Path(self.mirror_dir).parts, Path(self.generated_dir).parts
            if m[: len(g)] == g or g[: len(m)] == m:
                out.append("Source-mirror and generated folders must not contain each other.")
        if self.source.kind == "apple_notes" and not self.source.folder:
            out.append("Apple Notes folder is not selected.")
        if self.source.kind == "folder" and not self.source.folder_path:
            out.append("Source folder path is not set.")
        return out


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> AppConfig:
    p = config_path()
    if p.exists():
        return AppConfig.model_validate_json(p.read_text("utf-8"))
    return AppConfig()


def save_config(cfg: AppConfig) -> None:
    atomic_write_text(config_path(), json.dumps(cfg.model_dump(), indent=2))
