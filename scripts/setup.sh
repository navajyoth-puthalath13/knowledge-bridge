#!/usr/bin/env bash
# One-time setup: Python environment for the service + Node dependencies for the app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.11+ is required (brew install python@3.12)"; exit 1; }
command -v npm >/dev/null || { echo "Node.js 20+ is required (brew install node)"; exit 1; }

echo "→ Python environment ($("$PY" --version)) in backend/.venv"
"$PY" -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/backend/.venv/bin/pip" install --quiet -e "$ROOT/backend[dev]"

echo "→ Desktop app dependencies"
(cd "$ROOT/apps/desktop" && npm install --no-audit --no-fund)

# Newer npm versions can skip Electron's download step; fetch the binary if it is missing.
if ! (cd "$ROOT/apps/desktop" && node -e "require('electron')" >/dev/null 2>&1); then
  echo "→ Downloading the Electron app binary"
  (cd "$ROOT/apps/desktop" && node node_modules/electron/install.js)
fi

echo "✓ Setup complete. Start the app with: scripts/dev.sh   (or build a Mac app with scripts/build-mac.sh)"
