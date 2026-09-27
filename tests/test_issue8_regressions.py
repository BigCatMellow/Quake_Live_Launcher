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
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.test_v5_runtime import RuntimeHarness

ROOT = Path(__file__).resolve().parents[1]


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


STUB_SERVER = r"""
import os, signal, socket, sys, time
port = int(sys.argv[sys.argv.index("net_port") + 1])
if os.environ.get("STUB_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if not os.environ.get("STUB_NO_BIND"):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.bind(("127.0.0.1", port))
    except OSError:
        # What qzeroded does: complain, then take the next port.
        print("ERROR: UDP_OpenSocket: bind: Address already in use", flush=True)
        udp.bind(("127.0.0.1", port + 1))
time.sleep(120)
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False
    return "\nState:\tZ" not in state


class StubServerCase(unittest.TestCase):
    """Runs the real start_solo.sh / stop_solo.sh against a stub dedicated server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.capture = base / "server_args.txt"
        self.port = free_port()
        self.spawned: list[int] = []
        runtime = self.home / ".local/share/quake-live-launcher/solo_runtime"
        self.runtime = runtime
        (runtime / "READY").parent.mkdir(parents=True, exist_ok=True)
        (runtime / "READY").write_text("")
        (runtime / ".venv/bin").mkdir(parents=True)
        (runtime / ".venv/bin/python").symlink_to(sys.executable)
        so = runtime / ".venv/lib/python3/site-packages/shinqlx/shinqlx.so"
        so.parent.mkdir(parents=True)
        so.write_bytes(b"\x7fELF")
        # Stub QLDS: records its arguments, writes the plugin handshake with its
        # own PID (as the real plugin does from inside qzeroded), optionally
        # prints real failure lines, then becomes a process that binds the game
        # port and still looks like qzeroded.x64 (argv[0]) to the scripts.
        write_exec(runtime / "qlds/qzeroded.x64", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{self.capture}"
STUB_PID=$$ python3 - <<'PY'
import json, os, time
from pathlib import Path
p = Path(os.environ["HOME"]) / ".local/share/quake-live-launcher/solo_runtime/plugin_ready.json"
p.write_text(json.dumps({{"ready": True, "mode": "horde", "pid": int(os.environ["STUB_PID"]), "time": time.time()}}))
PY
if [ -n "${{STUB_ZMQ_FAIL:-}}" ]; then
  echo "[shinqlx.log_exception] ERROR: OSError: zmq error: InvalidArgument"
fi
exec -a "$0" python3 -c {shlex.quote(STUB_SERVER)} "$@"
""")
        config = self.home / ".config/quake-live-launcher"
        config.mkdir(parents=True)
        (config / "solo_session.json").write_text(json.dumps({"mode": "horde", "map": "campgrounds", "game_dir": ""}))

    def tearDown(self):
        pidfile = self.runtime / "server.pid"
        pids = list(self.spawned)
        if pidfile.exists():
            try:
                pids.append(int(pidfile.read_text().strip()))
            except ValueError:
                pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        self.tmp.cleanup()

    def env(self, **extra):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "QLL_SOLO_PORT": str(self.port),
               "QLL_STOP_TERM_WAIT": "1"}
        env.update(extra)
        return env

    def run_start(self, **extra):
        proc = subprocess.run(["bash", str(ROOT / "solo_engine/start_solo.sh")], env=self.env(**extra),
                              capture_output=True, text=True, timeout=90)
        pid = self.server_pid()
        if pid:
            self.spawned.append(pid)
        return proc

    def run_stop(self, **extra):
        return subprocess.run(["bash", str(ROOT / "solo_engine/stop_solo.sh")], env=self.env(**extra),
                              capture_output=True, text=True, timeout=60)

    def server_pid(self):
        try:
            return int((self.runtime / "server.pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def owns_port(self, pid):
        return subprocess.run([sys.executable, str(ROOT / "solo_engine/solo_ports.py"), "owns", str(pid), str(self.port)]).returncode == 0

    def server_args(self):
        return self.capture.read_text().splitlines()



class StartScriptStatsListenerTests(StubServerCase):
    def test_server_gets_a_non_empty_random_stats_password(self):
        proc = self.run_start()
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
        args = self.server_args()
        password = args[args.index("zmq_stats_password") + 1]
        self.assertRegex(password, r"^[0-9a-f]{32}$")
        self.assertLess(args.index("zmq_stats_password"), args.index("+map"))
        first = password
        self.run_stop()
        self.assertEqual(self.run_start().returncode, 0)
        args = self.server_args()
        self.assertNotEqual(args[args.index("zmq_stats_password") + 1], first, "fresh password per launch")

    def test_dead_stats_listener_fails_startup_loudly(self):
        proc = self.run_start(STUB_ZMQ_FAIL="1")
        self.assertEqual(proc.returncode, 8, proc.stdout[-2000:])
        self.assertIn("stats listener failed to connect", proc.stdout)
        self.assertNotIn("HEALTH OK", proc.stdout)


class StaleServerTests(StubServerCase):
    """2026-09-26 report: the previous server ignored SIGTERM and kept the
    port, the new one fell back to port+1, and the health check passed on the
    OLD server's socket, so the game joined the old (training-flag) server."""

    def test_server_that_ignores_sigterm_is_killed_and_new_one_owns_the_port(self):
        self.assertEqual(self.run_start(STUB_IGNORE_TERM="1").returncode, 0)
        old = self.server_pid()
        self.assertTrue(self.owns_port(old))
        proc = self.run_start()
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:])
        new = self.server_pid()
        self.assertNotEqual(old, new)
        self.assertFalse(alive(old), "old server must be gone")
        self.assertTrue(self.owns_port(new), "the NEW server must own the game port")

    def test_stale_server_missing_from_the_pid_file_is_found_by_port(self):
        self.assertEqual(self.run_start(STUB_IGNORE_TERM="1").returncode, 0)
        old = self.server_pid()
        (self.runtime / "server.pid").unlink()
        proc = self.run_start()
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:])
        self.assertFalse(alive(old))
        self.assertTrue(self.owns_port(self.server_pid()))

    def test_stop_sweeps_a_server_that_fell_back_to_the_next_port(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", self.port))
        # Launch the stub directly, as an old launcher would have: it wanted
        # PORT, found it taken, and bound PORT+1.
        stub = subprocess.Popen([str(self.runtime / "qlds/qzeroded.x64"), "+set", "net_port", str(self.port)],
                                env=self.env(STUB_IGNORE_TERM="1"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(stub.pid)
        try:
            for _ in range(50):
                if subprocess.run([sys.executable, str(ROOT / "solo_engine/solo_ports.py"), "owns", str(stub.pid), str(self.port + 1)]).returncode == 0:
                    break
                time.sleep(0.1)
            blocker.close()
            proc = self.run_stop()
            self.assertEqual(proc.returncode, 0, proc.stdout)
            stub.wait(timeout=10)
            self.assertIn("SIGKILL", proc.stdout)
        finally:
            blocker.close()

    def test_foreign_program_on_the_port_is_left_alone_and_reported(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", self.port))
        try:
            proc = self.run_start()
        finally:
            blocker.close()
        self.assertEqual(proc.returncode, 9, proc.stdout[-2000:])
        self.assertIn(f"{os.getpid()} foreign", proc.stdout)
        self.assertIn("held by another program", proc.stdout)
        self.assertFalse(self.capture.exists(), "no server may be launched onto a taken port")

    def test_stop_never_kills_a_non_solo_pid_from_a_stale_pid_file(self):
        other = subprocess.Popen(["sleep", "30"])
        try:
            (self.runtime / "server.pid").write_text(str(other.pid))
            proc = self.run_stop()
            self.assertIn("not a Solo server", proc.stdout)
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait()

    def test_server_that_never_binds_its_port_is_not_healthy(self):
        proc = self.run_start(STUB_NO_BIND="1", QLL_START_ATTEMPTS="3")
        self.assertEqual(proc.returncode, 7, proc.stdout[-2000:])
        self.assertIn("socket_ok=0", proc.stdout)
        self.assertNotIn("HEALTH OK", proc.stdout)
        self.assertIsNone(self.server_pid(), "a server that failed health is stopped")

    def test_handshake_from_another_process_is_not_health(self):
        # plugin_ready.json from some other PID (an old server) does not count.
        script = self.runtime / "qlds/qzeroded.x64"
        script.write_text(script.read_text().replace('int(os.environ["STUB_PID"])', "1"))
        proc = self.run_start(QLL_START_ATTEMPTS="4")
        self.assertEqual(proc.returncode, 7, proc.stdout[-2000:])
        self.assertIn("plugin_ok=0", proc.stdout)

    def test_engine_bind_error_fails_with_exit_9(self):
        # The port was free at the pre-launch check but the engine lost the
        # race for it: its own log line is the signal.
        script = self.runtime / "qlds/qzeroded.x64"
        script.write_text(script.read_text().replace(
            'if [ -n "${STUB_ZMQ_FAIL:-}" ]',
            'echo "zmq PUB socket error, bind failed: tcp://127.0.0.1:$PPID"\nif [ -n "${STUB_ZMQ_FAIL:-}" ]'))
        proc = self.run_start()
        self.assertEqual(proc.returncode, 9, proc.stdout[-2000:])
        self.assertIn("could not bind port", proc.stdout)
        self.assertIsNone(self.server_pid())


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
