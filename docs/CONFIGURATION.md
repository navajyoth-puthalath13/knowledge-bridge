# Configuration

Everything is set in the app (**Setup** and **Intelligence → Entity types**). It's stored in `~/Library/Application Support/Knowledge Bridge/config.json`, which stays outside the repository and the vault. API keys are never in this file; they live in the macOS Keychain under the service name "Knowledge Bridge".

| Key | Default | Meaning |
| --- | --- | --- |
| `vault_path` | — | Obsidian vault folder |
| `mirror_dir` | `Apple Notes` | Mirror folder inside the vault |
| `generated_dir` | `Knowledge Bridge` | Generated knowledge folder inside the vault. Must not overlap `mirror_dir`. |
| `obsidian_vault_name` | vault folder name | Name used for `obsidian://open?vault=` |
| `source.kind` | `apple_notes` | `apple_notes`, or `folder` for exported files |
| `source.account` / `source.folder` | — | Apple Notes account (empty = any) and folder path, e.g. `Knowledge/Podcasts` |
| `source.include_subfolders` | `false` | Mirror subfolders into matching subfolders |
| `source.export_attachments` | `true` | Copy attachments into `_attachments/` |
| `source.folder_path` | — | For `folder` sources |
| `provider.name` | `none` | Free: `none` (rules only), `ollama` (local), `gemini` (free tier), `groq` (free tier). Paid: `openai`, `xai` |
| `provider.model` | per provider | When empty: `llama3.1:8b`, `gemini-2.5-flash`, `llama-3.3-70b-versatile`, `gpt-4.1-mini`, `grok-3-mini` |
| `provider.base_url` | per provider | Override, e.g. a remote Ollama |
| `intelligence.auto_accept_confidence` | `0.8` | Below this, a new entity or stated relation goes to review |
| `intelligence.fuzzy_auto_merge` | `0.93` | Name similarity to merge automatically as an alias |
| `intelligence.fuzzy_review` | `0.80` | Similarity range `[0.80, 0.93)` creates a *possible duplicate* review |
| `intelligence.semantic_suggestions` | `true` | Ask the model for related notes (from a locally pre-filtered shortlist) |
| `intelligence.semantic_auto_accept` | `false` | Leave off: suggestions wait for your approval |
| `intelligence.enabled_types` | type defaults | e.g. `{"topic": true}`. Topics are off by default. |
| `sync_interval_minutes` | `15` | Automatic sync interval |
| `automation_enabled` | `false` | Automatic sync on/off. Persists across restarts; the first automatic run happens 30 s after launch. |
| `mirror_frontmatter` | `true` | Deterministic sync metadata at the top of each mirror |

## Environment variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `KB_DATA_DIR` | service | Override the data folder (tests use this) |
| `KB_TOKEN` | service | Per-session API token; set by Electron |
| `KB_PORT` | service | Port when run by hand (default 8765) |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY` | service | Override the Keychain |
| `KB_DISABLE_KEYRING` | service | Use the 0600 file fallback instead of the Keychain |
| `KB_MOCK_RESPONSES` | service | JSON of canned responses for provider `mock` (tests) |
| `KB_BACKEND_DIR`, `KB_PYTHON` | Electron | Override where the service is started from |

## Adding an entity type

Add one `EntityType(...)` to `backend/app/entities/types.py`, with a key, label, folder, collection, optional parent type and a one-line description for the prompt. The extraction prompt, resolver, renderer, UI toggle and index all pick it up automatically. To add a new collection, append it to `COLLECTIONS`.
