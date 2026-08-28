from __future__ import annotations

import unittest

from tests.test_v5_runtime import RuntimeHarness


class LiveRuntimeRegressions(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def reset_harness(self):
        self.harness.tearDown()
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def test_startup_human_death_does_not_end_fatal_modes_before_active(self):
        modes = (
            "horde", "boss_rush", "wipeout_solo", "gauntlet_run",
            "last_stand", "one_life", "movement_hunter", "predator",
        )
        for index, mode in enumerate(modes):
            with self.subTest(mode=mode):
                if index:
                    self.reset_harness()
                server, plugin, human = self.harness.boot(mode)
                self.assertEqual(plugin.controller.phase.value, "preparing")
                before = plugin.player_deaths
                server.death(human, None)
                self.assertNotEqual(plugin.controller.phase.value, "complete")
                self.assertEqual(plugin.controller.phase.value, "preparing")
                self.assertEqual(plugin.player_deaths, before)

    def test_horde_bots_receive_director_role_loadout_and_aggressive_cvars(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertTrue(bots)
        bot = bots[0]
        track = plugin.director_runtime.director.tracks.get(bot.id)
        self.assertIsNotNone(track)
        self.assertIn(track.role, {"chaser", "gunner", "marksman", "bruiser", "skirmisher", "berserker"})
        armed = [key for key, enabled in bot._weapons.items() if enabled and key != "g"]
        self.assertTrue(armed)
        self.assertIn(bot._weapon, {1, 2, 3, 5, 6, 7, 8})
        ammo_values = [getattr(bot._ammo, key, 0) for key in armed]
        self.assertTrue(any(value > 0 for value in ammo_values))
        self.assertEqual(server.cvars.get("bot_dynamicskill"), "0")
        self.assertEqual(server.cvars.get("bot_aasoptimize"), "1")
        self.assertEqual(server.cvars.get("bot_rocketjump"), "1")

    def test_horde_director_replaces_far_idle_undamaged_bot_without_clearing_wave(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertTrue(bots)
        victim = bots[0]
        original_count = len(plugin.controller.enemy_ids)
        human.position(x=0, y=0, z=0)

        # Make the entire encounter genuinely low-pressure. The Director should
        # not recycle one distant bot while the rest are already pressuring the
        # player. The chosen victim is made the clearest non-contributor so the
        # recovery decision is deterministic.
        for index, bot in enumerate(bots):
            distance = 5000 if bot.id == victim.id else 3000 + index * 100
            bot.position(x=distance, y=0, z=0)
            track = plugin.director_runtime.director.tracks[bot.id]
            track.spawned_at = 0
            track.last_contact = 0
            track.damage_received = 0

        snapshot, actions = plugin.director_runtime.tick(force=True, now=20)
        self.assertIsNotNone(snapshot)
        self.assertLess(snapshot.pressure, snapshot.pressure_low)
        self.assertEqual(snapshot.engaged, 0)
        self.assertTrue(any(action.kind == "recover_bot" and action.bot_id == victim.id for action in actions))
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertNotIn(victim.id, plugin.controller.enemy_ids)

        server.advance(0.5)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(len(plugin.controller.enemy_ids), original_count)
        self.assertNotIn(victim.id, plugin.controller.enemy_ids)


if __name__ == "__main__":
    unittest.main()
