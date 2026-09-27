"""Regression tests for the instant-forfeit failure.

Root cause: the scripted TDM sandbox was forced straight into a live match
(g_doWarmup 0, sv_warmupReadyPercentage 0). Every mode starts by clearing BLUE
and adding bots on later frames, and defeated enemies are kicked, so BLUE is
empty at mode start and at every wave clear. A live TDM match with an empty
team forfeits; allow_single_player() does not cover that rule. The fix keeps
the sandbox in warmup for its whole life.

FakeServer.would_forfeit() models the match-layer rule so these tests fail if
the old contract (or anything that lets a match start) comes back.
"""
from __future__ import annotations

import importlib
import json
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import ALL_MODES, RuntimeHarness


class WarmupSandboxTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def reset(self):
        self.harness.tearDown()
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def test_plugin_configures_permanent_warmup(self):
        server, _plugin, _human = self.harness.boot("horde")
        self.assertEqual(server.cvars.get("g_doWarmup"), "1")
        self.assertEqual(server.cvars.get("sv_warmupReadyPercentage"), "1")
        self.assertEqual(server.cvars.get("g_warmupReadyDelay"), "0")
        self.assertEqual(server.game.state, "warmup")

    def test_old_contract_is_what_forfeited(self):
        # Documents the failure: a live match with only the human on RED.
        server, _plugin, _human = self.harness.boot("horde")
        server.cvars["g_doWarmup"] = "0"
        self.assertTrue(server.would_forfeit())

    def test_no_mode_can_forfeit_at_start_or_when_blue_empties(self):
        for index, mode in enumerate(ALL_MODES):
            with self.subTest(mode=mode):
                if index:
                    self.reset()
                server, plugin, human = self.harness.boot(mode)
                # The instant the human spawns, BLUE is empty.
                self.assertFalse([p for p in server.players.values() if p.team == "blue"])
                self.assertFalse(server.would_forfeit(), f"{mode} forfeits at spawn")
                self.harness.spawn_initial(server)
                self.assertFalse(server.would_forfeit())
                # Clearing every enemy (kicks) empties BLUE again.
                plugin.clear_all_bots()
                self.assertFalse(server.would_forfeit(), f"{mode} forfeits when BLUE empties")

    def test_horde_wave_clear_does_not_forfeit(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        for bot in list(self.harness.active_bots(server, plugin)):
            server.death(bot, human)
            self.assertFalse(server.would_forfeit())
        self.assertEqual(plugin.controller.phase.value, "between_rounds")
        server.advance(3)
        self.assertEqual(plugin.horde.wave, 2)
        self.assertFalse(server.would_forfeit())

    def test_match_countdown_is_aborted_back_to_warmup(self):
        server, plugin, _human = self.harness.boot("horde")
        server.force_match_start()
        self.assertIn("abort", server.commands)
        self.assertEqual(server.game.state, "warmup")
        self.assertFalse(server.would_forfeit())

    def test_ready_up_is_blocked(self):
        server, plugin, human = self.harness.boot("horde")
        minqlx = importlib.import_module("minqlx")
        for command in ("readyup", "ready", "notready"):
            self.assertEqual(plugin.handle_client_command(human, command), minqlx.RET_STOP_ALL)
        self.assertIsNone(plugin.handle_client_command(human, "say hi"))

    def boot_directed(self, mode="horde"):
        session = {
            "mode": mode, "map": "campgrounds", "maps": ["campgrounds"],
            "map_pools": {"normal": ["campgrounds"]}, "skill": 3, "difficulty": "normal",
            "length": 2, "seed": 1234,
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

    def test_directed_frame_backstop_returns_live_match_to_warmup(self):
        server, plugin, _human = self.boot_directed("horde")
        self.assertEqual(server.game.state, "warmup")
        server.game.match_forced = True
        plugin.last_warmup_hold = 0.0
        plugin.next_warmup_check = 0.0
        plugin.handle_frame()
        self.assertIn("abort", server.commands)
        self.assertEqual(server.game.state, "warmup")


if __name__ == "__main__":
    unittest.main()
