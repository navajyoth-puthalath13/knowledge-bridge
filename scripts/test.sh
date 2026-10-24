#!/usr/bin/env bash
# Run all automated tests: backend unit/integration tests, desktop typecheck + build, Electron end-to-end.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "→ Backend tests"
(cd "$ROOT/backend" && .venv/bin/python -m pytest -q)
echo "→ Desktop build"
(cd "$ROOT/apps/desktop" && npm run build >/dev/null)
echo "→ Electron end-to-end"
if [ "$(uname)" = "Linux" ] && [ -z "${DISPLAY:-}" ]; then
  (cd "$ROOT/apps/desktop" && xvfb-run -a npm run test:e2e)
else
  (cd "$ROOT/apps/desktop" && npm run test:e2e)
fi
