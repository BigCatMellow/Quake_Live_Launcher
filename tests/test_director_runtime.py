from __future__ import annotations

import importlib
import json
import time
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import RuntimeHarness


class DirectorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def boot_directed(self, mode="horde", **extra):
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

    def active_bots(self, server, plugin):
        return [p for p in server.players.values() if p.id in plugin.controller.enemy_ids]

    def test_horde_idle_recovery_is_neutral_to_objective_progress(self):
        server, plugin, human = self.boot_directed("horde")
        server.advance(3)
        bots = self.active_bots(server, plugin)
        self.assertGreaterEqual(len(bots), 2)
        wave_before = plugin.horde.wave
        alive_before = len(plugin.controller.enemy_ids)

        human.position(x=0, y=0, z=0)
        now = time.time() + 20
        # Recovery should only happen when the encounter as a whole has gone
        # quiet; one wandering bot is not enough while others are fighting.
        for index, bot in enumerate(bots):
            bot.position(x=5000 if index == 0 else 3000 + index * 100, y=0, z=0)
            track = plugin.director_runtime.director.tracks[bot.id]
            track.last_contact = now - 20
            track.spawned_at = now - 20
            track.damage_received = 0
        idle = bots[0]
        plugin.director_runtime.tick(force=True, now=now)

        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.horde.wave, wave_before)
        self.assertNotIn(idle.id, plugin.controller.enemy_ids)
        server.advance(1)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.horde.wave, wave_before)
        self.assertEqual(len(plugin.controller.enemy_ids), alive_before)

    def test_gun_game_damage_burst_holds_replacement_without_changing_bot_skill(self):
        server, plugin, human = self.boot_directed("gun_game")
        server.advance(3)
        bots = self.active_bots(server, plugin)
        self.assertTrue(bots)
        victim = bots[0]
        human.position(x=0, y=0, z=0)
        for bot in bots:
            bot.position(x=300, y=0, z=0)

        plugin.handle_damage(human, victim, 85, 0, 6)
        plugin.director_runtime.tick(force=True, now=time.time())
        self.assertTrue(plugin.director_runtime.director.should_hold_reinforcements(time.time()))

        count_before = len(plugin.controller.enemy_ids)
        server.death(victim, human)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before - 1)
        server.advance(0.5)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before - 1)
        server.advance(4)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before)
        self.assertEqual(plugin.skill, 3)

    def test_horde_roles_use_bounded_weapon_loadouts(self):
        server, plugin, human = self.boot_directed("horde")
        server.advance(3)
        bots = self.active_bots(server, plugin)
        self.assertTrue(bots)
        for bot in bots:
            track = plugin.director_runtime.director.tracks.get(bot.id)
            self.assertIsNotNone(track)
            self.assertLessEqual(len(bot._weapons), 4)
            self.assertNotEqual(set(bot._weapons), {"g", "mg", "sg", "gl", "rl", "lg", "rg", "pg"})

    def test_director_command_explains_current_pressure(self):
        server, plugin, human = self.boot_directed("horde")
        server.advance(3)
        human.position(x=0, y=0, z=0)
        for bot in self.active_bots(server, plugin):
            bot.position(x=400, y=0, z=0)
        plugin.director_runtime.tick(force=True, now=time.time())
        plugin.cmd_director(human, ["!director"], None)
        self.assertTrue(any("DIRECTOR" in message for message in human.tells))
        self.assertTrue(any("pressure=" in message for message in human.tells))
        self.assertTrue(any("Learning:" in message for message in human.tells))

    def test_rocket_tag_bots_obey_rocket_only_contract(self):
        server, plugin, human = self.boot_directed("rocket_tag")
        server.advance(3)
        bots = self.active_bots(server, plugin)
        self.assertTrue(bots)
        for bot in bots:
            self.assertTrue(bot._weapons.get("rl"))
            self.assertFalse(bot._weapons.get("lg", False))
            self.assertFalse(bot._weapons.get("rg", False))


if __name__ == "__main__":
    unittest.main()
