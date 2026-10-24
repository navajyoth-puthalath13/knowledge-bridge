#!/usr/bin/env bash
# Build and launch the desktop app from source (it starts the Python service itself).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$ROOT/backend/.venv/bin/python" ] || { echo "Run scripts/setup.sh first"; exit 1; }
cd "$ROOT/apps/desktop"
if [ "${1:-}" = "--watch" ]; then
  npm run dev        # hot-reloading renderer
else
  npm start          # production build of the renderer
fi
