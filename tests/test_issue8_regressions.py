"""Regression tests from the 2026-09-26 auto-debug report (GitHub issue #8).

1. shinqlx's stats listener died on every start with "zmq error:
   InvalidArgument": libzmq rejects an empty PLAIN password and the server
   never set zmq_stats_password. Death/kill events only arrive through that
   listener, so the plugin never saw a kill or a death in real play.
2. The post-game report fired at launch ("quake-client-never-appeared")
   because Quake detection trusted a single sighting of a short-lived process.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import unittest

from tests.test_v5_runtime import RuntimeHarness

ROOT = Path(__file__).resolve().parents[1]


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class StartScriptStatsListenerTests(unittest.TestCase):
    """Runs the real start_solo.sh against a stub dedicated server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.capture = base / "server_args.txt"
        runtime = self.home / ".local/share/quake-live-launcher/solo_runtime"
        self.runtime = runtime
        (runtime / "READY").parent.mkdir(parents=True, exist_ok=True)
        (runtime / "READY").write_text("")
        (runtime / ".venv/bin").mkdir(parents=True)
        (runtime / ".venv/bin/python").symlink_to(sys.executable)
        so = runtime / ".venv/lib/python3/site-packages/shinqlx/shinqlx.so"
        so.parent.mkdir(parents=True)
        so.write_bytes(b"\x7fELF")
        # Stub QLDS: records its arguments, writes the plugin handshake the
        # real plugin writes, and optionally prints the real listener failure.
        write_exec(runtime / "qlds/qzeroded.x64", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{self.capture}"
python3 - <<'PY'
import json, os, time
from pathlib import Path
p = Path(os.environ["HOME"]) / ".local/share/quake-live-launcher/solo_runtime/plugin_ready.json"
p.write_text(json.dumps({{"ready": True, "mode": "horde", "pid": os.getpid(), "time": time.time()}}))
PY
if [ -n "${{STUB_ZMQ_FAIL:-}}" ]; then
  echo "[shinqlx.log_exception] ERROR: OSError: zmq error: InvalidArgument"
fi
exec sleep 60
""")
        config = self.home / ".config/quake-live-launcher"
        config.mkdir(parents=True)
        (config / "solo_session.json").write_text(json.dumps({"mode": "horde", "map": "campgrounds", "game_dir": ""}))
        self.bin = base / "bin"
        write_exec(self.bin / "ss", "#!/usr/bin/env bash\necho 'UNCONN 0 0 127.0.0.1:27960 0.0.0.0:*'\n")

    def tearDown(self):
        pidfile = self.runtime / "server.pid"
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            except Exception:
                pass
        self.tmp.cleanup()

    def run_start(self, **extra):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}
        env.update(extra)
        return subprocess.run(["bash", str(ROOT / "solo_engine/start_solo.sh")], env=env,
                              capture_output=True, text=True, timeout=60)

    def server_args(self):
        return self.capture.read_text().splitlines()

    def test_server_gets_a_non_empty_random_stats_password(self):
        proc = self.run_start()
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        args = self.server_args()
        password = args[args.index("zmq_stats_password") + 1]
        self.assertRegex(password, r"^[0-9a-f]{32}$")
        self.assertLess(args.index("zmq_stats_password"), args.index("+map"))
        first = password
        subprocess.run(["bash", str(ROOT / "solo_engine/stop_solo.sh")], env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                       capture_output=True, timeout=30)
        self.assertEqual(self.run_start().returncode, 0)
        args = self.server_args()
        self.assertNotEqual(args[args.index("zmq_stats_password") + 1], first, "fresh password per launch")

    def test_dead_stats_listener_fails_startup_loudly(self):
        proc = self.run_start(STUB_ZMQ_FAIL="1")
        self.assertEqual(proc.returncode, 8, proc.stdout[-2000:])
        self.assertIn("stats listener failed to connect", proc.stdout)
        self.assertNotIn("HEALTH OK", proc.stdout)


class PluginGuardTests(unittest.TestCase):
    def setUp(self):
        self.harness = RuntimeHarness(methodName="runTest")
        self.harness.setUp()

    def tearDown(self):
        self.harness.tearDown()

    def test_plugin_refuses_to_load_without_stats_password(self):
        from tests.fake_minqlx import FakeServer, install_fake_minqlx
        config = self.harness.home / ".config/quake-live-launcher"
        config.mkdir(parents=True, exist_ok=True)
        (config / "solo_session.json").write_text(json.dumps({"mode": "horde", "map": "campgrounds"}))
        server = FakeServer()
        server.cvars["zmq_stats_password"] = ""
        install_fake_minqlx(server)
        module = importlib.import_module("minqlx-plugins.solo_arcade")
        with self.assertRaisesRegex(RuntimeError, "zmq_stats_password"):
            module.solo_arcade()


class QuakeDetectionTests(unittest.TestCase):
    def setUp(self):
        import launcher
        self.launcher = launcher

    def run_wait(self, states, want, **kw):
        clock = {"t": 0.0}
        seq = list(states)

        def running():
            return seq.pop(0) if seq else states[-1]

        def sleep(seconds):
            clock["t"] += seconds

        return self.launcher.wait_for_quake_state(want, running=running, sleep=sleep, clock=lambda: clock["t"], **kw), clock["t"]

    def test_a_launch_blip_is_not_a_running_game(self):
        ok, elapsed = self.run_wait([False, True, False] + [False] * 400, True, timeout=180)
        self.assertFalse(ok)
        self.assertGreaterEqual(elapsed, 180)

    def test_game_counts_as_started_after_staying_up(self):
        ok, elapsed = self.run_wait([False] * 10 + [True] * 20, True, timeout=180)
        self.assertTrue(ok)
        self.assertGreaterEqual(elapsed, 14)

    def test_exit_needs_to_hold_so_a_flicker_is_not_a_quit(self):
        ok, elapsed = self.run_wait([True, False, True] + [True] * 30 + [False] * 10, False)
        self.assertTrue(ok)
        self.assertGreaterEqual(elapsed, 33)

    def test_helpers_mentioning_the_game_folder_are_not_the_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "pgrep"
            lines = [
                "101 /usr/bin/python3 /home/u/.local/share/quake-live-launcher/solo_engine/sync_maps.py /home/u/Steam/steamapps/common/Quake Live /x",
                "102 /home/u/.local/share/quake-live-launcher/solo_runtime/qlds/qzeroded.x64 +set fs_homepath /x",
                "103 python3 launcher.py --solo-exit-debug {\"game_dir\": \"/Steam/steamapps/common/Quake Live\"}",
            ]
            write_exec(fake, "#!/usr/bin/env bash\nprintf '%s\\n' " + " ".join(json.dumps(l) for l in lines) + "\n")
            old_path = os.environ["PATH"]
            os.environ["PATH"] = f"{tmp}:{old_path}"
            try:
                self.assertFalse(self.launcher.quake_running())
                lines.append("104 Z:\\\\home\\\\u\\\\Steam\\\\steamapps\\\\common\\\\Quake Live\\\\quakelive_steam.exe")
                write_exec(fake, "#!/usr/bin/env bash\nprintf '%s\\n' " + " ".join(json.dumps(l) for l in lines) + "\n")
                self.assertTrue(self.launcher.quake_running())
            finally:
                os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
