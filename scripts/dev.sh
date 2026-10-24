#!/usr/bin/env bash
# Build and launch the desktop app from source (it starts the Python service itself).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$ROOT/backend/.venv/bin/python" ] || { echo "Run scripts/setup.sh first"; exit 1; }
cd "$ROOT/apps/desktop"
if ! node -e "require('electron')" >/dev/null 2>&1; then
  echo "→ Electron binary missing; downloading it"
  node node_modules/electron/install.js
fi
if [ "${1:-}" = "--watch" ]; then
  npm run dev        # hot-reloading renderer
else
  npm start          # production build of the renderer
fi
