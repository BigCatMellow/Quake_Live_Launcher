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

    def test_horde_bots_receive_combat_loadout_and_aggressive_cvars(self):
        server, plugin, human = self.harness.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.harness.active_bots(server, plugin)
        self.assertTrue(bots)
        bot = bots[0]
        self.assertTrue(bot._weapons.get("rl"))
        self.assertTrue(bot._weapons.get("lg"))
        self.assertTrue(bot._weapons.get("rg"))
        self.assertGreater(bot._ammo.rl, 0)
        self.assertGreater(bot._ammo.lg, 0)
        self.assertEqual(server.cvars.get("bot_dynamicskill"), "0")
        self.assertEqual(server.cvars.get("bot_aasoptimize"), "1")
        self.assertEqual(server.cvars.get("bot_rocketjump"), "1")


if __name__ == "__main__":
    unittest.main()
