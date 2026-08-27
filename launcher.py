#!/usr/bin/env python3
"""Loader for the v5 launcher implementation."""
from pathlib import Path
import base64
import gzip

_BASE = Path(__file__).resolve().parent
_PARTS = sorted(_BASE.glob("launcher_impl.py.gz.b64part*"))
if not _PARTS:
    raise RuntimeError("Quake Live Launcher payload is missing: launcher_impl.py.gz.b64part*")
_SOURCE = gzip.decompress(base64.b64decode("".join(p.read_text(encoding="ascii").strip() for p in _PARTS)))
exec(compile(_SOURCE, str(_BASE / "launcher_impl.py"), "exec"), globals(), globals())
