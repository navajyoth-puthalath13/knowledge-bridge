#!/usr/bin/env bash
# Build "Knowledge Bridge.app" for this Mac. The app runs the Python service from
# this repository's backend/.venv, so keep the repository where it is (rebuild if moved).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(uname)" = "Darwin" ] || { echo "Run this on macOS"; exit 1; }
[ -x "$ROOT/backend/.venv/bin/python" ] || "$ROOT/scripts/setup.sh"
mkdir -p "$ROOT/apps/desktop/build"
echo "$ROOT/backend" > "$ROOT/apps/desktop/build/backend-path.txt"
cd "$ROOT/apps/desktop"
CSC_IDENTITY_AUTO_DISCOVERY=false npm run dist
APP="$(find "$ROOT/apps/desktop/release" -maxdepth 2 -name 'Knowledge Bridge.app' | head -1)"
echo "✓ Built: $APP"
if [ "${1:-}" = "--install" ]; then
  rm -rf "/Applications/Knowledge Bridge.app"
  cp -R "$APP" /Applications/
  echo "✓ Installed to /Applications/Knowledge Bridge.app"
fi
