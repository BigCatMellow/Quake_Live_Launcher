from __future__ import annotations

import time
import unittest

from tests.test_v5_runtime import RuntimeHarness


class DirectorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def test_horde_idle_recovery_is_neutral_to_objective_progress(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertGreaterEqual(len(bots), 2)
        wave_before = plugin.horde.wave
        alive_before = len(plugin.controller.enemy_ids)

        human.position(x=0, y=0, z=0)
        idle = bots[0]
        idle.position(x=3000, y=0, z=0)
        for bot in bots[1:]:
            bot.position(x=250, y=0, z=0)

        now = time.time() + 20
        plugin.director.tracks[idle.id].last_contact = now - 20
        plugin.director_next_tick = 0
        plugin._director_tick(now)

        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.horde.wave, wave_before)
        self.assertNotIn(idle.id, plugin.controller.enemy_ids)
        server.advance(1)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.horde.wave, wave_before)
        self.assertEqual(len(plugin.controller.enemy_ids), alive_before)

    def test_gun_game_damage_burst_holds_replacement_without_changing_bot_skill(self):
        server, plugin, human = self.harness.boot("gun_game")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertTrue(bots)
        victim = bots[0]
        human.position(x=0, y=0, z=0)
        for bot in bots:
            bot.position(x=300, y=0, z=0)

        plugin.handle_damage(human, victim, 85, 0, 6)
        plugin.director_next_tick = 0
        plugin._director_tick(time.time())
        self.assertTrue(plugin.director.should_hold_reinforcements(time.time()))

        count_before = len(plugin.controller.enemy_ids)
        server.death(victim, human)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before - 1)
        server.advance(0.5)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before - 1)
        server.advance(4)
        self.assertEqual(len(plugin.controller.enemy_ids), count_before)
        self.assertEqual(plugin.skill, 3)

    def test_horde_roles_use_bounded_weapon_loadouts(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertTrue(bots)
        for bot in bots:
            role = plugin.director_roles.get(bot.id)
            self.assertIsNotNone(role)
            self.assertLessEqual(len(bot._weapons), 4)
            self.assertNotEqual(set(bot._weapons), {"g", "mg", "sg", "gl", "rl", "lg", "rg", "pg"})

    def test_director_command_explains_current_pressure(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        human.position(x=0, y=0, z=0)
        for bot in self.harness.active_bots(server, plugin):
            bot.position(x=400, y=0, z=0)
        plugin.director_next_tick = 0
        plugin._director_tick(time.time())
        plugin.cmd_director(human, ["!director"], None)
        self.assertTrue(any("DIRECTOR" in message for message in human.tells))
        self.assertTrue(any("pressure=" in message for message in human.tells))


if __name__ == "__main__":
    unittest.main()
