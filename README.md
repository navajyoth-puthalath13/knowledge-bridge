# Knowledge Bridge

A personal macOS app that mirrors an Apple Notes folder into an Obsidian vault and adds an AI knowledge layer on top. It recognises books, podcast shows and episodes, people, design events and personal reflections. Each one gets a single canonical page, and Obsidian's Graph View connects your notes through them.

You keep writing in Apple Notes exactly as before. Knowledge Bridge:

- **Mirrors** each note into `Apple Notes/` in your vault as a faithful Markdown copy. It never inserts links, summaries or tags into the copy.
- **Recognises** entities anywhere in a note, including the middle of a paragraph. Repeated mentions resolve to one canonical entity ("Finding Flow" and "finding flow: the psychology of engagement…" become one book).
- **Generates** separate pages under `Knowledge Bridge/`: entity pages, collections (Books, Podcasts, Personal Thinking, Design Events) and a JSON index. Each page links to the source notes that mention it.
- **Asks you** about anything uncertain, such as ambiguous names, near-duplicates or suggested links between notes, through a review queue.

```
Podcast note ─┐                        ┌─► Books (collection)
              ├──◄ Finding Flow (book) ┤
Event note  ──┘                        └── evidence quotes from each note
```

The two notes are connected *through* the book page. No link is ever written into either note.

## Quick start (on your Mac)

Requirements: macOS, Python 3.11+ (`brew install python@3.12`), Node.js 20+ (`brew install node`), Obsidian.

```bash
git clone <your repo> knowledge-bridge && cd knowledge-bridge
scripts/setup.sh                      # Python venv + Node deps
scripts/validate-apple-notes.sh       # lists your Notes folders (first run triggers the macOS permission prompt)
scripts/validate-apple-notes.sh "Knowledge Bridge Test"   # fidelity report for a test folder
scripts/dev.sh                        # launch the app from source
scripts/build-mac.sh --install        # or: build "Knowledge Bridge.app" into /Applications
```

**API key (optional shortcut):** copy `.env.example` to `.env` in the repository folder and put your key in it, e.g. `GROQ_API_KEY=gsk_…`. `.env` is git-ignored and never uploaded. You can also paste the key in Setup, which stores it in the macOS Keychain.

In the app:

1. **Setup** → *Load folders* → pick your Apple Notes folder. Choose your Obsidian vault. Pick an AI provider, save an API key if needed, then press *Test connection*. Only free options are offered: **Groq** free tier (default), **Google Gemini** free tier, **Ollama** (runs on your Mac), or rules only. Saving Setup creates the whole folder structure in your vault.
2. **Dashboard** → *Dry run* shows exactly what would be written, without writing anything. Then press *Sync now*.
3. Open Obsidian → Graph View. Work through **Intelligence → Review queue** to confirm uncertain matches.
4. Turn on automatic sync (Setup or Dashboard). It runs while the Mac is awake and the app is open. On macOS, closing the window keeps it running until you quit.

The first access to Notes shows *"Knowledge Bridge wants to control Notes"*. Allow it. If you denied it: System Settings → Privacy & Security → Automation.

## What gets written where

| Location | Contents | Written by |
| --- | --- | --- |
| `<vault>/Apple Notes/<Notes folder>/` | Plain copies of your notes: same text and paragraphs, nothing added | Sync engine only. AI output never goes here. |
| `<vault>/Knowledge Bridge/Books/`, `Podcasts/`, `Events/` | One page per book, podcast and event. Each links to every note that mentions it, with the sentence that mentions it. These are the nodes in Graph View. | Renderer, through a writer that cannot write outside this folder |
| `<vault>/Knowledge Bridge/.kb/` | Hidden: JSON index and backups of anything ever overwritten | Renderer |
| `<vault>/.obsidian/graph.json` | Graph View colour groups: notes, books, podcasts, events (your other graph settings are kept) | Renderer |
| `~/Library/Application Support/Knowledge Bridge/` | `config.json`, SQLite registry, logs | Service |
| macOS Keychain ("Knowledge Bridge") | API keys | Service |

People's names, single podcast episodes and dated event editions are not nodes by default; turn them on in **Intelligence → Entity types** if you want them.

## Privacy

- Apple Notes is only **read**. The connector issues no write commands to Notes.
- **Rules only** and **Ollama** modes keep everything on your Mac, at no cost.
- **Google Gemini** and **Groq** have free tiers with rate limits. Free-tier Gemini requests may be used by Google to improve its products.
- With any cloud provider (Gemini, Groq, OpenAI, xAI), the text of *new or changed* notes in the selected folder is sent to that provider for entity extraction. Unchanged notes are never re-sent, and results are cached locally. The Setup and Dashboard screens show when cloud processing is active.
- The local API binds to `127.0.0.1` only. Every request needs a random per-launch token held by the Electron main process. The web page in the app never sees it.
- Personal data (notes, database, generated pages, keys) lives outside the repository, and `.gitignore` excludes it all.

## Development

```bash
scripts/test.sh                                    # everything below
cd backend && .venv/bin/python -m pytest -q        # 70+ backend tests (mock AI, fixtures)
cd apps/desktop && npm run build                   # typecheck + build renderer and main process
cd apps/desktop && npm run test:e2e                # drives the real Electron app + real service (xvfb-run on Linux)
scripts/dev.sh --watch                             # hot-reload renderer
```

Integration tests that need real systems are opt-in:

```bash
KB_MACOS_TESTS=1 KB_TEST_NOTES_FOLDER="Knowledge Bridge Test" .venv/bin/python -m pytest tests/test_macos_integration.py -v
KB_PROVIDER_TESTS=1 KB_PROVIDER=openai OPENAI_API_KEY=… .venv/bin/python -m pytest -m provider -v
```

Further reading:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data model, pipeline, guarantees.
- [docs/APPLE_NOTES.md](docs/APPLE_NOTES.md): connector evaluation and the macOS validation checklist.
- [docs/FIDELITY.md](docs/FIDELITY.md): what is and isn't preserved from Apple Notes.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): every setting and environment variable.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Repository layout

```
apps/desktop/     Electron + React + Vite control panel (electron/ = main & preload, src/ = UI, tests/e2e.mjs)
backend/app/      Python service: api/ connectors/ sync/ intelligence/ entities/ storage/ providers/ obsidian/ security/ tools/
backend/tests/    pytest suite (fixtures + mock AI); macOS / provider integration tests are opt-in
fixtures/         Sample notes and canned AI responses used by the tests
scripts/          setup, dev, build, test, Apple Notes validation
docs/             Architecture and operating docs
```
