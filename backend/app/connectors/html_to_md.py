"""Convert the HTML that Notes.app exposes into Obsidian-flavoured Markdown.

Notes.app's scripting `body` is simple HTML: one <div> per line, <div><br></div>
for empty lines, <h1>-<h3>, <b>/<i>/<u>/<strike>, <ul>/<ol>, <table>, <a>, <tt>
and (for pasted images on recent macOS) <img src="data:..."> elements.

The conversion is deterministic and lossless for text and paragraph order.
Features Markdown cannot express are preserved as inline HTML (underline) or
documented in docs/FIDELITY.md.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import MarkdownConverter

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/heic": "heic", "image/tiff": "tiff",
        "image/webp": "webp", "application/pdf": "pdf"}


@dataclass
class ConversionResult:
    markdown: str
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (filename, bytes)
    warnings: list[str] = field(default_factory=list)


class _NotesConverter(MarkdownConverter):
    def __init__(self, image_dir: str, **kw):
        super().__init__(**kw)
        self.image_dir = image_dir
        self.images: list[tuple[str, bytes]] = []
        self.warnings: list[str] = []

    def convert_u(self, el, text, parent_tags=None, **kw):  # markdown has no underline
        return f"<u>{text}</u>" if text.strip() else text

    def convert_strike(self, el, text, parent_tags=None, **kw):
        return f"~~{text}~~" if text.strip() else text

    convert_s = convert_strike
    convert_del = convert_strike

    def convert_tt(self, el, text, parent_tags=None, **kw):
        return f"`{text}`" if text.strip() else text

    def convert_img(self, el, text, parent_tags=None, **kw):
        src = el.get("src", "")
        m = re.match(r"data:([\w/+.-]+);base64,(.*)", src, re.S)
        if m:
            ctype, payload = m.group(1), m.group(2)
            try:
                data = base64.b64decode(payload)
            except Exception:
                self.warnings.append("an embedded image could not be decoded")
                return ""
            name = f"{hashlib.sha256(data).hexdigest()[:16]}.{_EXT.get(ctype, 'bin')}"
            self.images.append((name, data))
            return f"![[{self.image_dir}/{name}]]"
        if src:
            return f"![]({src})"
        self.warnings.append("an image without a source was skipped")
        return ""


def _normalise_notes_html(html: str) -> str:
    """Turn Notes' line-per-<div> structure into paragraphs markdownify understands."""
    soup = BeautifulSoup(html, "html.parser")
    for div in soup.find_all("div"):
        # A div containing only <br> is an intentionally empty line.
        children = [c for c in div.contents if not (isinstance(c, NavigableString) and not c.strip())]
        if len(children) == 1 and isinstance(children[0], Tag) and children[0].name == "br":
            div.replace_with(soup.new_tag("kb-blank"))
    return str(soup)


def notes_html_to_markdown(html: str, image_dir: str = "_attachments") -> ConversionResult:
    conv = _NotesConverter(
        image_dir=image_dir,
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
        escape_underscores=False,
        escape_asterisks=True,
        table_infer_header=True,
    )
    norm = _normalise_notes_html(html)
    # Each <div> is a hard line in Notes. Convert line by line so text order and
    # line breaks are preserved exactly; block elements are converted whole.
    soup = BeautifulSoup(norm, "html.parser")
    root = soup.body or soup
    # Notes wraps everything in <html><body> on some versions; unwrap single div wrappers.
    lines: list[str] = []
    for node in root.children:
        if isinstance(node, NavigableString):
            if node.strip():
                lines.append(conv.convert(str(node)).strip())
            continue
        if node.name == "kb-blank":
            lines.append("")
            continue
        if node.name in ("html", "body"):
            for sub in node.children:
                lines.extend(_convert_block(conv, sub))
            continue
        lines.extend(_convert_block(conv, node))
    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip("\n") + "\n"
    return ConversionResult(markdown=md, images=conv.images, warnings=conv.warnings)


def _convert_block(conv: _NotesConverter, node) -> list[str]:
    if isinstance(node, NavigableString):
        return [conv.convert(str(node)).strip()] if node.strip() else []
    if node.name == "kb-blank":
        return [""]
    if node.name == "div" and any(isinstance(c, Tag) and c.name in ("div", "ul", "ol", "table", "h1", "h2", "h3")
                                  for c in node.children):
        out: list[str] = []
        for sub in node.children:
            out.extend(_convert_block(conv, sub))
        return out
    text = conv.convert(str(node))
    if node.name in ("ul", "ol", "table", "h1", "h2", "h3", "h4", "pre", "blockquote"):
        # blocks need a blank line around them to render correctly in Markdown
        return ["", text.strip("\n"), ""]
    # a single line: strip trailing <br> artefacts, keep inner spacing
    return [text.strip("\n").rstrip()]


def plain_text_of_markdown(md: str) -> str:
    """Rough plain text used for AI analysis (never written anywhere)."""
    t = re.sub(r"!\[\[[^\]]*\]\]", "", md)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"</?u>", "", t)
    t = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~])", r"\1", t)
    t = re.sub(r"(\*\*|~~|`)", "", t)
    t = re.sub(r"(?m)^\s*#{1,6}\s+", "", t)
    t = re.sub(r"(?m)^\s*\|?\s*-{3,}.*$", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()
