"""Deterministic name normalisation and matching helpers."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_LEADING = re.compile(r"^(the|a|an)\s+")
_PUNCT = re.compile(r"[^\w\s]")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = " ".join(s.split())
    return _LEADING.sub("", s)


def short_title(s: str) -> str:
    """'Finding Flow: The Psychology of Engagement' -> 'Finding Flow'."""
    for sep in (":", " — ", " – ", " - ", "("):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if len(head) >= 3:
                return head
    return s


def keys_for(name: str) -> set[str]:
    keys = {norm(name), norm(short_title(name))}
    return {k for k in keys if k}


def similarity(a: str, b: str) -> float:
    """Similarity of two normalised names in [0, 1].

    A shorter name that is a whole-word prefix of a longer one scores 0.9:
    likely related but not certain ("Finding Flow" vs "Finding Flow Again"),
    so it goes to review. Real subtitles ("Title: Subtitle") already match
    exactly through the short-title key in keys_for()."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    if long_.startswith(short + " ") and len(short.split()) >= 2:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def slug(s: str, max_len: int = 40) -> str:
    s = norm(s).replace(" ", "-")
    return s[:max_len].strip("-") or "item"


_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def safe_filename(title: str, max_len: int = 120) -> str:
    """A filename (without extension) that works on macOS and inside Obsidian links."""
    t = _ILLEGAL_FILENAME.sub(" ", title)
    t = " ".join(t.split()).strip(" .")
    return (t[:max_len].rstrip(" .")) or "Untitled"


def find_excerpt(text: str, needle: str, width: int = 240) -> str | None:
    """Return the sentence in `text` containing `needle` (case/whitespace-insensitive), or None."""
    if not needle:
        return None
    flat = " ".join(text.split())
    i = flat.casefold().find(" ".join(needle.split()).casefold())
    if i < 0:
        return None
    start = max(flat.rfind(". ", 0, i), flat.rfind("\n", 0, i), flat.rfind("? ", 0, i), flat.rfind("! ", 0, i))
    start = 0 if start < 0 else start + 2
    end_candidates = [p for p in (flat.find(". ", i), flat.find("? ", i), flat.find("! ", i)) if p >= 0]
    end = min(end_candidates) + 1 if end_candidates else len(flat)
    excerpt = flat[start:end].strip()
    if len(excerpt) > width:
        lo = max(0, i - start - width // 2)
        excerpt = ("…" if lo else "") + excerpt[lo:lo + width].strip() + "…"
    return excerpt


def contains_text(text: str, needle: str) -> bool:
    return find_excerpt(text, needle) is not None
