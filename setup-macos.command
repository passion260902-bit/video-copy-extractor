#!/bin/bash
set -eu
cd -- "$(dirname -- "$0")"
for candidate in python3.11 python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not (3,11) <= sys.version_info[:2] < (3,14))'; then
    exec "$candidate" scripts/setup_runtime.py
  fi
done
echo 'Please install 64-bit Python 3.11, 3.12 or 3.13 from python.org, then retry.' >&2
exit 1
