"""Load API keys from a local, git-ignored `.env` file.

Looked up in the repository root (`knowledge-bridge/.env`) and in the data
folder. Values never override variables already set in the environment.
The file is listed in .gitignore and must never be committed.

    GROQ_API_KEY=gsk_...
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import data_dir

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load(paths: list[Path] | None = None) -> list[Path]:
    """Load every existing .env file; returns the files that were read."""
    loaded = []
    for p in paths or [REPO_ENV, data_dir() / ".env"]:
        if p.is_file():
            for k, v in parse(p.read_text("utf-8")).items():
                os.environ.setdefault(k, v)
            loaded.append(p)
    return loaded
