from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from solo_engine.plugins.director_learning import DirectorLearning
from solo_engine.plugins.solo_director import DirectorAction, DirectorSnapshot


def snapshot(*, pressure=20, low=42, high=68, taken=0, dealt=0, health=125, armor=50):
    return DirectorSnapshot(
        mode="horde",
        profile="Hunt",
        pressure=float(pressure),
        pressure_low=float(low),
        pressure_high=float(high),
        alive=4,
        engaged=1,
        idle=2,
        far=2,
        player_health=int(health),
        player_armor=int(armor),
        recent_damage_taken=float(taken),
        recent_damage_dealt=float(dealt),
        hold_until=0.0,
    )


class DirectorLearningTests(unittest.TestCase):
    def test_player_pressure_model_persists_and_decays_safely(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            learner = DirectorLearning(root, "horde", "normal", 9)
            for objective in range(3):
                start = objective * 20.0
                learner.begin_objective(start)
                for offset in range(1, 6):
                    learner.note_snapshot(
                        snapshot(pressure=16, taken=0, dealt=60),
                        ["chaser", "gunner"],
                        start + offset,
                    )
                learner.finish_objective(start + 8, reason="test")
            first_shift = learner.pressure_shift()
            self.assertGreater(first_shift, 0.0)
            self.assertLessEqual(first_shift, 6.0)
            self.assertTrue((root / "director_player.json").exists())

            reloaded = DirectorLearning(root, "horde", "normal", 10)
            self.assertGreater(reloaded.pressure_shift(), 0.0)
            self.assertLess(reloaded.pressure_shift(), first_shift)

    def test_action_is_evaluated_and_learned(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 11)
            learner.begin_objective(0)
            before = snapshot(pressure=12, taken=0, dealt=20)
            action = DirectorAction("recover_bot", "idle chaser", bot_id=7, role="chaser")
            action_id = learner.open_action(action, before, ["chaser", "gunner"], 0)
            learner.mark_execution(action_id, "replacement_spawned", replacement_bot_id=9)
            evaluations = learner.note_snapshot(
                snapshot(pressure=52, taken=10, dealt=30),
                ["chaser", "gunner"],
                5,
            )
            self.assertEqual(len(evaluations), 1)
            self.assertTrue(evaluations[0]["success"])
            row = learner.playbook["modes"]["horde"]["actions"]["recover_bot"]
            self.assertEqual(row["attempts"], 1)
            self.assertGreater(row["success_ema"], 0.9)
            self.assertTrue(learner.session_file.exists())

    def test_failed_action_creates_small_session_only_correction(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 15)
            action = DirectorAction("recover_bot", "idle chaser", bot_id=7, role="chaser")
            action_id = learner.open_action(action, snapshot(pressure=18), ["chaser"], 0)
            learner.mark_execution(action_id, "replacement_spawned", replacement_bot_id=8)
            evaluations = learner.note_snapshot(snapshot(pressure=15, taken=0), ["chaser"], 5)
            self.assertEqual(len(evaluations), 1)
            self.assertFalse(evaluations[0]["success"])
            self.assertGreater(learner.live_pressure_bias, 0.0)
            self.assertGreater(learner.pressure_shift(), 0.0)
            # Session correction must stay tightly bounded.
            self.assertLessEqual(abs(learner.live_pressure_bias), 4.0)

    def test_dangerous_action_immediately_biases_director_to_back_off(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 16)
            action = DirectorAction("recover_bot", "pressure test", bot_id=7, role="chaser")
            action_id = learner.open_action(action, snapshot(pressure=50, taken=5), ["chaser"], 0)
            learner.mark_execution(action_id, "replacement_spawned", replacement_bot_id=8)
            evaluations = learner.note_snapshot(snapshot(pressure=92, taken=100, health=30, armor=0), ["chaser"], 5)
            self.assertTrue(evaluations[0]["danger"])
            self.assertLess(learner.live_pressure_bias, 0.0)

    def test_learning_can_prefer_proven_role_but_cannot_leave_allowed_set(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 12)
            learner.player.setdefault("modes", {})["horde"] = {"objectives": 6, "pressure_shift": 0.0}
            learner.playbook.setdefault("modes", {})["horde"] = {
                "roles": {
                    "chaser": {"attempts": 10, "efficiency_ema": 0.20},
                    "gunner": {"attempts": 10, "efficiency_ema": 0.90},
                },
                "compositions": {},
            }
            chosen = learner.choose_role(
                "chaser",
                ("chaser", "gunner"),
                [],
                snapshot(pressure=18),
            )
            self.assertEqual(chosen, "gunner")
            self.assertIn(chosen, {"chaser", "gunner"})

            special = learner.choose_role(
                "boss", ("chaser", "gunner"), [], snapshot(pressure=18), special=True
            )
            self.assertEqual(special, "boss")

    def test_learned_role_selection_respects_composition_caps(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 17)
            learner.player.setdefault("modes", {})["horde"] = {"objectives": 6, "pressure_shift": 0.0}
            learner.playbook.setdefault("modes", {})["horde"] = {
                "roles": {
                    "chaser": {"attempts": 10, "efficiency_ema": 0.20},
                    "gunner": {"attempts": 10, "efficiency_ema": 0.95},
                },
                "compositions": {},
            }
            # Gunner is learned as effective, but two are already active.
            chosen = learner.choose_role(
                "chaser", ("chaser", "gunner"), ["gunner", "gunner"], snapshot(pressure=18)
            )
            self.assertEqual(chosen, "chaser")

    def test_playbook_records_dominant_composition_with_counts(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 18)
            learner.begin_objective(0)
            for now in (1, 2, 3):
                learner.note_snapshot(snapshot(pressure=52, taken=10, dealt=30), ["chaser", "chaser", "gunner"], now)
            learner.note_snapshot(snapshot(pressure=50), ["chaser", "gunner"], 4)
            learner.finish_objective(6, reason="test")
            comps = learner.playbook["modes"]["horde"]["compositions"]
            self.assertIn("chaser+chaser+gunner", comps)
            self.assertEqual(comps["chaser+chaser+gunner"]["attempts"], 1)

    def test_reinforcement_timing_adapts_pressure_not_damage(self):
        with TemporaryDirectory() as tmp:
            learner = DirectorLearning(Path(tmp), "horde", "normal", 13)
            faster = learner.adjust_reinforcement_delay(0.5, snapshot(pressure=12, taken=0))
            slower = learner.adjust_reinforcement_delay(0.5, snapshot(pressure=80, taken=70))
            neutral = learner.adjust_reinforcement_delay(0.5, snapshot(pressure=52, taken=10))
            self.assertLess(faster, 0.5)
            self.assertGreater(slower, 0.5)
            self.assertEqual(neutral, 0.5)

    def test_corrupt_memory_falls_back_to_safe_defaults(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(parents=True, exist_ok=True)
            (root / "director_player.json").write_text("not json", encoding="utf-8")
            (root / "director_playbook.json").write_text("[]", encoding="utf-8")
            learner = DirectorLearning(root, "horde", "normal", 14)
            self.assertEqual(learner.pressure_shift(), 0.0)
            self.assertEqual(learner.player["sessions"], 0)
            self.assertEqual(learner.playbook["modes"], {})


if __name__ == "__main__":
    unittest.main()
