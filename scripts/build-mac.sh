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
[ -n "$APP" ] || { echo "Build failed: Knowledge Bridge.app not found"; exit 1; }

# Apple Silicon refuses to launch apps with a missing or broken signature. Sign it locally
# ("ad-hoc", valid only on this Mac) and clear the download quarantine flag.
codesign --force --deep --sign - "$APP"
xattr -cr "$APP"
codesign --verify --deep "$APP" && echo "✓ Signed for this Mac"
echo "✓ Built: $APP"
if [ "${1:-}" = "--install" ]; then
  osascript -e 'quit app "Knowledge Bridge"' >/dev/null 2>&1 || true
  rm -rf "/Applications/Knowledge Bridge.app"
  cp -R "$APP" /Applications/
  xattr -cr "/Applications/Knowledge Bridge.app"
  echo "✓ Installed to /Applications/Knowledge Bridge.app"
  echo "  If it does not open, run this to see why:"
  echo "  \"/Applications/Knowledge Bridge.app/Contents/MacOS/Knowledge Bridge\""
fi
