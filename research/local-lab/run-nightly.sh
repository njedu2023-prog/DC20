#!/bin/sh
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$PYTHON" ]; then
  echo 'Bundled Python runtime missing; install/configure a NumPy-enabled runtime.' >&2
  exit 1
fi
exec "$PYTHON" "$BASE/operations.py" "$@"
