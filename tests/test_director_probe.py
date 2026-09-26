"""Tests for the Director capability probe (solo_probe) against the fake engine."""
from __future__ import annotations

import importlib
import json
import unittest

from tests.fake_minqlx import FakeServer, install_fake_minqlx
from tests.test_v5_runtime import RuntimeHarness

CATALOG = {
    "sarge": "bots/sarge_c.c", "keel": "bots/keel_c.c", "anarki": "bots/anarki_c.c",
    "qllprobea": "bots/sarge_c.c", "qllprobeb": "bots/qll_probe_c.c",
}


class ProbeLogicTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()
        install_fake_minqlx(FakeServer())
        self.probe = importlib.import_module("minqlx-plugins.solo_probe")

    def tearDown(self):
        self.harness.tearDown()

    def snap(self, **kw):
        base = {
            "alive": True, "health": 50, "armor": 0,
            "weapons": {k: False for k in self.probe.WEAPON_KEYS},
            "ammo": {k: 0 for k in self.probe.WEAPON_KEYS},
            "powerups": {k: False for k in self.probe.POWERUP_KEYS}, "holdable": None,
        }
        for key, value in kw.items():
            if isinstance(value, dict):
                base[key] = {**base[key], **value}
            else:
                base[key] = value
        return base

    def test_classify_pickups(self):
        c = self.probe.classify_pickup
        before = self.snap()
        self.assertEqual(c(before, self.snap(health=150))["guess"], "item_health_mega")
        self.assertEqual(c(before, self.snap(health=75))["guess"], "item_health")
        self.assertEqual(c(before, self.snap(health=55))["guess"], "item_health_small")
        self.assertEqual(c(before, self.snap(armor=100))["guess"], "item_armor_body")
        self.assertEqual(c(before, self.snap(armor=5))["guess"], "item_armor_shard")
        self.assertEqual(c(before, self.snap(weapons={"rl": True}, ammo={"rl": 10}))["guess"], "weapon_rocketlauncher")
        self.assertEqual(c(before, self.snap(ammo={"rg": 10}))["guess"], "ammo_slugs")
        self.assertEqual(c(before, self.snap(powerups={"quad": True}))["guess"], "item_quad")
        self.assertEqual(c(before, self.snap(holdable=27))["guess"], "holdable_27")
        self.assertFalse(c(before, self.snap())["picked"])

    def test_parse_botlist(self):
        rows = self.probe.parse_botlist([
            "^1name             model            aifile              funname",
            "Sarge            sarge            bots/sarge_c.c      Sarge",
            "QLLProbeB        sarge            bots/qll_probe_c.c  QLL Probe B",
            "random text line",
        ])
        self.assertEqual(rows["sarge"]["aifile"], "bots/sarge_c.c")
        self.assertIn("qllprobeb", rows)
        self.assertEqual(len(rows), 2)

    def test_lure_verdicts(self):
        v = self.probe.lure_verdict
        self.assertEqual(v(2000, 1500, "Sarge"), "picked_up")
        self.assertEqual(v(2000, 300, None), "approached")
        self.assertEqual(v(2000, 1900, None), "ignored")
        self.assertEqual(v(None, None, None), "not_measured")

    def test_character_file_verdicts(self):
        base = {"bots": {"qllprobeb": {"present": True, "characterfile": "bots/qll_probe_c.c"}}}
        loaded = dict(base, loader_lines=["loaded skill 4 from bots/qll_probe_c.c"])
        self.assertEqual(self.probe.verdicts(loaded)["custom_character_file"], "yes")
        fell = dict(base, loader_lines=["couldn't find skill 4 in bots/qll_probe_c.c", "loaded default skill 4 from bots/qll_probe_c.c"])
        self.assertEqual(self.probe.verdicts(fell)["custom_character_file"], "fell_back_to_default")
        mixed = dict(base, loader_lines=["loaded skill 4 from bots/sarge_c.c", "couldn't find skill 4 in bots/qll_probe_c.c"])
        self.assertEqual(self.probe.verdicts(mixed)["custom_character_file"], "fell_back_to_default")


class ProbeRunTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def run_probe(self, lure_behavior="pickup", catalog=CATALOG):
        config = self.harness.home / ".config/quake-live-launcher"
        config.mkdir(parents=True, exist_ok=True)
        (config / "solo_session.json").write_text(json.dumps({"mode": "horde", "map": "campgrounds"}))
        server = FakeServer()
        server.bot_catalog = dict(catalog)
        server.lure_behavior = lure_behavior
        # Distinct engine spawn points so the probe learns where to put the lure.
        server.spawn_points = [(0, 0, 24), (1500, 0, 24), (-1500, 200, 24), (0, 1800, 24), (300, -1200, 24)]
        install_fake_minqlx(server)
        module = importlib.import_module("minqlx-plugins.solo_probe")
        plugin = module.solo_probe()
        server.plugin = plugin
        # Fake bots never leave their spawn points, so a watcher's spawn adds
        # the free point a wandering real bot would have left open.
        watcher = server.add_human()
        watcher.position(x=300, y=-1200, z=24)
        server.emit("player_spawn", watcher)
        server.emit("player_loaded", watcher)
        for _ in range(400):
            if plugin.done:
                break
            server.advance(1.0)
        runtime = self.harness.home / ".local/share/quake-live-launcher/solo_runtime"
        return server, plugin, json.loads((runtime / "director_probe.json").read_text())

    def test_full_probe_answers_every_question(self):
        server, plugin, results = self.run_probe()
        self.assertTrue(results["done"])
        v = results["verdicts"]
        self.assertEqual(v["custom_bot_files"], "yes")
        self.assertEqual(v["custom_character_file"], "yes")
        self.assertEqual(v["fractional_skill"], "yes")
        self.assertEqual(v["mega_health_id"], 7)
        self.assertEqual(results["item_ids"]["weapon_rocketlauncher"], 12)
        self.assertEqual(results["item_ids"]["item_quad"], 26)
        self.assertEqual(results["item_count"], 29)
        self.assertEqual(v["bot_item_lure"], "picked_up")
        self.assertIn("sarge", results["botlist"]["stock_aifiles"])
        self.assertFalse([b for b in server.players.values() if b.steam_id > 90_000_000_000_000_000], "probe bots removed")

    def test_ignored_lure_and_missing_custom_bots(self):
        catalog = {k: v for k, v in CATALOG.items() if not k.startswith("qllprobe")}
        server, plugin, results = self.run_probe(lure_behavior="ignore", catalog=catalog)
        self.assertEqual(results["verdicts"]["bot_item_lure"], "ignored")
        self.assertFalse(results["botlist"]["has_probe_b"])

    def test_probe_writes_readiness_for_start_script(self):
        config = self.harness.home / ".config/quake-live-launcher"
        config.mkdir(parents=True, exist_ok=True)
        (config / "solo_session.json").write_text(json.dumps({"mode": "horde"}))
        server = FakeServer()
        install_fake_minqlx(server)
        importlib.import_module("minqlx-plugins.solo_probe").solo_probe()
        ready = json.loads((self.harness.home / ".local/share/quake-live-launcher/solo_runtime/plugin_ready.json").read_text())
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["mode"], "horde")

    def test_probe_never_calls_replace_items(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "solo_engine/plugins/solo_probe.py").read_text()
        code = "\n".join(line for line in source.splitlines() if not line.strip().startswith(("#", '"')))
        self.assertNotIn("minqlx.replace_items(", code)


if __name__ == "__main__":
    unittest.main()


class ProbeScriptTests(unittest.TestCase):
    """Runs run_director_probe.sh for real, with stub start/stop scripts."""

    def test_script_writes_bot_files_runs_probe_and_restores_state(self):
        import os
        import shutil
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            engine = home / "engine"
            engine.mkdir()
            shutil.copy(root / "solo_engine/run_director_probe.sh", engine / "run_director_probe.sh")
            runtime = home / ".local/share/quake-live-launcher/solo_runtime"
            (runtime / ".venv/bin").mkdir(parents=True)
            (runtime / ".venv/bin/python").symlink_to(sys.executable)
            (runtime / "READY").write_text("")
            session = home / ".config/quake-live-launcher/solo_session.json"
            session.parent.mkdir(parents=True)
            session.write_text('{"mode": "gun_game", "mine": true}')
            capture = home / "captured"
            (engine / "start_solo.sh").write_text(f"""#!/usr/bin/env bash
[ "$QLL_PLUGINS" = "solo_probe" ] || exit 9
mkdir -p "{capture}"
cp "$HOME/.local/share/quake-live-launcher/solo_runtime/home/baseq3/scripts/qll_probe.bot" "{capture}/"
cp "$HOME/.local/share/quake-live-launcher/solo_runtime/home/baseq3/botfiles/bots/qll_probe_c.c" "{capture}/"
cp "$HOME/.config/quake-live-launcher/solo_session.json" "{capture}/session.json"
echo '{{"done": true, "verdicts": {{"custom_bot_files": "yes", "bot_item_lure": "ignored"}}, "lure": {{}}}}' > "$HOME/.local/share/quake-live-launcher/solo_runtime/director_probe.json"
""")
            (engine / "stop_solo.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
            for name in ("start_solo.sh", "stop_solo.sh", "run_director_probe.sh"):
                os.chmod(engine / name, 0o755)
            env = dict(os.environ, HOME=str(home), QLL_PROBE_TIMEOUT="5")
            proc = subprocess.run(["bash", str(engine / "run_director_probe.sh")], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("Custom .bot files load", proc.stdout)
            bot_def = (capture / "qll_probe.bot").read_text()
            self.assertIn("QLLProbeB", bot_def)
            self.assertIn("bots/qll_probe_c.c", bot_def)
            char = (capture / "qll_probe_c.c").read_text()
            for skill in ("skill 1", "skill 4", "skill 5"):
                self.assertIn(skill, char)
            self.assertIn('0\t"QLLProbeB"', char)
            self.assertIn("37\t1.0", char)
            self.assertTrue(json.loads((capture / "session.json").read_text())["director_probe"])
            # Cleanup: user's session restored, temporary bot files removed.
            self.assertEqual(json.loads(session.read_text()), {"mode": "gun_game", "mine": True})
            self.assertFalse((runtime / "home/baseq3/scripts/qll_probe.bot").exists())
            self.assertFalse((runtime / "home/baseq3/botfiles/bots/qll_probe_c.c").exists())
