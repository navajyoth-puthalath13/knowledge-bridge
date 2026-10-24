# Architecture

```
┌──────────────── Electron app ────────────────┐        ┌────────── Python service (127.0.0.1) ──────────┐
│ Renderer (React)  ──IPC──►  Main process     │ HTTP + │ api/routes ─► service (job runner, scheduler)  │
│  no Node, sandboxed      restricted bridge,  │ token  │      │                                         │
│                          path allowlist,     │───────►│      ▼                                         │
│                          spawns + supervises │        │  sync/engine ──► connectors/ (Apple Notes JXA, │
│                          the service         │        │      │                       folder fallback)  │
└──────────────────────────────────────────────┘        │      ├─► obsidian/mirror ─► <vault>/Apple Notes │
                                                        │      ├─► intelligence/pipeline ─► providers/    │
                                                        │      │         (validate, resolve, review)      │
                                                        │      └─► obsidian/render ─► <vault>/Knowledge   │
                                                        │                               Bridge/           │
                                                        │  storage/db (SQLite registry, journal, runs)   │
                                                        └────────────────────────────────────────────────┘
```

## Components

**Electron main** (`apps/desktop/electron/`)
- Picks a free localhost port and generates a random token. It starts `backend/.venv/bin/python -m app.main` with `KB_TOKEN` in the environment.
- Health-checks the service, restarts it up to three times on a crash, and sends SIGTERM on quit, which lets uvicorn run its graceful shutdown.
- Forwards renderer requests only for an allowlisted set of API paths and methods. The renderer has no Node, runs sandboxed with context isolation, cannot navigate, and cannot open windows.
- Owns the native-only operations: folder picker, `obsidian://` open, Finder open, login item.

**Python service** (`backend/app/`)

| Module | Responsibility |
| --- | --- |
| `connectors/` | Read-only source access. `AppleNotesConnector` runs `notes.jxa.js` through `osascript` and uses bulk property reads to list notes cheaply. `FolderConnector` reads exported files and is used by the tests. |
| `connectors/html_to_md.py` | Deterministic Notes HTML → Markdown conversion. Embedded images are extracted to files. |
| `obsidian/mirror.py` | Renders the mirror file: deterministic frontmatter (source id, dates, attachments) followed by the converted body. |
| `obsidian/writer.py` | `ScopedWriter`: atomic writes (temp file, fsync, rename) confined to one root. Refuses `..`, absolute paths and symlink escapes. |
| `sync/engine.py` | Discovery, change detection, journalled mirror writes, renames, conflicts, missing notes, dry run, recovery. Then runs the intelligence phase and rendering. |
| `intelligence/pipeline.py` | Per note: verify mirror checksum, call the provider, validate output, resolve entities, store mentions and relationships with evidence, apply rules, re-verify the checksum. |
| `intelligence/registry.py` | Canonical entities, aliases, exact and fuzzy matching, merges, structural collection links, decisions. |
| `intelligence/reviews.py` | Applies review-queue decisions and records them so reprocessing never asks again. |
| `obsidian/render.py` | Rebuilds every generated page from the registry. Only changed files are written; edited pages are backed up before being overwritten. |
| `providers/` | One OpenAI-compatible client for OpenAI, xAI and Ollama, with retries and backoff, plus a deterministic mock. |
| `security/` | Token and Host-header middleware; Keychain-backed secrets. |
| `entities/types.py` | The entity type table. Adding a type is one entry here. |

## Data model (SQLite)

- `notes`: one row per source note. Holds the stable source id, title, mirror path, `source_hash` (raw content), `mirror_hash` (bytes written) and status (`active`, `missing`, `locked`, `conflict`, `detached`). Also `processed_hash` and `processed_key`, which record what the AI layer last analysed.
- `entities`: canonical nodes with a stable `id`, `type`, `name` and normalised name. Source notes are entities of type `note`, so every edge is entity → entity. Collections are entities of type `collection`.
- `aliases`: every known spelling per entity, normalised. Subtitle-stripped forms are stored too, so "Finding Flow" matches "Finding Flow: The Psychology…".
- `relationships`: `src_id`, `tgt_id`, `type` (`mentions`, `about`, `belongs_to`, `references`, `related_to`), plus `note_id` (evidence source), `label`, verbatim `evidence`, `confidence`, `status` (`accepted`, `pending`, `rejected`), `origin` (`llm`, `rule`, `semantic`, `structural`, `user`) and timestamps.
- `reviews` + `decisions`: the review queue, and remembered answers keyed by candidate, so decisions survive reprocessing.
- `pending_writes`: write-ahead journal for mirror writes.
- `conflicts`, `sync_runs`, `llm_cache`, `meta` (render manifest).

## Pipeline for a new or changed note

1. **Mirror.** List the folder (cheap). Fetch bodies only for notes whose modification date or title changed. Render the mirror. If the file on disk is not byte-identical to what was last written and the Apple Note has also changed, stop that write and record a conflict. Otherwise journal the write, write it atomically, update the row, and clear the journal entry.
2. **Read and verify.** The pipeline reads the mirror and checks its sha256 against `mirror_hash`.
3. **Extract.** The provider returns JSON. A cache keyed by prompt version, model, note text and candidates avoids repeat calls; *Reprocess* bypasses it.
4. **Validate.** Pydantic schema. Every entity must appear in the note text: its evidence, surface form or name must be found verbatim. Hallucinations are dropped and reported. Unknown or disabled types are ignored.
5. **Resolve.** Exact alias match → reuse. Fuzzy ≥ 0.93 → reuse and add the alias. Fuzzy 0.80–0.93 → *possible duplicate* review. Low confidence, flagged-ambiguous or first-name-only people → *ambiguous reference* review. Otherwise create.
6. **Relate.** `mentions` or `about` (primary subject) from the note to each entity. `belongs_to` from episode → show and event → series. Stated relations between entities are accepted only with verbatim evidence and enough confidence; otherwise they go to review. Semantic note↔note suggestions always go to review by default.
7. **Rules.** Known aliases (≥ 2 words or ≥ 6 characters) found verbatim become `rule` mentions. A note naming another note's title gets a `references` edge. Personal notes `belongs_to` Personal Thinking.
8. **Transact.** All of steps 5–7 run in one SQLite transaction. Reprocessing first removes everything that note previously contributed, so nothing duplicates.
9. **Verify** the mirror checksum again, then **render** the generated pages.

## Guarantees and how they are enforced

| Guarantee | Mechanism | Test |
| --- | --- | --- |
| Apple Notes never modified | Connector script only reads properties; no write commands exist in it | code review; `test_macos_integration.py` |
| Mirror body never contains AI output | Renderer writes only via `ScopedWriter(generated_root)` | `test_mirror_body_has_no_generated_content`, `test_scoped_writer_refuses_escape` |
| Mirror unchanged by processing | Checksum before and after each note; integrity report | `test_intelligence_and_rebuild_never_change_mirror` |
| No silent overwrite of Obsidian edits | Write only if disk bytes == last written bytes; otherwise conflict | `test_conflict_stops_write_and_keeps_both_versions` |
| No overwrite of unrelated vault files | Name collision → `Title (2).md` | `test_never_overwrites_unrelated_vault_file` |
| Interrupted writes recovered | Journal + atomic rename + recovery on start | `test_interrupted_write_is_recovered` |
| One canonical entity per thing | Alias registry + fuzzy review | `test_one_canonical_book_for_all_mentions` |
| Idempotent | Hash-based skip + per-note replacement | `test_idempotent_second_run`, `test_reprocessing_does_not_duplicate` |
| Notes vanish → entities kept | Notes marked `missing`; entities and pages remain | `test_missing_note_keeps_mirror_and_entities` |
| Empty listing never wipes state | Refuses to mark all notes missing | `test_empty_source_listing_is_refused` |
| Generated layer rebuildable | Pages derived purely from the registry | `test_rebuild_from_registry_is_identical` |

## Design decisions

- **Source notes are graph nodes as they are.** Entity pages link *to* the mirror files. Graph View shows note ← book → note without touching the notes. Two notes are linked directly only by explicit evidence (a `references` rule) or an approved suggestion. Those links are shown on a generated *connections* page, never in the note.
- **Personal reflections** get a small page under `Entities/Personal Thinking/` that links to the note and the collection, so the collection stays a clean index.
- **The renderer is a pure function of the registry.** It always re-renders everything but writes only changed files. This is simpler and safer than incremental page patching.
- **One OpenAI-compatible client** covers OpenAI, xAI and Ollama. Adding Anthropic or another API means one new `LLMProvider` subclass.
- **Rules-only mode** is a real offline option: it recognises entities you've already confirmed but cannot discover new ones.
