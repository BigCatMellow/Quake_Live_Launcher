from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import launcher


class HotLoadLauncherTests(unittest.TestCase):
    def test_hot_switch_requires_pid_matched_protocol_marker(self):
        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / "hotload_ready.json"
            old_marker = launcher.SOLO_HOTLOAD_READY_FILE
            old_pid = launcher.solo_server_pid
            old_ready = launcher.solo_plugin_ready
            try:
                launcher.SOLO_HOTLOAD_READY_FILE = marker
                launcher.solo_server_pid = lambda: 4321
                launcher.solo_plugin_ready = lambda *args, **kwargs: True
                self.assertFalse(launcher.solo_hot_switch_available())

                marker.write_text(json.dumps({"ready": True, "protocol": 1, "pid": 9999}))
                self.assertFalse(launcher.solo_hot_switch_available())

                marker.write_text(json.dumps({"ready": True, "protocol": 1, "pid": 4321}))
                self.assertTrue(launcher.solo_hot_switch_available())

                marker.write_text(json.dumps({"ready": True, "protocol": 2, "pid": 4321}))
                self.assertFalse(launcher.solo_hot_switch_available())
            finally:
                launcher.SOLO_HOTLOAD_READY_FILE = old_marker
                launcher.solo_server_pid = old_pid
                launcher.solo_plugin_ready = old_ready


if __name__ == "__main__":
    unittest.main()
