"""Tests for setup_solo_engine.sh skipping work that is already done.

The real script runs against a temporary HOME with a fake runtime: a stub
SteamCMD that records calls (and "installs" QLDS), a stub venv python that
records pip calls, a stub self-test, and a stub dpkg-query. PATH is restricted
so no real Rust toolchain or network tool is reachable.
"""
from __future__ import annotations

import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class SetupScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.calls = base / "calls.log"
        self.calls.write_text("")
        self.engine = base / "engine"
        self.engine.mkdir()
        shutil.copy(ROOT / "solo_engine/setup_solo_engine.sh", self.engine / "setup_solo_engine.sh")
        shutil.copytree(ROOT / "solo_engine/plugins", self.engine / "plugins")
        shutil.copy(ROOT / "solo_engine/sync_maps.py", self.engine / "sync_maps.py")
        write_exec(self.engine / "self_test.sh", f'#!/usr/bin/env bash\necho "self_test" >> "{self.calls}"\nexit 0\n')
        self.runtime = self.home / ".local/share/quake-live-launcher/solo_runtime"
        # Stub SteamCMD: records its arguments and "installs" QLDS on app_update.
        write_exec(self.runtime / "steamcmd/steamcmd.sh", f"""#!/usr/bin/env bash
echo "steamcmd $*" >> "{self.calls}"
case "$*" in *app_update*)
  dir=""; prev=""
  for a in "$@"; do [ "$prev" = "+force_install_dir" ] && dir="$a"; prev="$a"; done
  mkdir -p "$dir/steamapps"; printf '#!/bin/sh\\n' > "$dir/qzeroded.x64"; chmod +x "$dir/qzeroded.x64"
  printf '"AppState"\\n{{\\n\\t"buildid"\\t\\t"1234567"\\n}}\\n' > "$dir/steamapps/appmanifest_349090.acf";;
esac
exit 0
""")
        self.bin = base / "bin"
        write_exec(self.bin / "dpkg-query", "#!/usr/bin/env bash\nprintf 'install ok installed'\n")
        write_exec(self.bin / "curl", f'#!/usr/bin/env bash\necho "curl $*" >> "{self.calls}"\nexit 1\n')

    def tearDown(self):
        self.tmp.cleanup()

    def install_qlds(self):
        qlds = self.runtime / "qlds"
        write_exec(qlds / "qzeroded.x64", "#!/bin/sh\n")
        (qlds / "steamapps").mkdir(parents=True, exist_ok=True)
        (qlds / "steamapps/appmanifest_349090.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"7654321"\n}\n')

    def install_shinqlx(self):
        venv = self.runtime / ".venv"
        so = venv / "lib/python3/site-packages/shinqlx/_shinqlx.so"
        so.parent.mkdir(parents=True, exist_ok=True)
        so.write_bytes(b"\x7fELF")
        write_exec(venv / "bin/python", f"""#!/usr/bin/env bash
echo "venv-python $*" >> "{self.calls}"
case "$*" in
  "-m pip show shinqlx") echo "Name: shinqlx"; exit 0;;
  "--version") echo "Python 3.11.0"; exit 0;;
esac
exit 0
""")

    def env(self):
        return {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "TERM": "dumb"}

    def run_setup(self, *args):
        return subprocess.run(["bash", str(self.engine / "setup_solo_engine.sh"), *args], env=self.env(),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def calls_made(self):
        return self.calls.read_text()

    def ready(self):
        return (self.runtime / "READY").exists() and (self.runtime / "SELF_TEST_OK").exists()

    def test_everything_installed_skips_download_and_build(self):
        self.install_qlds(); self.install_shinqlx()
        proc = self.run_setup()
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])
        calls = self.calls_made()
        self.assertNotIn("steamcmd", calls)
        self.assertNotIn("pip install", calls)
        self.assertNotIn("curl", calls)
        self.assertIn("self_test", calls)
        self.assertTrue(self.ready())
        self.assertIn("already installed; skipping download", proc.stdout)
        self.assertIn("buildid 7654321", proc.stdout)
        self.assertIn("shinqlx already built; skipping Rust toolchain and compile", proc.stdout)
        self.assertTrue((self.runtime / "qlds/minqlx-plugins/spawn_director.py").exists(), "plugins refreshed")
        self.assertTrue((self.runtime / "home/baseq3/server.cfg").exists(), "config refreshed")

    def test_missing_server_is_installed_but_shinqlx_is_not_rebuilt(self):
        self.install_shinqlx()
        proc = self.run_setup()
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])
        calls = self.calls_made()
        self.assertIn("+app_update 349090 validate", calls)
        self.assertNotIn("pip install", calls)
        self.assertTrue(self.ready())

    def test_missing_shinqlx_builds_without_touching_the_server(self):
        self.install_qlds()
        venv_python = self.runtime / ".venv/bin/python"
        self.install_shinqlx()
        shutil.rmtree(self.runtime / ".venv/lib")  # venv exists, but no built shinqlx
        proc = self.run_setup()
        calls = self.calls_made()
        self.assertNotIn("steamcmd", calls)
        self.assertIn("pip install --upgrade pip wheel", calls, "build path started")
        # No Rust toolchain is reachable here, so the build stops with the clear rustup message.
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("rustup could not be downloaded", proc.stdout)
        self.assertFalse(self.ready())
        self.assertTrue(venv_python.exists())

    def test_repair_flag_revalidates_server_and_rebuilds(self):
        self.install_qlds(); self.install_shinqlx()
        proc = self.run_setup("--repair")
        calls = self.calls_made()
        self.assertIn("+app_update 349090 validate", calls)
        self.assertIn("pip install --upgrade pip wheel", calls)
        self.assertNotEqual(proc.returncode, 0)  # stops at the (unreachable) Rust toolchain

    def test_unknown_option_is_rejected(self):
        proc = self.run_setup("--bogus")
        self.assertEqual(proc.returncode, 64)

    def run_in_terminal(self, keys: str):
        """Run setup attached to a pseudo-terminal and type `keys` at the prompt."""
        pid, fd = pty.fork()
        if pid == 0:
            os.execve("/bin/bash", ["bash", str(self.engine / "setup_solo_engine.sh")], self.env())
        output = b""
        sent = False
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            if not sent and b"Choice [Enter/r]" in output:
                os.write(fd, keys.encode())
                sent = True
            if b"Press Enter to close this terminal" in output or b"Full setup log" in output and b"ERROR" in output:
                os.write(fd, b"\n")
        os.waitpid(pid, 0)
        return output.decode(errors="replace")

    def test_terminal_prompt_defaults_to_quick_check(self):
        self.install_qlds(); self.install_shinqlx()
        out = self.run_in_terminal("\n")
        self.assertIn("already installed", out)
        self.assertNotIn("steamcmd", self.calls_made())
        self.assertTrue(self.ready())

    def test_terminal_prompt_r_runs_full_repair(self):
        self.install_qlds(); self.install_shinqlx()
        self.run_in_terminal("r\n")
        self.assertIn("+app_update 349090 validate", self.calls_made())


if __name__ == "__main__":
    unittest.main()
