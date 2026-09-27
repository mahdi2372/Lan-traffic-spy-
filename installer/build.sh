#!/usr/bin/env bash
# Build the standalone exe (Windows) or a zip fallback (other platforms).
#   python -m venv .venv && .venv/bin/pip install -r requirements-build.txt
#   ./installer/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi

echo "== running full test suites =="
"$PY" -m pytest tests/python -q
if command -v node >/dev/null 2>&1 && [ -d node_modules ]; then
  npm test
fi

echo "== building standalone exe (pyinstaller) =="
if "$PY" -m PyInstaller --clean --noconfirm installer/lanwatcher.spec; then
  echo "== done: dist/LANWatcher.exe =="
else
  echo "== pyinstaller unavailable here - building portable zipapp instead =="
  ./installer/build_zipapp.sh
fi

if command -v iscc >/dev/null 2>&1; then
  echo "== building installer (inno setup) =="
  iscc installer/LanWatcher.iss
else
  echo "iscc not found - run 'iscc installer/LanWatcher.iss' on a Windows box to build the setup exe."
fi
