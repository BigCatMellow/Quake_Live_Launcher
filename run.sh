#!/usr/bin/env bash
set -u
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/launcher_gui.py" "$@"
