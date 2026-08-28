from __future__ import annotations

import unittest

from solo_engine.plugins.solo_director import ROLE_SPECS, SoloDirector, profile_for


class DirectorCoreTests(unittest.TestCase):
    def test_every_mode_has_profile_and_valid_roles(self):
        modes = (
            "arena_run", "horde", "gun_game", "boss_rush", "wipeout_solo",
            "gauntlet_run", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
            "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
            "random_loadout",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                profile = profile_for(mode, "normal")
                self.assertLess(profile.pressure_low, profile.pressure_high)
                self.assertGreaterEqual(profile.max_engaged, 1)
                self.assertTrue(profile.roles)
                self.assertTrue(all(role in ROLE_SPECS for role in profile.roles))

    def test_difficulty_changes_pressure_before_skill(self):
        easy = profile_for("horde", "easy")
        normal = profile_for("horde", "normal")
        hard = profile_for("horde", "hard")
        nightmare = profile_for("horde", "nightmare")
        self.assertLess(easy.max_engaged, normal.max_engaged)
        self.assertGreaterEqual(hard.max_engaged, normal.max_engaged)
        self.assertGreaterEqual(nightmare.pressure_high, hard.pressure_high)
        self.assertLessEqual(easy.skill_cap, normal.skill_cap)
        self.assertLessEqual(normal.skill_cap, 4)

    def test_seeded_role_assignment_is_reproducible(self):
        first = SoloDirector("horde", "normal", 42)
        second = SoloDirector("horde", "normal", 42)
        first.begin_objective(0)
        second.begin_objective(0)
        roles_a = [first.role_for_spawn(index) for index in range(10)]
        roles_b = [second.role_for_spawn(index) for index in range(10)]
        self.assertEqual(roles_a, roles_b)
        self.assertGreater(len(set(roles_a)), 1)

    def test_low_pressure_idle_bot_requests_recovery(self):
        director = SoloDirector("horde", "normal", 7)
        director.begin_objective(0)
        director.register_bot(1, "slash", "chaser", 0)
        snapshot, actions = director.tick(
            now=10,
            player_health=125,
            player_armor=50,
            bots=[{"id": 1, "name": "slash", "role": "chaser", "distance": 2600}],
            active=True,
        )
        self.assertLess(snapshot.pressure, snapshot.pressure_low)
        self.assertEqual(snapshot.idle, 1)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "recover_bot")
        self.assertEqual(actions[0].bot_id, 1)

    def test_recent_contact_prevents_recovery(self):
        director = SoloDirector("horde", "normal", 7)
        director.begin_objective(0)
        director.register_bot(1, "slash", "chaser", 0)
        director.note_damage(
            now=9.5, damage=12, attacker_id=1, target_id=0,
            attacker_is_bot=True, target_is_bot=False,
        )
        snapshot, actions = director.tick(
            now=10,
            player_health=125,
            player_armor=50,
            bots=[{"id": 1, "name": "slash", "role": "chaser", "distance": 2600}],
            active=True,
        )
        self.assertEqual(snapshot.engaged, 1)
        self.assertFalse(any(action.kind == "recover_bot" for action in actions))

    def test_damaged_idle_bot_is_never_replaced_with_fresh_health(self):
        director = SoloDirector("horde", "normal", 7)
        director.begin_objective(0)
        director.register_bot(1, "slash", "chaser", 0)
        director.note_damage(
            now=1, damage=40, attacker_id=0, target_id=1,
            attacker_is_bot=False, target_is_bot=True,
        )
        snapshot, actions = director.tick(
            now=20,
            player_health=125,
            player_armor=50,
            bots=[{"id": 1, "name": "slash", "role": "chaser", "distance": 3000}],
            active=True,
        )
        self.assertEqual(snapshot.idle, 1)
        self.assertFalse(any(action.kind == "recover_bot" for action in actions))

    def test_severe_damage_creates_bounded_recovery_window(self):
        director = SoloDirector("gun_game", "normal", 7)
        director.begin_objective(0)
        director.note_damage(
            now=10, damage=85, attacker_id=1, target_id=0,
            attacker_is_bot=True, target_is_bot=False,
        )
        snapshot, actions = director.tick(
            now=10,
            player_health=35,
            player_armor=0,
            bots=[],
            active=True,
        )
        self.assertTrue(any(action.kind == "hold_reinforcements" for action in actions))
        self.assertTrue(director.should_hold_reinforcements(10.1))
        self.assertGreater(director.reinforcement_delay(10.1, 0.35), 0.35)
        self.assertFalse(director.should_hold_reinforcements(20))

    def test_non_recovery_modes_observe_without_replacing(self):
        director = SoloDirector("boss_rush", "normal", 7)
        director.begin_objective(0)
        director.register_bot(1, "keel", "boss", 0)
        snapshot, actions = director.tick(
            now=30,
            player_health=150,
            player_armor=50,
            bots=[{"id": 1, "name": "keel", "role": "boss", "distance": 5000}],
            active=True,
        )
        self.assertEqual(snapshot.idle, 1)
        self.assertFalse(any(action.kind == "recover_bot" for action in actions))


if __name__ == "__main__":
    unittest.main()