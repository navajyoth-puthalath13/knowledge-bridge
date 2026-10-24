"""Filesystem writes confined to one root directory.

Every component that writes to the vault gets a `ScopedWriter` bound to the
only directory it is allowed to touch. The intelligence/rendering layer is
bound to the generated folder, so it cannot write into the source mirror even
by accident (path traversal, symlinks and absolute paths are rejected).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class WriteOutsideRootError(PermissionError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def file_sha256(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError:
        return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kb-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


class ScopedWriter:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, rel_or_abs: str | Path) -> Path:
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = self.root / p
        # resolve() follows symlinks, so a symlink pointing outside is caught too
        real = p.resolve()
        if real != self.root and self.root not in real.parents:
            raise WriteOutsideRootError(f"refusing to write outside {self.root}: {p}")
        return real

    def write_text(self, rel: str | Path, text: str) -> tuple[Path, str]:
        path = self.resolve(rel)
        data = text.encode("utf-8")
        atomic_write_bytes(path, data)
        return path, sha256_bytes(data)

    def write_bytes(self, rel: str | Path, data: bytes) -> tuple[Path, str]:
        path = self.resolve(rel)
        atomic_write_bytes(path, data)
        return path, sha256_bytes(data)

    def move(self, src: str | Path, dst: str | Path) -> Path:
        s, d = self.resolve(src), self.resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            raise FileExistsError(d)
        os.replace(s, d)
        return d

    def delete(self, rel: str | Path) -> None:
        path = self.resolve(rel)
        path.unlink(missing_ok=True)
