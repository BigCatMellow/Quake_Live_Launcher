from __future__ import annotations

import unittest

import launcher


class HotLoadWaitTests(unittest.TestCase):
    def test_loading_state_is_not_reported_as_completed_switch(self):
        old_status = launcher.solo_match_status
        old_ready = launcher.solo_plugin_ready
        states = [
            {"request_id": "abc", "state": "loading", "mode": "gun_game"},
            {"request_id": "abc", "state": "started", "mode": "gun_game"},
        ]
        try:
            launcher.solo_match_status = lambda: states.pop(0) if states else {"request_id": "abc", "state": "started", "mode": "gun_game"}
            launcher.solo_plugin_ready = lambda mode=None: True
            result = launcher.wait_for_solo_match_switch("abc", "gun_game", timeout=1.0)
            self.assertEqual(result["state"], "started")
        finally:
            launcher.solo_match_status = old_status
            launcher.solo_plugin_ready = old_ready


if __name__ == "__main__":
    unittest.main()
