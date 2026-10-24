"""API key storage.

Keys go to the macOS Keychain (via `keyring`), never to the repository, the
vault or config.json. Environment variables (OPENAI_API_KEY, XAI_API_KEY) are
honoured as an override (also GEMINI_API_KEY, GROQ_API_KEY). On systems without a keyring backend (Linux CI), a
0600-permission file in the data directory is used as a fallback.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..config import data_dir

log = logging.getLogger(__name__)
SERVICE = "Knowledge Bridge"
ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "xai": "XAI_API_KEY"}


def _keyring():
    if os.environ.get("KB_DISABLE_KEYRING"):
        return None
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        kr = keyring.get_keyring()
        if isinstance(kr, FailKeyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__:
            return None
        return keyring
    except Exception:  # pragma: no cover - backend import problems
        return None


def _fallback_file() -> Path:
    return data_dir() / "secrets.json"


def _read_fallback() -> dict:
    p = _fallback_file()
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def _write_fallback(d: dict) -> None:
    p = _fallback_file()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)


def storage_backend() -> str:
    return "macOS Keychain" if _keyring() else "local file (0600) — no system keychain available"


def get_secret(provider: str) -> str | None:
    env = ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env]
    kr = _keyring()
    if kr:
        try:
            return kr.get_password(SERVICE, provider)
        except Exception as e:  # pragma: no cover
            log.warning("keychain read failed: %s", e)
            return None
    return _read_fallback().get(provider)


def set_secret(provider: str, value: str | None) -> None:
    kr = _keyring()
    if kr:
        if value:
            kr.set_password(SERVICE, provider, value)
        else:
            try:
                kr.delete_password(SERVICE, provider)
            except Exception:
                pass
        return
    d = _read_fallback()
    if value:
        d[provider] = value
    else:
        d.pop(provider, None)
    _write_fallback(d)
