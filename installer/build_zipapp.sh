#!/usr/bin/env bash
# Build a cross-platform standalone zipapp (dist/lanwatcher.pyz) when
# PyInstaller cannot run (e.g. no libpython shared library). Windows exe +
# installer are produced by installer/build.sh / CI on a Windows runner.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

echo "== compile check =="
"$PY" -m compileall -q lanwatcher launcher.py

echo "== staging =="
rm -rf build/ziproot dist/lanwatcher.pyz
mkdir -p build/ziproot dist
cp -r lanwatcher build/ziproot/
rm -rf build/ziproot/lanwatcher/**/__pycache__ build/ziproot/lanwatcher/__pycache__
cat > build/ziproot/__main__.py <<'EOF'
import sys
from lanwatcher.__main__ import main
sys.exit(main())
EOF

echo "== zipapp =="
"$PY" -m zipapp build/ziproot -o dist/lanwatcher.pyz -p "/usr/bin/env python3" --compress

echo "== smoke =="
timeout 6 "$PY" dist/lanwatcher.pyz --help >/dev/null
echo "OK: dist/lanwatcher.pyz"
