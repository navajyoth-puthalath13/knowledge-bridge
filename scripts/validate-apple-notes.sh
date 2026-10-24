#!/usr/bin/env bash
# Phase 1 check on your Mac: can Knowledge Bridge read your Apple Notes folder, and how
# faithfully does each note convert? Read-only. Writes a report folder you can inspect.
#   scripts/validate-apple-notes.sh "Knowledge Bridge Test" [account]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$ROOT/backend/.venv/bin/python" ] || { echo "Run scripts/setup.sh first"; exit 1; }
cd "$ROOT/backend"
exec .venv/bin/python -m app.tools.validate_apple_notes "$@"
