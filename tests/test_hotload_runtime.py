from __future__ import annotations

import importlib
import json
import os
import time
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import RuntimeHarness


class HotLoadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def boot_directed(self, mode="horde"):
        session = {
            "mode": mode,
            "map": "campgrounds",
            "maps": ["campgrounds"],
            "map_pools": {key: ["campgrounds"] for key in ("normal", "boss", "elite", "rail", "rocket", "lg", "plasma", "duel", "survival")},
            "skill": 3,
            "difficulty": "normal",
            "length": 2,
            "seed": 1234,
            "movement": {"air_control": "enhanced", "side_thrusters": True, "dash_strength": 340, "ground_dash_hop": 155, "dash_charges": 1},
        }
        config = self.harness.home / ".config/quake-live-launcher"
        config.mkdir(parents=True, exist_ok=True)
        (config / "solo_session.json").write_text(json.dumps(session), encoding="utf-8")
        server = FakeServer()
        install_fake_minqlx(server)
        module = importlib.import_module("minqlx-plugins.solo_directed")
        plugin = module.solo_directed()
        server.plugin = plugin
        human = server.add_human()
        server.emit("player_loaded", human)
        server.emit("player_spawn", human)
        return server, plugin, human

    def test_training_contract_is_active_before_horde_gameplay(self):
        server, plugin, human = self.boot_directed("horde")
        self.assertTrue(server.single_player_allowed)
        self.assertEqual(server.cvars.get("g_training"), "1")
        self.assertEqual(human.team, "red")
        server.advance(3)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertTrue(plugin.controller.enemy_ids)

        # Model the exact live race we are protecting against: an initialization
        # boundary loses the level's training flag after the plugin constructor.
        # The first live frame must restore single-player permission before the
        # ordinary multiplayer forfeit path can own the match.
        server.single_player_allowed = False
        server.cvars["g_training"] = "0"
        plugin.next_training_assert = 0
        plugin.handle_frame()
        self.assertTrue(server.single_player_allowed)
        self.assertEqual(server.cvars.get("g_training"), "1")
        self.assertEqual(plugin.controller.phase.value, "active")

    def test_directed_server_advertises_hotload_protocol_for_its_pid(self):
        _server, plugin, _human = self.boot_directed("horde")
        runtime = self.harness.home / ".local/share/quake-live-launcher/solo_runtime"
        marker = json.loads((runtime / "hotload_ready.json").read_text())
        self.assertTrue(marker["ready"])
        self.assertEqual(marker["protocol"], 1)
        self.assertEqual(marker["pid"], os.getpid())
        self.assertEqual(marker["mode"], "horde")
        plugin.handle_unload(plugin)
        self.assertFalse((runtime / "hotload_ready.json").exists())

    def test_running_server_hot_loads_new_solo_mode_without_reconnect(self):
        server, plugin, human = self.boot_directed("horde")
        server.advance(3)
        self.assertEqual(plugin.mode, "horde")
        self.assertEqual(plugin.controller.phase.value, "active")
        original_human_id = human.id

        config = self.harness.home / ".config/quake-live-launcher"
        session_path = config / "solo_session.json"
        session = json.loads(session_path.read_text())
        session.update({
            "mode": "gun_game",
            "map": "campgrounds",
            "seed": 98765,
            "created_at": time.time(),
        })
        session_path.write_text(json.dumps(session), encoding="utf-8")

        runtime = self.harness.home / ".local/share/quake-live-launcher/solo_runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        request_id = "test-hotload-1"
        (runtime / "match_request.json").write_text(json.dumps({
            "request_id": request_id,
            "mode": "gun_game",
            "map": "campgrounds",
            "requested_at": time.time(),
        }), encoding="utf-8")

        plugin.next_match_request_poll = 0
        plugin.handle_frame()

        self.assertEqual(plugin.mode, "gun_game")
        self.assertEqual(plugin.player_id, original_human_id)
        self.assertEqual(human.team, "red")
        self.assertIn("map campgrounds tdm", server.commands)
        self.assertEqual(server.cvars.get("g_training"), "1")
        ready = json.loads((runtime / "plugin_ready.json").read_text())
        self.assertEqual(ready["mode"], "gun_game")
        hotload = json.loads((runtime / "hotload_ready.json").read_text())
        self.assertEqual(hotload["mode"], "gun_game")
        self.assertEqual(hotload["protocol"], 1)
        status = json.loads((runtime / "match_status.json").read_text())
        self.assertEqual(status["request_id"], request_id)
        self.assertEqual(status["state"], "loading")

        human.is_alive = True
        server.emit("player_spawn", human)
        server.advance(3)
        self.assertTrue(plugin.mode_started)
        self.assertIsNotNone(plugin.gun_game)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertTrue(plugin.controller.enemy_ids)
        status = json.loads((runtime / "match_status.json").read_text())
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["mode"], "gun_game")


if __name__ == "__main__":
    unittest.main()
