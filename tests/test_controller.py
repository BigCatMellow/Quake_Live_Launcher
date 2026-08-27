import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

from solo_controller import Phase, SoloController
from modes.horde import HordeState
from modes.gun_game import GunGameState, WEAPON_SEQUENCE


class ControllerTests(unittest.TestCase):
    def test_one_player_objective_lifecycle(self):
        c = SoloController("horde")
        c.wait_for_player()
        self.assertEqual(c.phase, Phase.WAITING_FOR_PLAYER)
        self.assertTrue(c.player_loaded(1))
        token = c.begin_objective(2)
        self.assertEqual(c.phase, Phase.PREPARING)
        c.enemy_spawned(10)
        self.assertEqual(c.phase, Phase.PREPARING)
        self.assertTrue(c.enemy_spawned(11))
        self.assertEqual(c.phase, Phase.ACTIVE)
        self.assertFalse(c.enemy_died(10))
        self.assertTrue(c.enemy_died(11))
        self.assertEqual(c.phase, Phase.BETWEEN_ROUNDS)
        self.assertTrue(c.token_valid(token, Phase.BETWEEN_ROUNDS))

    def test_unknown_bot_cannot_clear_wave(self):
        c = SoloController("horde", phase=Phase.PREPARING)
        c.begin_objective(1)
        c.enemy_spawned(10)
        self.assertFalse(c.enemy_died(99))
        self.assertEqual(c.phase, Phase.ACTIVE)
        self.assertEqual(c.enemy_ids, {10})

    def test_stale_callback_generation_is_invalid(self):
        c = SoloController("horde", phase=Phase.PREPARING)
        old = c.begin_objective(1)
        c.begin_objective(2)
        self.assertFalse(c.token_valid(old))

    def test_map_transition_waits_for_correct_map(self):
        c = SoloController("arena_run")
        c.wait_for_player(); c.player_loaded(3)
        c.request_map("bloodrun", {"round": 5})
        self.assertIsNone(c.resume_map_if_ready("campgrounds", 3))
        payload = c.resume_map_if_ready("bloodrun", 3)
        self.assertEqual(payload, {"round": 5})
        self.assertEqual(c.phase, Phase.PREPARING)

    def test_completion_invalidates_callbacks(self):
        c = SoloController("horde", phase=Phase.PREPARING)
        token = c.begin_objective(1)
        c.finish()
        self.assertEqual(c.phase, Phase.COMPLETE)
        self.assertFalse(c.token_valid(token))


class HordeTests(unittest.TestCase):
    def test_horde_scales_and_elites(self):
        h = HordeState(123)
        first = h.plan()
        self.assertEqual(first["wave"], 1)
        self.assertFalse(first["elite"])
        for _ in range(4): h.clear_wave()
        fifth = h.plan()
        self.assertEqual(fifth["wave"], 5)
        self.assertTrue(fifth["elite"])
        self.assertIn("keel", fifth["bots"])


class GunGameTests(unittest.TestCase):
    def test_gun_game_reaches_real_terminal_state(self):
        g = GunGameState()
        seen = [g.weapon]
        for _ in range(len(WEAPON_SEQUENCE) - 1):
            complete = g.scored_kill()
            seen.append(g.weapon)
        self.assertEqual(tuple(seen), WEAPON_SEQUENCE)
        self.assertFalse(complete)
        self.assertTrue(g.scored_kill())
        self.assertTrue(g.complete)


if __name__ == "__main__":
    unittest.main()
