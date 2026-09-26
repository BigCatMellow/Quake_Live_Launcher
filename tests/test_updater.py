"""Installer + auto-updater tests against a local HTTP 'release channel'.

The artifacts are built by tools/build_release.py (the same code CI runs) and
served over HTTP; the real generated installer and the real installed launcher
shortcut are executed with HOME pointed at a temporary directory.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_release = load_module("qll_build_release", ROOT / "tools/build_release.py")
qll_update = load_module("qll_update_under_test", ROOT / "qll_update.py")


class ReleaseServer:
    """Serves a directory over HTTP and records every path requested."""

    def __init__(self, directory: Path):
        self.requests = []
        server_self = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server_self.requests.append(self.path.split("?")[0])
                return super().do_GET()

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class DecideTests(unittest.TestCase):
    remote = {"commit": "b" * 40, "app_version": "5.0-new", "archive": "a.zip", "sha256": "x"}

    def test_decisions(self):
        decide = qll_update.decide
        self.assertEqual(decide({"source": "release", "commit": "a" * 40, "app_version": "old"}, self.remote)[0], True)
        self.assertEqual(decide({"source": "release", "commit": "b" * 40}, self.remote)[0], False)
        self.assertEqual(decide({"source": "local", "commit": "a" * 40}, self.remote)[0], False)
        self.assertEqual(decide({}, self.remote)[0], True, "pre-updater installs move onto the release channel")
        self.assertEqual(decide({"source": "release"}, {})[0], False)


class SafeExtractTests(unittest.TestCase):
    def make_zip(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                zf.writestr(info, data)
        return buffer.getvalue()

    def extract(self, entries):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.zip"
            archive.write_bytes(self.make_zip(entries))
            dest = Path(tmp) / "out"
            dest.mkdir()
            qll_update.safe_extract(archive, dest)
            return sorted(str(p.relative_to(dest)) for p in dest.rglob("*"))

    def test_rejects_traversal_absolute_and_symlinks(self):
        for bad in ("../evil.sh", "a/../../evil.sh", "/etc/evil"):
            with self.subTest(bad=bad), self.assertRaises(qll_update.UpdateError):
                self.extract([(bad, b"x", 0o100644)])
        with self.assertRaises(qll_update.UpdateError):
            self.extract([("link", b"/etc/passwd", 0o120777)])

    def test_keeps_executable_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.zip"
            archive.write_bytes(self.make_zip([("run.sh", b"#!/bin/sh\n", 0o100755), ("x.txt", b"x", 0o100644)]))
            qll_update.safe_extract(archive, Path(tmp))
            self.assertTrue(os.access(Path(tmp) / "run.sh", os.X_OK))
            self.assertFalse(os.access(Path(tmp) / "x.txt", os.X_OK))


class BuildReleaseTests(unittest.TestCase):
    def test_artifacts_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            manifest = build_release.build(out, commit="c" * 40, built_at="2026-09-26T00:00:00Z")
            archive = out / manifest["archive"]
            self.assertEqual(manifest["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertIn(manifest["sha256"], (out / f"{manifest['archive']}.sha256").read_text())
            self.assertEqual(json.loads((out / "version.json").read_text()), manifest)
            with zipfile.ZipFile(archive) as zf:
                names = {n for n in zf.namelist()}
                expected = {p.as_posix() for p in build_release.shipped_files()} | {"release.json"}
                self.assertEqual(names, expected)
                self.assertIn("qll_update.py", names)
                release = json.loads(zf.read("release.json"))
                self.assertEqual(release["commit"], "c" * 40)
                self.assertEqual(release["source"], "release")
                for exe in ("install.sh", "uninstall.sh", "run.sh", "solo_engine/start_solo.sh", "solo_engine/run_director_probe.sh"):
                    self.assertTrue((zf.getinfo(exe).external_attr >> 16) & 0o111, exe)
            installer = out / build_release.INSTALLER_NAME
            self.assertTrue(os.access(installer, os.X_OK))
            self.assertEqual(subprocess.run(["bash", "-n", str(installer)]).returncode, 0)
            self.assertIn((ROOT / "qll_update.py").read_text().strip().splitlines()[-1], installer.read_text())


class InstallAndUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.dist = base / "dist"
        self.server = ReleaseServer(self.dist)
        self.stub_out = base / "launched.json"
        self.stub = base / "stub_launcher.py"
        self.stub.write_text(
            "import json, os, sys\n"
            f"open({str(self.stub_out)!r}, 'w').write(json.dumps({{'args': sys.argv[1:], "
            "'update_done': os.environ.get('QLL_UPDATE_DONE')}))\n"
        )

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def publish(self, commit, *, mutate=None):
        if self.dist.exists():
            shutil.rmtree(self.dist)
        manifest = build_release.build(self.dist, commit=commit, built_at="2026-09-26T00:00:00Z")
        if mutate:
            mutate(manifest)
        return manifest

    def env(self, **extra):
        env = dict(os.environ, HOME=str(self.home), QLL_RELEASE_BASE=self.server.url, QLL_UPDATER_NO_GUI="1", QLL_ALLOW_ROOT="1",
                   QLL_UPDATER_EXEC=json.dumps([sys.executable, str(self.stub)]))
        env.pop("QLL_UPDATE_DONE", None)
        env.pop("QLL_NO_UPDATE", None)
        env.update(extra)
        return env

    def installed(self):
        return json.loads((self.home / ".local/share/quake-live-launcher/version.json").read_text())

    def run_installer(self, **extra):
        return subprocess.run(["bash", str(self.dist / build_release.INSTALLER_NAME), "--yes"],
                              env=self.env(**extra), capture_output=True, text=True, timeout=120)

    def run_shortcut(self, *args, **extra):
        if self.stub_out.exists():
            self.stub_out.unlink()
        started = time.time()
        proc = subprocess.run([str(self.home / ".local/bin/quake-live-launcher"), *args],
                              env=self.env(**extra), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.stub_out.exists(), "launcher was not started: " + proc.stdout + proc.stderr)
        return json.loads(self.stub_out.read_text()), time.time() - started, proc

    def test_standalone_installer_installs_latest_release(self):
        self.publish("a" * 40)
        proc = self.run_installer()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        root = self.home / ".local/share/quake-live-launcher"
        for name in ("launcher.py", "launcher_gui.py", "qll_update.py", "solo_engine/start_solo.sh"):
            self.assertTrue((root / name).exists(), name)
        version = self.installed()
        self.assertEqual(version["commit"], "a" * 40)
        self.assertEqual(version["source"], "release")
        wrapper = (self.home / ".local/bin/quake-live-launcher").read_text()
        self.assertIn("qll_update.py\" --launch", wrapper)
        self.assertTrue((self.home / ".local/share/applications/quake-live-launcher.desktop").exists())
        self.assertIn("Installed Quake Live Launcher", proc.stdout)

    def test_launch_updates_to_new_release_then_starts_it(self):
        self.publish("a" * 40)
        self.assertEqual(self.run_installer().returncode, 0)
        self.publish("b" * 40)
        launched, _, proc = self.run_shortcut("--some-arg", "value")
        self.assertEqual(self.installed()["commit"], "b" * 40)
        self.assertEqual(launched["args"], ["--some-arg", "value"])
        self.assertEqual(launched["update_done"], "1")
        self.assertIn("Updated to", proc.stdout)
        # Next launch: already current, so only the manifest is fetched.
        self.server.requests.clear()
        launched, _, _ = self.run_shortcut()
        self.assertEqual(self.server.requests, ["/version.json"])
        self.assertEqual(launched["args"], [])

    def test_offline_starts_installed_version_quickly(self):
        self.publish("a" * 40)
        self.assertEqual(self.run_installer().returncode, 0)
        launched, elapsed, _ = self.run_shortcut(QLL_RELEASE_BASE="http://127.0.0.1:9")
        self.assertLess(elapsed, 10)
        self.assertEqual(self.installed()["commit"], "a" * 40)
        self.assertIsNone(launched["update_done"])

    def test_bad_checksum_keeps_installed_version(self):
        self.publish("a" * 40)
        self.assertEqual(self.run_installer().returncode, 0)

        def corrupt(manifest):
            manifest["sha256"] = "0" * 64
            (self.dist / "version.json").write_text(json.dumps(manifest))

        self.publish("b" * 40, mutate=corrupt)
        launched, _, proc = self.run_shortcut()
        self.assertEqual(self.installed()["commit"], "a" * 40)
        self.assertIn("checksum mismatch", proc.stdout)
        self.assertEqual(launched["update_done"], "1")

    def test_release_without_updater_is_refused(self):
        self.publish("a" * 40)
        self.assertEqual(self.run_installer().returncode, 0)

        def strip_updater(manifest):
            archive = self.dist / manifest["archive"]
            with zipfile.ZipFile(archive) as zf:
                entries = [(i, zf.read(i.filename)) for i in zf.infolist() if i.filename != "qll_update.py"]
            with zipfile.ZipFile(archive, "w") as zf:
                for info, data in entries:
                    zf.writestr(info, data)
            manifest["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            (self.dist / "version.json").write_text(json.dumps(manifest))

        self.publish("b" * 40, mutate=strip_updater)
        _, _, proc = self.run_shortcut()
        self.assertEqual(self.installed()["commit"], "a" * 40)
        self.assertIn("predates the auto-updater", proc.stdout)

    def test_local_source_install_is_never_overwritten(self):
        self.publish("b" * 40)
        proc = subprocess.run(["bash", str(ROOT / "install.sh")], env=self.env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.installed()["source"], "local")
        self.server.requests.clear()
        launched, _, _ = self.run_shortcut()
        self.assertEqual(self.installed()["source"], "local")
        self.assertNotIn("/" + build_release.ARCHIVE_NAME, self.server.requests)

    def test_auto_update_can_be_turned_off(self):
        self.publish("a" * 40)
        self.assertEqual(self.run_installer().returncode, 0)
        settings = self.home / ".config/quake-live-launcher/update.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"auto_update": False}))
        self.publish("b" * 40)
        self.server.requests.clear()
        self.run_shortcut()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.installed()["commit"], "a" * 40)
        settings.unlink()
        self.run_shortcut(QLL_NO_UPDATE="1")
        self.assertEqual(self.installed()["commit"], "a" * 40)

    def test_installer_explains_a_release_without_manifest(self):
        self.dist.mkdir(parents=True, exist_ok=True)  # empty channel: version.json 404s
        build_release.build_installer(self.dist)
        proc = self.run_installer()
        self.assertEqual(proc.returncode, 3)
        self.assertIn("no install manifest", proc.stdout)
        self.assertNotIn("internet connection", proc.stdout)

    def test_installer_refuses_root(self):
        self.publish("a" * 40)
        if os.geteuid() != 0:
            self.skipTest("root guard can only be exercised as root")
        env = self.env()
        env.pop("QLL_ALLOW_ROOT")
        proc = subprocess.run(["bash", str(self.dist / build_release.INSTALLER_NAME), "--yes"], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("normal user", proc.stderr)
        self.assertFalse((self.home / ".local/share/quake-live-launcher").exists())


if __name__ == "__main__":
    unittest.main()
