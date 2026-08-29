from __future__ import annotations

import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import launcher


class GithubDebugUploadTests(unittest.TestCase):
    def test_public_report_scrubs_home_host_and_github_tokens(self):
        sample = (
            f"path={Path.home()}/quake host={socket.gethostname()} "
            "token=ghp_ABC123 github_pat_SECRET_value "
            "Authorization: Bearer SUPERSECRET"
        )
        scrubbed = launcher._scrub_public_debug_text(sample)
        self.assertNotIn(str(Path.home()), scrubbed)
        self.assertNotIn(socket.gethostname(), scrubbed)
        self.assertNotIn("ghp_ABC123", scrubbed)
        self.assertNotIn("github_pat_SECRET_value", scrubbed)
        self.assertNotIn("SUPERSECRET", scrubbed)

    def test_authenticated_owner_creates_issue(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "diagnostics-github.txt"
            report.write_text("diagnostic body", encoding="utf-8")
            status = root / "status.json"
            old_report = launcher.write_solo_exit_debug_report
            old_load = launcher.load_json
            old_which = launcher.shutil.which
            old_run = launcher.subprocess.run
            old_status = launcher.SOLO_GITHUB_DEBUG_STATUS_FILE
            calls = []
            try:
                launcher.write_solo_exit_debug_report = lambda *args, **kwargs: report
                launcher.load_json = lambda *args, **kwargs: {"mode": "horde", "map": "campgrounds"}
                launcher.shutil.which = lambda name: "/usr/bin/gh" if name == "gh" else None

                def fake_run(args, **kwargs):
                    calls.append(args)
                    if args[1:3] == ["auth", "status"]:
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if args[1:3] == ["api", "user"]:
                        return SimpleNamespace(returncode=0, stdout="BigCatMellow\n", stderr="")
                    return SimpleNamespace(
                        returncode=0,
                        stdout="https://github.com/BigCatMellow/Quake_Live_Launcher/issues/99\n",
                        stderr="",
                    )

                launcher.subprocess.run = fake_run
                launcher.SOLO_GITHUB_DEBUG_STATUS_FILE = status
                result = launcher.upload_solo_exit_debug(reason="test")
                self.assertTrue(result["uploaded"])
                self.assertTrue(result["url"].endswith("/99"))
                self.assertEqual(len(calls), 3)
                self.assertTrue(status.exists())
            finally:
                launcher.write_solo_exit_debug_report = old_report
                launcher.load_json = old_load
                launcher.shutil.which = old_which
                launcher.subprocess.run = old_run
                launcher.SOLO_GITHUB_DEBUG_STATUS_FILE = old_status

    def test_non_owner_login_never_posts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "diagnostics-github.txt"
            report.write_text("diagnostic body", encoding="utf-8")
            old_report = launcher.write_solo_exit_debug_report
            old_load = launcher.load_json
            old_which = launcher.shutil.which
            old_run = launcher.subprocess.run
            old_status = launcher.SOLO_GITHUB_DEBUG_STATUS_FILE
            calls = []
            try:
                launcher.write_solo_exit_debug_report = lambda *args, **kwargs: report
                launcher.load_json = lambda *args, **kwargs: {"mode": "horde", "map": "campgrounds"}
                launcher.shutil.which = lambda name: "/usr/bin/gh" if name == "gh" else None

                def fake_run(args, **kwargs):
                    calls.append(args)
                    if args[1:3] == ["auth", "status"]:
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if args[1:3] == ["api", "user"]:
                        return SimpleNamespace(returncode=0, stdout="SomeoneElse\n", stderr="")
                    raise AssertionError("issue creation must not be attempted for another GitHub user")

                launcher.subprocess.run = fake_run
                launcher.SOLO_GITHUB_DEBUG_STATUS_FILE = root / "status.json"
                result = launcher.upload_solo_exit_debug(reason="test")
                self.assertFalse(result["uploaded"])
                self.assertIn("restricted", result["error"])
                self.assertEqual(len(calls), 2)
            finally:
                launcher.write_solo_exit_debug_report = old_report
                launcher.load_json = old_load
                launcher.shutil.which = old_which
                launcher.subprocess.run = old_run
                launcher.SOLO_GITHUB_DEBUG_STATUS_FILE = old_status


if __name__ == "__main__":
    unittest.main()
