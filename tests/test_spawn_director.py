"""Tests for the spawn Director: learned spawn points, fair placement, flankers."""
from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
import random
import tempfile
import unittest

from tests.test_v5_runtime import RuntimeHarness

# A ring of engine spawn points around the origin (radius 1200) plus two that
# sit right next to where the player stands, like a small-map spawn.
RING = [(1200 * math.cos(a), 1200 * math.sin(a), 24.0) for a in [i * math.pi / 4 for i in range(8)]]
CLOSE = [(150.0, 0.0, 24.0), (-150.0, 60.0, 24.0)]


def load(name):
    return importlib.import_module(f"minqlx-plugins.{name}")


class SpawnBookTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()
        self.sd = load("spawn_director")
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "spawn_points.json"

    def tearDown(self):
        self.tmp.cleanup()
        self.harness.tearDown()

    def test_learn_merges_nearby_points_and_ignores_origin(self):
        book = self.sd.SpawnPointBook(self.path)
        self.assertTrue(book.learn("CampGrounds", (100, 100, 24)))
        self.assertFalse(book.learn("campgrounds", (130, 110, 24)), "within merge radius")
        self.assertFalse(book.learn("campgrounds", (0, 0, 0)), "unset origin is not a spawn")
        self.assertTrue(book.learn("campgrounds", (900, 100, 24)))
        points = book.points("campgrounds")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].seen, 2)

    def test_points_persist_per_map(self):
        book = self.sd.SpawnPointBook(self.path)
        for x in range(5):
            book.learn("bloodrun", (x * 300.0, 0.0, 16.0))
        self.assertTrue(book.save(force=True))
        again = self.sd.SpawnPointBook(self.path)
        self.assertEqual(len(again.points("bloodrun")), 5)
        self.assertEqual(again.points("campgrounds"), [])
        self.assertEqual(json.loads(self.path.read_text())["version"], 1)

    def test_cap_per_map(self):
        book = self.sd.SpawnPointBook(self.path)
        for i in range(self.sd.MAX_POINTS_PER_MAP + 20):
            book.learn("big", (i * 200.0, 0.0, 0.5))
        self.assertEqual(len(book.points("big")), self.sd.MAX_POINTS_PER_MAP)

    def request(self, **kw):
        base = dict(human_pos=(0.0, 0.0, 24.0), human_vel=None, occupants=[], preferred=1000.0, max_distance=1900.0)
        base.update(kw)
        return self.sd.PlacementRequest(**base)

    def ring_points(self):
        return [self.sd.SpawnPoint(*p) for p in RING]

    def test_too_few_points_leaves_engine_alone(self):
        points = self.ring_points()[:3]
        decision = self.sd.choose_spawn(points, (100.0, 0.0, 24.0), self.request(), rng=random.Random(1))
        self.assertFalse(decision.moved)
        self.assertEqual(decision.reason, "too_few_points")

    def test_fair_engine_choice_is_kept(self):
        decision = self.sd.choose_spawn(self.ring_points(), RING[0], self.request(), rng=random.Random(1))
        self.assertFalse(decision.moved)
        self.assertEqual(decision.reason, "engine_choice_ok")

    def test_spawn_on_top_of_player_is_moved_into_band(self):
        decision = self.sd.choose_spawn(self.ring_points(), CLOSE[0], self.request(), rng=random.Random(1))
        self.assertTrue(decision.moved)
        self.assertGreaterEqual(decision.distance, self.sd.MIN_PLAYER_DISTANCE)

    def test_never_places_within_min_distance_or_on_occupants(self):
        points = [self.sd.SpawnPoint(*p) for p in CLOSE + RING[:4]]
        occupied = [RING[0], RING[1], RING[2]]
        decision = self.sd.choose_spawn(points, CLOSE[0], self.request(occupants=occupied), rng=random.Random(1))
        self.assertTrue(decision.moved)
        self.assertEqual(decision.point.pos, RING[3])

    def test_no_fair_point_keeps_engine_choice(self):
        points = [self.sd.SpawnPoint(*p) for p in CLOSE] + [self.sd.SpawnPoint(5000.0 + i, 0.0, 0.0) for i in range(4)]
        decision = self.sd.choose_spawn(points, CLOSE[0], self.request(), rng=random.Random(1))
        self.assertFalse(decision.moved)
        self.assertEqual(decision.reason, "no_fair_point")

    def test_flankers_prefer_behind_a_moving_player(self):
        # Player runs toward +x; the point at 180 degrees (-1200, 0) is behind.
        for seed in range(10):
            points = self.ring_points()
            decision = self.sd.choose_spawn(
                points, RING[0], self.request(human_vel=(320.0, 0.0, 0.0), flank=True), rng=random.Random(seed)
            )
            self.assertTrue(decision.moved)
            self.assertLess(decision.point.x, -800, decision.point.pos)

    def test_front_liners_do_not_appear_behind_a_moving_player(self):
        points = self.ring_points()
        engine_pos = CLOSE[0]
        decision = self.sd.choose_spawn(points, engine_pos, self.request(human_vel=(320.0, 0.0, 0.0)), rng=random.Random(3))
        self.assertTrue(decision.moved)
        self.assertGreater(decision.point.x, -800)

    def test_recently_used_points_are_rotated(self):
        points = self.ring_points()
        used = set()
        for seed in range(4):
            decision = self.sd.choose_spawn(points, CLOSE[0], self.request(), now=100.0, rng=random.Random(seed))
            used.add(decision.point.pos)
        self.assertGreaterEqual(len(used), 4)


class ControllerReinforcementTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()
        self.mod = load("solo_controller")

    def tearDown(self):
        self.harness.tearDown()

    def controller(self):
        c = self.mod.SoloController("horde")
        c.wait_for_player(); c.player_loaded(0)
        c.begin_objective(2, auto_clear=True)
        c.expect_reinforcements(1)
        c.enemy_spawned(1); c.enemy_spawned(2)
        return c

    def test_clear_waits_for_inbound_reinforcements(self):
        c = self.controller()
        self.assertEqual(c.phase, self.mod.Phase.ACTIVE)
        self.assertFalse(c.enemy_died(1))
        self.assertFalse(c.enemy_died(2), "a flanker is still inbound")
        self.assertTrue(c.reinforcement_arrived(3))
        self.assertTrue(c.enemy_died(3))
        self.assertEqual(c.phase, self.mod.Phase.BETWEEN_ROUNDS)

    def test_cancelled_reinforcement_can_clear(self):
        c = self.controller()
        c.enemy_died(1); c.enemy_died(2)
        self.assertTrue(c.reinforcement_cancelled())
        self.assertEqual(c.phase, self.mod.Phase.BETWEEN_ROUNDS)

    def test_new_objective_resets_pending(self):
        c = self.controller()
        c.begin_objective(1)
        self.assertEqual(c.pending_reinforcements, 0)
        self.assertFalse(c.reinforcement_arrived(9))


class SpawnDirectorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def reset(self):
        self.harness.tearDown()
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def boot(self, mode="horde", points=None, **extra):
        server, plugin, human = self.harness.boot(mode, **extra)
        human.position(x=0, y=0, z=24)
        server.spawn_points = list(points if points is not None else RING)
        return server, plugin, human

    def bots(self, server, plugin):
        return self.harness.active_bots(server, plugin)

    def test_every_engine_spawn_is_learned_and_saved(self):
        server, plugin, human = self.boot()
        self.harness.spawn_initial(server)
        learned = plugin.spawn_book.points("campgrounds")
        self.assertGreaterEqual(len(learned), 2)
        plugin.spawn_book.save(force=True)
        data = json.loads((self.harness.home / ".local/share/quake-live-launcher/solo_runtime/spawn_points.json").read_text())
        self.assertIn("campgrounds", data["maps"])

    def test_bots_spawning_on_the_player_are_moved_away(self):
        server, plugin, human = self.boot()
        # Pre-learn the ring (as earlier waves would), then make the engine
        # pick spawns right next to the player.
        for point in RING:
            plugin.spawn_book.learn("campgrounds", point)
        server.spawn_points = list(CLOSE)
        self.harness.spawn_initial(server)
        bots = self.bots(server, plugin)
        self.assertTrue(bots)
        for bot in bots:
            pos = bot.position()
            d = math.dist((pos.x, pos.y, pos.z), (0, 0, 24))
            self.assertGreaterEqual(d, 700, (bot.name, d))
        positions = [(b.position().x, b.position().y) for b in bots]
        for i, a in enumerate(positions):
            for b in positions[i + 1:]:
                self.assertGreaterEqual(math.dist(a, b), 96)
        self.assertGreater(plugin.placement_stats["moved"], 0)

    def test_placement_is_logged_for_audit(self):
        server, plugin, human = self.boot()
        for point in RING:
            plugin.spawn_book.learn("campgrounds", point)
        server.spawn_points = list(CLOSE)
        self.harness.spawn_initial(server)
        log = self.harness.home / ".local/share/quake-live-launcher/solo_runtime/director_actions.jsonl"
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        placements = [e for e in events if e.get("event") == "spawn_placement"]
        self.assertTrue(placements)
        moved = [e for e in placements if e["moved"]]
        self.assertTrue(moved)
        self.assertTrue(all(e["distance"] >= 700 for e in moved))
        self.assertTrue(all(e["reason"] in ("band", "flank") for e in moved))

    def test_placement_can_be_disabled_per_session(self):
        server, plugin, human = self.boot(director={"spawn_placement": False})
        for point in RING:
            plugin.spawn_book.learn("campgrounds", point)
        server.spawn_points = list(CLOSE)
        self.harness.spawn_initial(server)
        self.assertEqual(plugin.placement_stats["moved"], 0)

    def horde_at_wave(self, wave, **extra):
        server, plugin, human = self.boot("horde", **extra)
        self.harness.spawn_initial(server)
        while plugin.horde.wave < wave:
            self.harness.kill_all_owned(server, plugin, human)
            server.advance(2)
            self.harness.spawn_initial(server)
        return server, plugin, human

    def test_large_waves_send_flankers_after_the_front_line(self):
        server, plugin, human = self.boot("horde")
        plugin.horde.wave = 6  # 5 bots -> 1 flanker
        plugin.clear_all_bots()
        plugin.controller.begin_objective(0)
        plugin._start_horde_wave()
        server.advance(2)
        self.assertEqual(plugin.controller.phase.value, "active", "front line alone activates the wave")
        self.assertEqual(plugin.controller.pending_reinforcements, 1)
        front = list(plugin.controller.enemy_ids)
        self.assertEqual(len(front), 4)
        for bot in list(self.bots(server, plugin)):
            server.death(bot, human)
        self.assertEqual(plugin.controller.phase.value, "active", "wave must not clear with a flanker inbound")
        server.advance(4)
        self.assertEqual(plugin.controller.pending_reinforcements, 0)
        self.assertTrue(any("FLANKERS" in m for m in server.messages))
        for bot in list(self.bots(server, plugin)):
            server.death(bot, human)
        self.assertEqual(plugin.controller.phase.value, "between_rounds")

    def test_flanker_that_never_spawns_stops_blocking_the_clear(self):
        server, plugin, human = self.boot("horde")
        plugin.horde.wave = 6
        plugin.clear_all_bots()
        plugin.controller.begin_objective(0)
        original = server.console_command

        def drop_flank_addbots(command):
            if command.startswith("addbot") and any(command.split()[1] == n for n in plugin.flank_names):
                server.commands.append(command)
                return
            original(command)

        import minqlx
        minqlx.console_command = drop_flank_addbots
        plugin._start_horde_wave()
        server.advance(2)
        for bot in list(self.bots(server, plugin)):
            server.death(bot, human)
        self.assertEqual(plugin.controller.phase.value, "active")
        server.advance(15)
        self.assertEqual(plugin.horde.wave, 7, "timed-out flanker released the clear and the next wave began")

    def test_small_squads_bosses_and_disabled_setting_have_no_flankers(self):
        server, plugin, human = self.boot("horde")
        self.assertEqual(plugin._split_squad(["a", "b", "c"])[1], [])
        plugin.current_plan = {"boss": True}
        self.assertEqual(plugin._split_squad(["a", "b", "c", "d"])[1], [])
        self.reset()
        server, plugin, human = self.boot("horde", director={"flankers": False})
        self.assertEqual(plugin._split_squad(["a", "b", "c", "d", "e", "f"])[1], [])
        self.reset()
        server, plugin, human = self.boot("one_life")
        self.assertEqual(plugin._split_squad(["a", "b", "c", "d", "e"])[1], [], "continuous modes are not squads")

    def test_every_wave_mode_still_completes_with_flankers(self):
        for mode, length in (("arena_run", 3), ("gauntlet_run", None), ("wipeout_solo", None)):
            with self.subTest(mode=mode):
                self.reset()
                extra = {"length": length} if length else {}
                server, plugin, human = self.boot(mode, **extra)
                for _ in range(200):
                    if plugin.controller.phase.value == "complete":
                        break
                    if plugin.mode == "arena_run" and plugin.run and plugin.run.waiting_for_pick:
                        plugin.cmd_pick(human, ["!pick", "1"], None)
                    server.advance(1)
                    bots = self.bots(server, plugin)
                    if bots:
                        server.death(bots[0], human)
                self.assertEqual(plugin.controller.phase.value, "complete")


if __name__ == "__main__":
    unittest.main()
