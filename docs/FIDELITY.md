# Content fidelity

The mirror body is a deterministic conversion of the HTML that Notes.app exposes through scripting. Text and paragraph order are always preserved exactly. Formatting is preserved where Markdown (or inline HTML Obsidian renders) can express it.

| Apple Notes feature | Mirror | Notes |
| --- | --- | --- |
| Folders | Same structure as Apple Notes: `Apple Notes/<Notes folder>/<subfolder>/<note>.md` | Subfolders need *Include subfolders* |
| Title (first line) | Kept as the first line of the body (usually `# Title`) | Also used as the file name |
| Paragraphs / line breaks | Preserved line by line | Several consecutive empty lines collapse to one (Markdown renders them the same) |
| Title / Heading / Subheading | `#`, `##`, `###` | |
| Bold, italic | `**…**`, `*…*` | |
| Underline | `<u>…</u>` | Markdown has no underline; Obsidian renders the HTML tag |
| Strikethrough | `~~…~~` | |
| Monospaced | `` `…` `` | |
| Bulleted / dashed / numbered lists | `-` / `1.` lists, including nesting | |
| Checklists | Plain bullet list | **Tick state is not exposed** by Notes' scripting interface |
| Links | `[text](url)` | `applenotes:` note links are kept as-is |
| Tables | Markdown pipe tables; first row as header | Multi-line cells are flattened |
| Inline images (pasted) | Extracted to `Apple Notes/_attachments/<id>/` and embedded with `![[…]]` | When Notes provides them inline as data URIs |
| Other attachments (PDF, files, audio) | Copied to `_attachments/` when Notes can export them; listed in frontmatter `attachments:` | Position inside the note is not exposed for these, so they are **listed, not placed inline**. Failures are reported per attachment and never block the note. |
| Sketches / drawings, scanned documents, maps, link previews | Usually not exported | Shown as an attachment-export warning in the sync history |
| Block quotes, colours, fonts, highlights | Text kept, styling dropped | |
| Password-protected notes | **Skipped**, never mirrored | Status `locked` |
| Tags (`#tag`) | Kept as text (Obsidian treats them as tags) | |

## Frontmatter

Each mirror starts with deterministic sync metadata, written by code and never by the AI:

```yaml
---
apple_notes_id: "x-coredata://…/ICNote/p123"
created: "…"
modified: "…"
source_folder: "Knowledge"     # the Notes folder the note lives in
attachments:                   # only when present
  - {"name": "slides.pdf", "file": "Apple Notes/_attachments/…/0-slides.pdf"}
kb_mirror: true
---
```

To get bodies with no frontmatter at all, set `"mirror_frontmatter": false` in `config.json`.

## Verifying

- Diagnostics → *Source integrity* compares every mirror with the checksum recorded when it was written.
- `scripts/validate-apple-notes.sh "<folder>"` writes each note's raw HTML next to its mirror for side-by-side comparison.
