#!/bin/bash
# Double-click launcher for Snip2MD on macOS.
cd "$(dirname "$0")"
ROOT="$(pwd -P)"
PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
if [[ -z "$PY" ]]; then
  echo "Python 3.11+ is required. Install it from https://www.python.org/downloads/"
  read -r _
  exit 1
fi
exec "$PY" "$ROOT/snip2md.py"
