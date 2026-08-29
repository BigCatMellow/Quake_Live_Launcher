#!/usr/bin/env python3
"""Loader for the v5 GTK launcher implementation with Solo hot-load UI support."""
from pathlib import Path
import base64
import gzip

_BASE = Path(__file__).resolve().parent
_PARTS = sorted(_BASE.glob("launcher_gui_impl.py.gz.b64part*"))
if not _PARTS:
    raise RuntimeError("Quake Live Launcher GUI payload is missing: launcher_gui_impl.py.gz.b64part*")
_SOURCE = gzip.decompress(base64.b64decode("".join(p.read_text(encoding="ascii").strip() for p in _PARTS)))

# The retained GUI payload predates in-place Solo switching. Keep the payload
# byte-stable and apply the narrow compatibility edit here.
_old = b"""            if core.quake_running():\n                self.error('Close Quake Live before starting a Solo Engine mode.'); return\n"""
_new = b"""            if core.quake_running() and not core.solo_hot_switch_available():\n                self.error('Quake Live is running, but there is no healthy Solo Engine session to hot-load into. Close Quake once, start any Solo mode, and after that you can switch Solo modes without closing the game.'); return\n"""
if _old not in _SOURCE:
    raise RuntimeError("Solo hot-load GUI compatibility point was not found in the retained payload")
_SOURCE = _SOURCE.replace(_old, _new, 1)
_SOURCE = _SOURCE.replace(b'APP_VERSION = "5.0-alpha"', b'APP_VERSION = "5.0-alpha-hotload2"', 1)
exec(compile(_SOURCE, str(_BASE / "launcher_gui_impl.py"), "exec"), globals(), globals())
