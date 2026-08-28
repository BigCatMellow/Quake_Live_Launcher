from __future__ import annotations

import importlib
import json
import time
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import RuntimeHarness


class HotLoadGenerationTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def boot_directed(self):
        session = {
            "mode": "horde",
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
        session_path = config / "solo_session.json"
        session_path.write_text(json.dumps(session), encoding="utf-8")
        server = FakeServer()
        install_fake_minqlx(server)
        module = importlib.import_module("minqlx-plugins.solo_directed")
        plugin = module.solo_directed()
        server.plugin = plugin
        human = server.add_human()
        server.emit("player_loaded", human)
        server.emit("player_spawn", human)
        server.advance(3)
        return server, plugin, human, session_path

    def test_old_mode_delayed_callback_cannot_fire_after_hotload(self):
        server, plugin, human, session_path = self.boot_directed()
        self.assertEqual(plugin.mode, "horde")
        self.assertEqual(plugin.controller.phase.value, "active")
        controller_identity = id(plugin.controller)
        old_generation = plugin.controller.generation
        fired = []

        plugin._schedule(0.5, lambda: fired.append("stale-horde-callback"), plugin.controller.phase)

        session = json.loads(session_path.read_text())
        session.update({"mode": "gun_game", "seed": 98765, "created_at": time.time()})
        session_path.write_text(json.dumps(session), encoding="utf-8")
        runtime = self.harness.home / ".local/share/quake-live-launcher/solo_runtime"
        request_id = "generation-safety-switch"
        (runtime / "match_request.json").write_text(json.dumps({
            "request_id": request_id,
            "mode": "gun_game",
            "map": "campgrounds",
            "requested_at": time.time(),
        }), encoding="utf-8")

        plugin.next_match_request_poll = 0
        plugin.handle_frame()
        self.assertEqual(id(plugin.controller), controller_identity)
        self.assertGreater(plugin.controller.generation, old_generation)
        self.assertEqual(plugin.mode, "gun_game")

        human.is_alive = True
        server.emit("player_spawn", human)
        server.advance(1.0)

        self.assertEqual(fired, [])
        self.assertEqual(plugin.mode, "gun_game")
        self.assertEqual(plugin.controller.phase.value, "active")


if __name__ == "__main__":
    unittest.main()
