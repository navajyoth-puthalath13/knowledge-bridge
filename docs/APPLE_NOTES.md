# Apple Notes connector

## Options evaluated

| Approach | Stable note id | Rich content | Change detection | Reliability / maintenance | Verdict |
| --- | --- | --- | --- | --- | --- |
| **AppleScript / JXA via `osascript`** (Notes.app scripting dictionary) | Yes: `x-coredata://…/ICNote/p123`, survives renames | HTML body: headings, lists, tables, links, styles, many inline images | `modification date` on every note; bulk reads are cheap | Apple's supported automation interface; one permission prompt | **Chosen** |
| Shortcuts ("Find Notes", "Get details of Notes") | No reliable id exposed | Rich text / plain | Modification date | Needs a Shortcut installed and run via `shortcuts run`; output marshalling is fragile | Rejected |
| Reading `NoteStore.sqlite` directly | Yes | Gzipped protobuf blobs; formatting must be reverse-engineered | Yes | Undocumented, changes between macOS versions, needs Full Disk Access | Rejected (unsafe to maintain) |
| Manual export (File → Export as PDF / Markdown on macOS 15+) | No | Good | No | Manual | Supported as a **fallback** via the *Exported files folder* source |

JXA was chosen over plain AppleScript for the same interface plus native JSON output (`JSON.stringify`). That avoids fragile text parsing.

## How the connector works

`backend/app/connectors/notes.jxa.js` supports four read-only operations:

- `ping`: lists accounts (health check, and triggers the permission prompt).
- `folders`: walks every account's folder tree. Some macOS versions return nested folders at the top level, so the deepest path per folder id is kept. "Recently Deleted" is skipped.
- `list`: for the selected folder (and optionally its subfolders) reads `id`, `name`, `modificationDate` and `creationDate` with one Apple Event per property.
- `fetch`: for a batch of ids reads `body` (HTML), `passwordProtected` and `attachments`. With attachment export on, each attachment is saved to a temporary directory and returned base64-encoded. Failures are reported per attachment.

The script calls no `make`, `delete`, `move` or `set` commands. The sync engine fetches bodies only for notes whose title or modification date changed.

## Validate on your Mac (Phase 1)

1. In Notes, create a folder named **Knowledge Bridge Test** containing:
   - a note with an H1 title, H2 heading, bulleted and numbered lists, bold, italic, underline, strikethrough and monospace text, and a web link;
   - a note with a table;
   - a note with a pasted image and a dragged-in PDF;
   - a note with a checklist (some items ticked);
   - a password-protected note;
   - a note you then rename;
   - a note inside a subfolder.
2. Run:
   ```bash
   scripts/validate-apple-notes.sh                         # lists folders; first run asks for permission
   scripts/validate-apple-notes.sh "Knowledge Bridge Test" # writes a report folder
   ```
3. Open the printed `report.md`. For each note, compare `*.source.html` with `*.mirror.md`. Text and paragraph order must match. Check the feature-coverage list against the notes you created.
4. Run the automated integration tests against the same folder:
   ```bash
   cd backend
   KB_MACOS_TESTS=1 KB_TEST_NOTES_FOLDER="Knowledge Bridge Test" .venv/bin/python -m pytest tests/test_macos_integration.py -v
   ```
5. Rename a note in Notes and re-run the app's sync. The mirror file should be renamed, not duplicated.

If anything in the report looks wrong, the `*.source.html` file shows exactly what macOS returned. Converter fixes go in `connectors/html_to_md.py`, with a regression test in `tests/test_mirror.py`.

## Permissions

- **Automation → Notes** is required. The first run shows the prompt. If it was denied: System Settings → Privacy & Security → Automation → enable *Notes* under Knowledge Bridge (or under Terminal when running `scripts/dev.sh`).
- Full Disk Access is **not** required.
