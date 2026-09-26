from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGINS = ROOT / "solo_engine" / "plugins"
ALL_MODES = (
    "arena_run", "horde", "gun_game", "boss_rush", "wipeout_solo",
    "gauntlet_run", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
    "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
    "random_loadout",
)


class RuntimeHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.pkg_root = self.home / "runtimepkg"
        shutil.copytree(SOURCE_PLUGINS, self.pkg_root / "minqlx-plugins")
        sys.path.insert(0, str(self.pkg_root))

    def tearDown(self):
        for key in list(sys.modules):
            if key == "minqlx" or key.startswith("minqlx-plugins"):
                sys.modules.pop(key, None)
        if str(self.pkg_root) in sys.path:
            sys.path.remove(str(self.pkg_root))
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.temp.cleanup()

    def boot(self, mode="horde", **extra):
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
        session.update(extra)
        config = self.home / ".config/quake-live-launcher"
        config.mkdir(parents=True, exist_ok=True)
        (config / "solo_session.json").write_text(json.dumps(session), encoding="utf-8")

        server = FakeServer()
        install_fake_minqlx(server)
        module = importlib.import_module("minqlx-plugins.solo_arcade")
        plugin = module.solo_arcade()
        server.plugin = plugin
        human = server.add_human()
        server.emit("player_loaded", human)
        server.emit("player_spawn", human)
        return server, plugin, human

    def active_bots(self, server, plugin):
        return [p for p in server.players.values() if p.id in plugin.controller.enemy_ids]

    def spawn_initial(self, server, max_seconds=3):
        server.advance(max_seconds)

    def kill_all_owned(self, server, plugin, human):
        for bot in list(self.active_bots(server, plugin)):
            server.death(bot, human)

    def test_minqlx_style_hyphen_package_import_and_ready_handshake(self):
        server, plugin, human = self.boot("horde")
        self.assertTrue(server.single_player_allowed)
        self.assertEqual(human.team, "red")
        ready = self.home / ".local/share/quake-live-launcher/solo_runtime/plugin_ready.json"
        payload = json.loads(ready.read_text())
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["mode"], "horde")

    def test_horde_bots_are_blue_and_early_kill_does_not_deadlock(self):
        server, plugin, human = self.boot("horde")
        server.run_next()
        server.run_next()
        bots = self.active_bots(server, plugin)
        self.assertGreaterEqual(len(bots), 1)
        server.death(bots[0], human)
        server.advance(2)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.controller.fulfilled_spawns, plugin.controller.expected_spawns)
        for bot in self.active_bots(server, plugin):
            self.assertEqual(bot.team, "blue")
        self.kill_all_owned(server, plugin, human)
        self.assertEqual(plugin.controller.phase.value, "between_rounds")
        server.advance(1.1)
        self.assertEqual(plugin.horde.wave, 2)

    def test_bot_vs_bot_kill_fails_contract_instead_of_clearing_wave(self):
        server, plugin, human = self.boot("horde")
        self.spawn_initial(server)
        bots = self.active_bots(server, plugin)
        self.assertGreaterEqual(len(bots), 2)
        server.death(bots[0], bots[1])
        self.assertEqual(plugin.controller.phase.value, "failed")
        self.assertIn("bot-vs-bot", plugin.controller.failure)

    def test_ground_dash_is_short_hop_and_horizontal_burst(self):
        server, plugin, human = self.boot("horde")
        human.velocity(x=100, y=0, z=0)
        plugin.request_side_dash(human, "right")
        human.velocity(x=100, y=25, z=0)
        server.advance(0.1)
        velocity = human.velocity()
        self.assertGreater(velocity.y, 25)
        self.assertEqual(velocity.z, 155)

    def test_gun_game_reaches_terminal_state(self):
        server, plugin, human = self.boot("gun_game")
        self.spawn_initial(server)
        safety = 20
        while plugin.controller.phase.value != "complete" and safety:
            bots = self.active_bots(server, plugin)
            self.assertTrue(bots)
            server.death(bots[0], human)
            server.advance(1)
            safety -= 1
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertTrue(plugin.gun_game.complete)

    def test_arena_run_two_round_flow_choice_and_completion(self):
        server, plugin, human = self.boot("arena_run", length=2)
        self.spawn_initial(server)
        self.kill_all_owned(server, plugin, human)
        self.assertTrue(plugin.run.waiting_for_pick)
        plugin.cmd_pick(human, ["!pick", "1"], None)
        server.advance(2)
        self.assertEqual(plugin.run.round, 2)
        self.kill_all_owned(server, plugin, human)
        self.assertTrue(plugin.run.complete)
        self.assertEqual(plugin.controller.phase.value, "complete")

    def test_wipeout_has_real_five_round_terminal_state(self):
        server, plugin, human = self.boot("wipeout_solo")
        for _ in range(5):
            self.spawn_initial(server)
            self.kill_all_owned(server, plugin, human)
            server.advance(1.2)
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertGreater(plugin.wipeout_round, 5)

    def test_movement_hunter_timer_finishes(self):
        server, plugin, human = self.boot("movement_hunter")
        self.spawn_initial(server)
        server.advance(91)
        self.assertEqual(plugin.controller.phase.value, "complete")

    def test_every_advertised_mode_reaches_gameplay_state(self):
        for mode in ALL_MODES:
            with self.subTest(mode=mode):
                self.tearDown(); self.setUp()
                server, plugin, human = self.boot(mode)
                server.advance(2)
                self.assertNotEqual(plugin.controller.phase.value, "failed")
                self.assertTrue(plugin.mode_started)
                if plugin.controller.phase.value not in ("complete", "between_rounds"):
                    self.assertIn(plugin.controller.phase.value, ("preparing", "active"))

    def _kill_until_complete(self, server, plugin, human, max_kills=40):
        kills = 0
        while plugin.controller.phase.value != "complete" and kills < max_kills:
            server.advance(1)
            bots = self.active_bots(server, plugin)
            if not bots:
                continue
            server.death(bots[0], human)
            kills += 1
        server.advance(2)
        return kills

    def test_boss_rush_ten_boss_terminal_state(self):
        server, plugin, human = self.boot("boss_rush")
        kills = self._kill_until_complete(server, plugin, human, 15)
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertEqual(kills, 10)

    def test_gauntlet_ten_stage_terminal_state(self):
        server, plugin, human = self.boot("gauntlet_run")
        self._kill_until_complete(server, plugin, human, 80)
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertGreater(plugin.gauntlet_stage, 10)

    def test_bounty_and_rocket_target_goals_complete(self):
        for mode, goal in (("bounty_hunt", 8), ("rocket_tag", 10)):
            with self.subTest(mode=mode):
                self.tearDown(); self.setUp()
                server, plugin, human = self.boot(mode)
                server.advance(2)
                safety = 30
                while plugin.controller.phase.value != "complete" and safety:
                    target = next((p for p in self.active_bots(server, plugin) if p.id == plugin.target_bot_id), None)
                    self.assertIsNotNone(target)
                    server.death(target, human)
                    server.advance(1)
                    safety -= 1
                self.assertEqual(plugin.controller.phase.value, "complete")
                self.assertGreaterEqual(plugin.target_score, goal)

    def test_kill_goal_modes_reach_terminal_state(self):
        for mode, goal in (("one_life", 12), ("predator", 25), ("accuracy_trial", 20), ("speedrun_combat", 15), ("random_loadout", 20)):
            with self.subTest(mode=mode):
                self.tearDown(); self.setUp()
                server, plugin, human = self.boot(mode)
                kills = self._kill_until_complete(server, plugin, human, goal + 8)
                self.assertEqual(plugin.controller.phase.value, "complete")
                self.assertGreaterEqual(kills, goal)

    def test_last_stand_human_death_is_terminal(self):
        server, plugin, human = self.boot("last_stand")
        server.advance(2)
        server.death(human, None)
        self.assertEqual(plugin.controller.phase.value, "complete")

    def test_arena_map_transition_resumes_from_player_lifecycle(self):
        server, plugin, human = self.boot(
            "arena_run",
            map_pools={"normal": ["bloodrun"], "boss": ["bloodrun"], "elite": ["bloodrun"], "rail": ["bloodrun"], "rocket": ["bloodrun"], "lg": ["bloodrun"]},
        )
        self.assertEqual(server.game.map, "bloodrun")
        self.assertIsNotNone(plugin.controller.pending_map)
        server.emit("player_loaded", human)
        self.assertIsNotNone(plugin.pending_resume_payload)
        server.emit("player_spawn", human)
        server.advance(2)
        self.assertIsNone(plugin.controller.pending_map)
        self.assertEqual(plugin.controller.phase.value, "active")


if __name__ == "__main__":
    unittest.main()
