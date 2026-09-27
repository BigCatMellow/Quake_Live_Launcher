"""Regression tests for the 5.0-alpha-modes1 Solo mode overhaul.

Bugs covered: bot self-kills failing the run, duplicate bot names (deaths are
resolved by name), no resupply between rounds, stale/absent trial loadouts,
round-plan scaling that never reached bots, and Accuracy Trial measuring
nothing. Design changes covered: Last Stand escalation, Predator hunger,
Movement Hunter callouts, Gun Game tiers + humiliation, marked bounty targets,
Speedrun clock/splits, Wipeout lives, personal-best records, !again and the
F5-F7 upgrade-pick binds.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import RuntimeHarness

MOD_LIGHTNING = 8  # fake_minqlx value


class ModeOverhaulTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def reset(self):
        self.harness.tearDown()
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def boot(self, mode, **extra):
        return self.harness.boot(mode, **extra)

    def bots(self, server, plugin):
        return self.harness.active_bots(server, plugin)

    def records(self):
        path = self.harness.home / ".config/quake-live-launcher/solo_records.json"
        return json.loads(path.read_text()) if path.exists() else {}

    # ---------------- bugs ----------------
    def test_bot_self_kill_is_a_normal_enemy_death(self):
        server, plugin, human = self.boot("horde")
        self.harness.spawn_initial(server)
        bot = self.bots(server, plugin)[0]
        server.death(bot, bot, {"SUICIDE": True, "MOD": "ROCKET_SPLASH"})
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertIsNone(plugin.controller.failure)
        self.assertNotIn(bot.id, plugin.controller.enemy_ids)
        self.assertEqual(plugin.kills, 0, "a self-kill must not be credited to the player")

    def test_bot_self_kill_clears_wave_when_last(self):
        server, plugin, human = self.boot("horde")
        self.harness.spawn_initial(server)
        bots = self.bots(server, plugin)
        for bot in bots[:-1]:
            server.death(bot, human)
        server.death(bots[-1], bots[-1], {"SUICIDE": True})
        self.assertEqual(plugin.controller.phase.value, "between_rounds")

    def test_telefrag_between_bots_is_neutral(self):
        server, plugin, human = self.boot("horde")
        self.harness.spawn_initial(server)
        a, b = self.bots(server, plugin)[:2]
        server.death(a, b, {"MOD": "TELEFRAG"})
        self.assertEqual(plugin.controller.phase.value, "active")

    def test_real_bot_infighting_still_fails_contract(self):
        server, plugin, human = self.boot("horde")
        self.harness.spawn_initial(server)
        a, b = self.bots(server, plugin)[:2]
        server.death(a, b, {"MOD": "ROCKET"})
        self.assertEqual(plugin.controller.phase.value, "failed")

    def test_continuous_replacements_never_duplicate_live_names(self):
        for mode in ("last_stand", "one_life", "bounty_hunt", "speedrun_combat", "gun_game"):
            with self.subTest(mode=mode):
                self.reset()
                server, plugin, human = self.boot(mode)
                self.harness.spawn_initial(server)
                for _ in range(25):
                    if plugin.controller.phase.value != "active":
                        break
                    bots = self.bots(server, plugin)
                    if not bots:
                        server.advance(1); continue
                    target = next((b for b in bots if b.id == plugin.target_bot_id), bots[0])
                    server.death(target, human)
                    server.advance(1.5)
                    names = [b.name.lower() for b in server.players.values() if b.steam_id > 90_000_000_000_000_000]
                    self.assertEqual(len(names), len(set(names)), names)

    def test_horde_late_waves_have_unique_names(self):
        horde = importlib.import_module("minqlx-plugins.modes.horde")
        for wave in range(1, 40):
            plan = horde.HordeState(seed=77, wave=wave).plan()
            self.assertEqual(len(plan["bots"]), len(set(plan["bots"])), (wave, plan["bots"]))

    def test_horde_resupplies_between_waves_without_lowering_health(self):
        server, plugin, human = self.boot("horde")
        self.harness.spawn_initial(server)
        human._ammo.rl = 0
        human.health = 20
        self.harness.kill_all_owned(server, plugin, human)
        server.advance(2)
        self.assertEqual(plugin.horde.wave, 2)
        self.assertGreater(human._ammo.rl, 0)
        self.assertGreaterEqual(human.health, 150)
        human.health = 190
        self.harness.spawn_initial(server)
        self.harness.kill_all_owned(server, plugin, human)
        server.advance(2)
        self.assertEqual(human.health, 190, "resupply must never lower health")

    def test_boss_rush_resupplies_and_applies_damage_multiplier(self):
        server, plugin, human = self.boot("boss_rush")
        self.harness.spawn_initial(server)
        boss = self.bots(server, plugin)[0]
        self.assertGreaterEqual(boss.health, 750)
        human.health = 100
        plugin.handle_damage(human, boss, 50, 0, 6)
        self.assertLess(human.health, 100)
        human._ammo.rl = 0
        server.death(boss, human)
        server.advance(2)
        self.assertEqual(plugin.boss_round, 2)
        self.assertGreater(human._ammo.rl, 0)

    def test_gauntlet_trial_weapon_on_single_map(self):
        server, plugin, human = self.boot("gauntlet_run")
        self.harness.spawn_initial(server)
        expected = {"rail": 7, "rocket": 5, "lg": 6, "plasma": 8}
        for _ in range(6):
            kind = plugin.gauntlet_kind
            if kind in expected:
                self.assertEqual(human._weapon, expected[kind], kind)
            self.harness.kill_all_owned(server, plugin, human)
            server.advance(2)
            self.harness.spawn_initial(server)

    def test_arena_pick_loadout_matches_new_round_trial(self):
        server, plugin, human = self.boot("arena_run")
        core = importlib.import_module("minqlx-plugins.solo_core")
        plugin.run.round = 4  # round 4 is an LG trial
        core.roll_upgrade_choices(plugin.run)
        plugin.cmd_pick(human, ["!pick", "1"], None)
        self.assertEqual(plugin.current_plan.get("theme"), "lg")
        self.assertEqual(human._weapon, 6)

    def test_arena_round_plan_scales_regular_bots(self):
        server, plugin, human = self.boot("arena_run")
        core = importlib.import_module("minqlx-plugins.solo_core")
        plugin.run.round = 9
        plugin._launch_arena_plan(core.round_plan(plugin.run))
        self.harness.spawn_initial(server)
        runtime = importlib.import_module("minqlx-plugins.director_runtime")
        for bot in self.bots(server, plugin):
            role = plugin.director_runtime.director.tracks[bot.id].role
            base_hp, base_armor = runtime.ROLE_STATS[role]
            self.assertGreater(bot.health, base_hp)
            self.assertGreater(bot.armor, base_armor)

    def test_scaled_role_stats(self):
        runtime = importlib.import_module("minqlx-plugins.director_runtime")
        self.assertEqual(runtime.scaled_role_stats(100, 20, None), (100, 20))
        self.assertEqual(runtime.scaled_role_stats(140, 60, {"health": 150, "armor": 20}), (210, 80))
        self.assertEqual(runtime.scaled_role_stats(100, 20, {"boss": True, "health": 900, "armor": 300}), (900, 300))

    def test_accuracy_trial_reports_real_stats(self):
        server, plugin, human = self.boot("accuracy_trial")
        self.harness.spawn_initial(server)
        for _ in range(20):
            if plugin.controller.phase.value == "complete":
                break
            bot = self.bots(server, plugin)[0]
            for _hit in range(10):
                plugin.handle_damage(bot, human, 7, 0, MOD_LIGHTNING)
            server.death(bot, human)
            server.advance(1.5)
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertEqual(plugin.acc_hits, 200)
        self.assertEqual(plugin.acc_damage, 1400)
        self.assertEqual(len(plugin.acc_ttk), 20)
        self.assertTrue(any("LG:" in m and "time-to-kill" in m for m in server.messages))
        self.assertFalse(any("review final weapon accuracy" in m for m in server.messages))

    # ---------------- mode design ----------------
    def test_last_stand_threat_escalates_with_kills(self):
        server, plugin, human = self.boot("last_stand")
        self.harness.spawn_initial(server)
        base_skill = plugin._reinforcement_skill()
        for _ in range(15):
            server.death(self.bots(server, plugin)[0], human)
            server.advance(2)
        self.assertGreaterEqual(plugin.threat_level, 4)
        self.assertGreater(len(plugin.controller.enemy_ids), 5)
        self.assertGreater(plugin.current_plan["health"], 100)
        self.assertGreater(plugin._reinforcement_skill(), base_skill - 1)
        self.assertTrue(any("THREAT LEVEL" in m for m in server.messages))

    def test_last_stand_threat_escalates_with_time(self):
        server, plugin, human = self.boot("last_stand")
        self.harness.spawn_initial(server)
        plugin.objective_live_at -= 125
        plugin._update_last_stand_threat()
        self.assertEqual(plugin.threat_level, 3)

    def test_predator_hunger_drains_to_floor_and_resets_on_kill(self):
        server, plugin, human = self.boot("predator")
        self.harness.spawn_initial(server)
        human.health = 60
        now = time.time()
        plugin.last_kill_time = now - 30
        for step in range(40):
            plugin._tick_predator_hunger(now + step * 1.01)
        self.assertEqual(human.health, 10, "hunger drains to its floor but never kills")
        self.assertTrue(any("STARVING" in c for c in human.centers))
        server.death(self.bots(server, plugin)[0], human)
        self.assertEqual(human.health, 40)
        self.assertFalse(plugin.hunger_announced)

    def test_movement_hunter_timer_starts_live_with_callouts(self):
        server, plugin, human = self.boot("movement_hunter")
        self.harness.spawn_initial(server, 3)  # fake clock is now 3.0; bots went live ~0.6
        server.advance(85)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertTrue(any("60" in c for c in human.centers))
        self.assertTrue(any("10" in c for c in human.centers))
        server.advance(5)
        self.assertEqual(plugin.controller.phase.value, "complete")
        self.assertTrue(any("MOVEMENT HUNTER CLEAR" in m for m in server.messages))

    def test_gun_game_two_kills_per_tier_and_gauntlet_finish(self):
        server, plugin, human = self.boot("gun_game")
        self.harness.spawn_initial(server)
        self.assertEqual(plugin.gun_game.total_kills_required, 15)
        kills = 0
        while plugin.controller.phase.value != "complete" and kills < 30:
            server.death(self.bots(server, plugin)[0], human)
            kills += 1
            server.advance(1)
        self.assertEqual(kills, 15)
        self.assertEqual(plugin.controller.phase.value, "complete")

    def test_gun_game_bot_gauntlet_kill_demotes(self):
        server, plugin, human = self.boot("gun_game")
        self.harness.spawn_initial(server)
        for _ in range(4):
            server.death(self.bots(server, plugin)[0], human)
            server.advance(1)
        self.assertEqual(plugin.gun_game.index, 2)
        bot = self.bots(server, plugin)[0]
        server.death(human, bot, {"MOD": "GAUNTLET"})
        self.assertEqual(plugin.gun_game.index, 1)
        self.assertTrue(any("HUMILIATED" in m for m in server.messages))
        human.is_alive = True
        server.death(human, bot, {"MOD": "ROCKET"})
        self.assertEqual(plugin.gun_game.index, 1, "only Gauntlet kills demote")

    def test_bounty_target_is_marked_and_escapes_on_self_kill(self):
        server, plugin, human = self.boot("bounty_hunt")
        server.advance(2)
        target = next(b for b in self.bots(server, plugin) if b.id == plugin.target_bot_id)
        self.assertIn("haste", target._powerups)
        server.death(target, target, {"SUICIDE": True})
        self.assertEqual(plugin.target_score, 0)
        server.advance(2)
        self.assertIsNotNone(plugin.target_bot_id)
        self.assertNotEqual(plugin.target_bot_id, target.id)

    def test_speedrun_clock_starts_when_objective_is_live_and_saves_splits(self):
        server, plugin, human = self.boot("speedrun_combat")
        requested_at = plugin.start_time
        self.harness.spawn_initial(server)
        self.assertIsNotNone(plugin.objective_live_at)
        self.assertGreaterEqual(plugin.start_time, requested_at)
        for _ in range(15):
            server.death(self.bots(server, plugin)[0], human)
            server.advance(1)
        self.assertEqual(plugin.controller.phase.value, "complete")
        entry = self.records()["speedrun_combat:normal"]
        self.assertIn("best_time", entry)
        self.assertIn("split_5", entry)
        self.assertIn("split_10", entry)

    def test_wipeout_gives_three_lives(self):
        server, plugin, human = self.boot("wipeout_solo")
        self.harness.spawn_initial(server)
        bot = self.bots(server, plugin)[0]
        server.death(human, bot); human.is_alive = True
        server.death(human, bot); human.is_alive = True
        self.assertEqual(plugin.controller.phase.value, "active")
        server.death(human, bot)
        self.assertEqual(plugin.controller.phase.value, "complete")

    # ---------------- cross-mode ----------------
    def test_results_record_personal_bests(self):
        server, plugin, human = self.boot("horde")
        for _ in range(2):
            self.harness.spawn_initial(server)
            self.harness.kill_all_owned(server, plugin, human)
            server.advance(2)
        self.harness.spawn_initial(server)
        server.death(human, self.bots(server, plugin)[0])
        entry = self.records()["horde:normal"]
        self.assertEqual(entry["best_progress"], 2)
        self.assertEqual(entry["runs"], 1)
        self.assertTrue(any("RESULT" in m and "2 waves cleared" in m for m in server.messages))
        self.assertTrue(any("!again" in m for m in server.messages))

    def test_second_run_reports_new_best(self):
        saved = None
        for waves in (1, 3):
            self.reset()
            if saved is not None:
                records = self.harness.home / ".config/quake-live-launcher/solo_records.json"
                records.parent.mkdir(parents=True, exist_ok=True)
                records.write_text(json.dumps(saved))
            server, plugin, human = self.boot("horde")
            for _ in range(waves):
                self.harness.spawn_initial(server)
                self.harness.kill_all_owned(server, plugin, human)
                server.advance(2)
            self.harness.spawn_initial(server)
            server.death(human, self.bots(server, plugin)[0])
            saved = self.records()
        self.assertEqual(saved["horde:normal"]["best_progress"], 3)
        self.assertEqual(saved["horde:normal"]["runs"], 2)
        self.assertTrue(any("NEW BEST" in m and "was 1" in m for m in server.messages))

    def test_finish_mode_only_records_once(self):
        server, plugin, human = self.boot("one_life")
        self.harness.spawn_initial(server)
        plugin._finish_mode("done")
        plugin._finish_mode("again")
        self.assertEqual(self.records()["one_life:normal"]["runs"], 1)

    def test_best_command(self):
        server, plugin, human = self.boot("one_life")
        plugin.cmd_best(human, ["!best"], None)
        self.assertTrue(any("No records" in t for t in human.tells))

    def test_qlpick_client_command_picks_upgrade(self):
        server, plugin, human = self.boot("arena_run", length=3)
        self.harness.spawn_initial(server)
        self.harness.kill_all_owned(server, plugin, human)
        self.assertTrue(plugin.run.waiting_for_pick)
        minqlx = importlib.import_module("minqlx")
        self.assertEqual(plugin.handle_client_command(human, "qlpick 2"), minqlx.RET_STOP_ALL)
        self.assertFalse(plugin.run.waiting_for_pick)
        self.assertEqual(sum(plugin.run.upgrades.values()), 1)
        self.assertTrue(any("F5" in m for m in server.messages))


class AgainCommandTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def boot_directed(self, mode):
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

    def test_again_replays_same_mode_with_new_seed_after_death(self):
        server, plugin, human = self.boot_directed("horde")
        server.advance(3)
        server.death(human, next(p for p in server.players.values() if p.id in plugin.controller.enemy_ids))
        server.advance(1)
        self.assertEqual(plugin.controller.phase.value, "complete")
        old_seed = plugin.seed
        plugin.cmd_again(human, ["!again"], None)
        self.assertEqual(plugin.mode, "horde")
        self.assertNotEqual(plugin.seed, old_seed)
        self.assertEqual(plugin.horde.wave, 1)
        self.assertIn("map campgrounds tdm", server.commands)
        # The map reload brings the player back; the run restarts on spawn.
        server.emit("player_loaded", human)
        server.emit("player_spawn", human)
        server.advance(3)
        self.assertEqual(plugin.controller.phase.value, "active")
        self.assertEqual(plugin.kills, 0)


class PickBindTests(unittest.TestCase):
    def test_controls_cfg_binds_pick_keys_and_remembers_originals(self):
        import launcher
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp)
            (game_dir / "baseq3").mkdir()
            (game_dir / "baseq3" / "qzconfig.cfg").write_text(
                'bind A "+moveleft"\nbind D "+moveright"\nbind F5 "vote yes"\n', encoding="utf-8"
            )
            old_dirs = launcher.game_user_baseq3_dirs
            launcher.game_user_baseq3_dirs = lambda _g: []
            try:
                cfg, originals = launcher.write_solo_controls_cfg(game_dir, enabled=True)
            finally:
                launcher.game_user_baseq3_dirs = old_dirs
            text = cfg.read_text()
            for index, key in enumerate(("F5", "F6", "F7"), 1):
                self.assertIn(f'bind {key} "cmd qlpick {index}"', text)
            self.assertEqual(originals["F5"], "vote yes")
            self.assertEqual(originals["F6"], "")
            self.assertEqual(originals["A"], "+moveleft")
            self.assertIn("qldash", text)

    def test_controls_cfg_is_multiline_and_uses_real_strafe_keys(self):
        # Regression: the payload wrote one "//"-prefixed line (the whole file
        # was a comment) and its bind regex never matched, forcing A/D.
        import launcher
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp)
            (game_dir / "baseq3").mkdir()
            qz = game_dir / "baseq3" / "qzconfig.cfg"
            qz.write_text('bind S "+moveleft"\nbind F "+moveright"\n', encoding="utf-8")
            old_dirs = launcher.game_user_baseq3_dirs
            launcher.game_user_baseq3_dirs = lambda _g: []
            try:
                cfg, originals = launcher.write_solo_controls_cfg(game_dir, enabled=True)
                lines = cfg.read_text().splitlines()
                self.assertGreater(len(lines), 8)
                self.assertIn('bind S "+qll_side_left"', lines)
                self.assertIn('bind F "+qll_side_right"', lines)
                self.assertNotIn("\\n", cfg.read_text())
                # Simulate Quake saving the temporary binds, then restore.
                qz.write_text('bind S "+qll_side_left"\nbind F "+qll_side_right"\nbind F5 "cmd qlpick 1"\n', encoding="utf-8")
                self.assertTrue(launcher.restore_strafe_binds(game_dir, originals))
            finally:
                launcher.game_user_baseq3_dirs = old_dirs
            restored = qz.read_text()
            self.assertIn('bind S "+moveleft"', restored)
            self.assertIn('bind F "+moveright"', restored)
            self.assertIn('bind F5 ""', restored)
            self.assertNotIn("qll", restored)
            self.assertNotIn("qlpick", restored)
            self.assertNotIn("\\n", restored)


if __name__ == "__main__":
    unittest.main()
