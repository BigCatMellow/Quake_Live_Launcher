from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from solo_engine.plugins.director_runtime import DirectorRuntime
from solo_engine.plugins.solo_director import DirectorAction, DirectorSnapshot, profile_for


class FakeHuman:
    id = 1
    steam_id = 123456789
    health = 30
    armor = 0

    def position(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)


class FakePlugin:
    mode = "horde"
    current_plan = None
    kills = 0
    player_deaths = 0
    start_time = 0.0

    def __init__(self):
        self.controller = SimpleNamespace(phase=SimpleNamespace(value="active"), enemy_ids=set())
        self.human = FakeHuman()
        self.logs = []

    def primary_player(self):
        return self.human

    def bot_players(self):
        return []

    def _log(self, message):
        self.logs.append(str(message))


class DirectorLiveAdaptationTests(unittest.TestCase):
    def test_dangerous_action_lowers_target_inside_same_encounter(self):
        with TemporaryDirectory() as tmp:
            plugin = FakePlugin()
            runtime = DirectorRuntime(plugin, "horde", "normal", 42, Path(tmp))
            base = profile_for("horde", "normal")
            self.assertEqual(runtime.director.profile.pressure_low, base.pressure_low)

            before = DirectorSnapshot(
                mode="horde", profile="Hunt", pressure=50,
                pressure_low=base.pressure_low, pressure_high=base.pressure_high,
                alive=1, engaged=1, idle=0, far=0,
                player_health=100, player_armor=50,
                recent_damage_taken=5, recent_damage_dealt=15, hold_until=0,
            )
            action = DirectorAction("recover_bot", "test intervention", bot_id=7, role="chaser")
            action_id = runtime.learning.open_action(action, before, ["chaser"], 0)
            runtime.learning.mark_execution(action_id, "replacement_spawned", replacement_bot_id=8)

            # Simulate the intervention producing an immediate dangerous damage
            # burst. The evaluation happens at t=5 and must lower the Director's
            # target for subsequent decisions in this same objective.
            runtime.director.note_damage(
                now=5, damage=100, attacker_id=8, target_id=plugin.human.id,
                attacker_is_bot=True, target_is_bot=False,
            )
            snapshot, _ = runtime.tick(force=True, now=5)

            self.assertGreaterEqual(snapshot.recent_damage_taken, 100)
            self.assertLess(runtime.learning.live_pressure_bias, 0)
            self.assertLess(runtime.director.profile.pressure_low, base.pressure_low)
            self.assertLess(runtime.director.profile.pressure_high, base.pressure_high)
            self.assertGreaterEqual(runtime.director.profile.pressure_low, base.pressure_low - 6)

    def test_live_correction_never_changes_native_skill_contract(self):
        with TemporaryDirectory() as tmp:
            plugin = FakePlugin()
            runtime = DirectorRuntime(plugin, "horde", "normal", 43, Path(tmp))
            original_skill_cap = runtime.director.profile.skill_cap
            runtime.learning.live_pressure_bias = -4.0
            runtime._refresh_learned_profile()
            self.assertEqual(runtime.director.profile.skill_cap, original_skill_cap)
            self.assertEqual(runtime.director.profile.max_engaged, profile_for("horde", "normal").max_engaged)


if __name__ == "__main__":
    unittest.main()
