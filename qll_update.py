#!/usr/bin/env python3
"""Quake Live Launcher installer and auto-updater (Python standard library only).

One file, three jobs:

  qll_update.py --install [--yes]   fresh install of the latest release (the
                                    standalone installer runs this)
  qll_update.py --launch [ARGS...]  what the launcher shortcut runs: check the
                                    release channel, update if a newer build is
                                    published, then start the launcher
  qll_update.py --check             print installed vs published version

Release channel: the rolling GitHub prerelease ``v5-alpha-latest``, which CI
publishes only after the full test/install/archive checks pass. It carries:

  version.json                          app_version, commit, built_at, sha256
  Quake_Live_Launcher_v5-alpha.zip      the install archive (contains release.json)

Update rules:
  * a release install updates whenever the published commit differs;
  * a local install (``bash install.sh`` from a source checkout) is never
    overwritten automatically - that is a developer's working copy;
  * no network / GitHub down / anything unexpected -> start the installed
    version unchanged; the launcher must never fail to open because of this;
  * opt out with ``{"auto_update": false}`` in
    ~/.config/quake-live-launcher/update.json or QLL_NO_UPDATE=1.

Downloads are verified against the manifest's SHA-256 before anything is
touched, and archive paths are checked so nothing can be written outside the
staging directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPO = "BigCatMellow/Quake_Live_Launcher"
CHANNEL_TAG = "v5-alpha-latest"
DEFAULT_RELEASE_BASE = f"https://github.com/{REPO}/releases/download/{CHANNEL_TAG}"
MANIFEST_NAME = "version.json"
USER_AGENT = "QuakeLiveLauncher-Updater/1"
CHECK_TIMEOUT = 4.0
DOWNLOAD_TIMEOUT = 60.0
REQUIRED_FILES = ("install.sh", "launcher.py", "launcher_gui.py", "qll_update.py")


class UpdateError(RuntimeError):
    pass


# ------------------------------------------------------------------ paths
def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def install_dir() -> Path:
    return home() / ".local/share/quake-live-launcher"


def version_file() -> Path:
    return install_dir() / "version.json"


def settings_file() -> Path:
    return home() / ".config/quake-live-launcher/update.json"


def log_file() -> Path:
    return install_dir() / "logs/updater.log"


def release_base() -> str:
    return os.environ.get("QLL_RELEASE_BASE", DEFAULT_RELEASE_BASE).rstrip("/")


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def say(message: str) -> None:
    print(f"[quake-live-launcher] {message}", flush=True)
    log(message)


# ------------------------------------------------------------- manifests
def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def installed_version() -> dict:
    return _read_json(version_file())


def settings() -> dict:
    data = {"auto_update": True}
    data.update(_read_json(settings_file()))
    return data


def fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_manifest(timeout: float = CHECK_TIMEOUT) -> dict:
    # A query string defeats stale CDN caches of the rolling release asset.
    url = f"{release_base()}/{MANIFEST_NAME}?t={int(time.time())}"
    data = json.loads(fetch(url, timeout).decode("utf-8"))
    for key in ("commit", "archive", "sha256", "app_version"):
        if not isinstance(data, dict) or not data.get(key):
            raise UpdateError(f"release manifest is missing {key!r}")
    if "/" in str(data["archive"]) or "\\" in str(data["archive"]):
        raise UpdateError("release manifest archive name is not a plain file name")
    return data


def decide(installed: dict, remote: dict) -> tuple[bool, str]:
    """Return (should_update, reason)."""
    if not remote:
        return False, "no release information"
    if installed.get("source") == "local":
        return False, "local source install; not overwritten automatically"
    if not installed:
        return True, f"installed version unknown (pre-updater install) -> {remote['app_version']}"
    if installed.get("commit") == remote.get("commit"):
        return False, f"up to date ({installed.get('app_version')})"
    return True, f"{installed.get('app_version', '?')} -> {remote['app_version']}"


# ------------------------------------------------------ download/install
def download_archive(manifest: dict, dest_dir: Path, timeout: float = DOWNLOAD_TIMEOUT) -> Path:
    data = fetch(f"{release_base()}/{manifest['archive']}?t={int(time.time())}", timeout)
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != str(manifest["sha256"]).lower():
        raise UpdateError(f"download checksum mismatch (expected {manifest['sha256']}, got {digest})")
    path = Path(dest_dir) / str(manifest["archive"])
    path.write_bytes(data)
    return path


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract a zip, refusing absolute paths, '..' and symlinks; keep exec bits."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or info.filename.startswith(("/", "\\")):
                raise UpdateError(f"unsafe path in archive: {info.filename}")
            mode = (info.external_attr >> 16) & 0o177777
            if stat.S_ISLNK(mode):
                raise UpdateError(f"symlink in archive: {info.filename}")
            target = (dest / Path(*name.parts)).resolve()
            if dest not in target.parents and target != dest:
                raise UpdateError(f"archive path escapes staging directory: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            if mode & 0o111:
                target.chmod(0o755)


def install_archive(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="qll-update-") as staging:
        staging_path = Path(staging)
        safe_extract(archive, staging_path)
        missing = [name for name in REQUIRED_FILES if not (staging_path / name).is_file()]
        if missing:
            raise UpdateError(
                "the published release does not contain " + ", ".join(missing)
                + " (it predates the auto-updater; it is replaced on the next release)"
            )
        proc = subprocess.run(
            ["bash", str(staging_path / "install.sh")],
            cwd=str(staging_path), capture_output=True, text=True, timeout=120,
        )
        log("install.sh output:\n" + (proc.stdout or "") + (proc.stderr or ""))
        if proc.returncode != 0:
            raise UpdateError(f"install.sh failed with exit code {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-400:]}")


def install_latest(timeout: float = CHECK_TIMEOUT, progress=None) -> dict:
    manifest = fetch_manifest(timeout)
    if progress:
        progress(f"Downloading Quake Live Launcher {manifest['app_version']}…")
    with tempfile.TemporaryDirectory(prefix="qll-download-") as tmp:
        archive = download_archive(manifest, Path(tmp))
        if progress:
            progress(f"Installing Quake Live Launcher {manifest['app_version']}…")
        install_archive(archive)
    return manifest


# ------------------------------------------------------------ desktop UI
class Progress:
    """Pulsing zenity dialog when available; otherwise silent (console + log)."""

    def __init__(self, title: str):
        self.proc = None
        if os.environ.get("QLL_UPDATER_NO_GUI") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return
        zenity = shutil.which("zenity")
        if not zenity:
            return
        try:
            self.proc = subprocess.Popen(
                [zenity, "--progress", "--pulsate", "--no-cancel", "--auto-close", f"--title={title}", "--text=Checking…"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            )
        except Exception:
            self.proc = None

    def __call__(self, text: str) -> None:
        say(text)
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(f"# {text}\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def close(self) -> None:
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write("100\n")
                    self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def notify(text: str) -> None:
    sender = shutil.which("notify-send")
    if sender and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("QLL_UPDATER_NO_GUI"):
        try:
            subprocess.Popen([sender, "Quake Live Launcher", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


# --------------------------------------------------------------- commands
def launch(args: list[str]) -> None:
    """Replace this process with the (possibly just updated) launcher."""
    override = os.environ.get("QLL_UPDATER_EXEC")  # tests: JSON argv list
    if override:
        argv = list(json.loads(override)) + list(args)
    else:
        argv = [sys.executable, str(install_dir() / "launcher_gui.py"), *args]
    os.execvp(argv[0], argv)


def cmd_launch(args: list[str]) -> int:
    if os.environ.get("QLL_UPDATE_DONE") or os.environ.get("QLL_NO_UPDATE"):
        launch(args)
    if not settings().get("auto_update", True):
        log("auto-update disabled in update.json")
        launch(args)
    installed = installed_version()
    try:
        remote = fetch_manifest(CHECK_TIMEOUT)
    except Exception as exc:
        log(f"update check skipped: {exc}")
        launch(args)
    should, reason = decide(installed, remote)
    log(f"update check: {reason}")
    if not should:
        launch(args)
    progress = Progress("Updating Quake Live Launcher")
    try:
        progress(f"Update available: {reason}")
        with tempfile.TemporaryDirectory(prefix="qll-download-") as tmp:
            progress(f"Downloading Quake Live Launcher {remote['app_version']}…")
            archive = download_archive(remote, Path(tmp))
            progress(f"Installing Quake Live Launcher {remote['app_version']}…")
            install_archive(archive)
        say(f"Updated to {remote['app_version']} ({str(remote['commit'])[:10]}); restarting.")
        notify(f"Updated to {remote['app_version']}")
    except Exception as exc:
        say(f"Update failed, starting the installed version instead: {exc}")
    finally:
        progress.close()
    os.environ["QLL_UPDATE_DONE"] = "1"
    launch(args)
    return 0


def cmd_install(assume_yes: bool) -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("QLL_ALLOW_ROOT") != "1":
        say("Please run the installer as your normal user, not with sudo; it installs into your home folder.")
        return 2
    say(f"Installing the latest Quake Live Launcher from {REPO} ({CHANNEL_TAG})…")
    try:
        manifest = install_latest(timeout=15.0, progress=say)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            say("The published release has no install manifest (version.json) yet. It is added the next time CI "
                "publishes the release from main; try again after that.")
        else:
            say(f"GitHub returned an error ({exc.code} {exc.reason}); try again in a few minutes.")
        return 3
    except urllib.error.URLError as exc:
        say(f"Could not reach GitHub: {exc}. Check your internet connection and try again.")
        return 3
    except Exception as exc:
        say(f"Install failed: {exc}")
        return 4
    say(f"Installed Quake Live Launcher {manifest['app_version']} ({str(manifest['commit'])[:10]}).")
    try:
        __import__("gi")
    except Exception:
        say("Note: the launcher window needs GTK for Python. On Mint/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0")
    say("Open your application menu and search for: Quake Live Launcher")
    say("It checks for updates each time it starts.")
    if assume_yes or not sys.stdin.isatty():
        return 0
    try:
        answer = input("Launch it now? [Y/n] ").strip().lower()
    except EOFError:
        return 0
    if answer in ("", "y", "yes"):
        env = dict(os.environ, QLL_UPDATE_DONE="1")
        subprocess.Popen(
            [sys.executable, str(install_dir() / "launcher_gui.py")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        say("Launcher started; you can close this window.")
    return 0


def cmd_check() -> int:
    installed = installed_version()
    print(f"Installed: {installed.get('app_version', 'unknown')} ({installed.get('commit', '?')[:10]}, {installed.get('source', 'unknown')} install)")
    try:
        remote = fetch_manifest(CHECK_TIMEOUT)
    except Exception as exc:
        print(f"Published: unavailable ({exc})")
        return 1
    print(f"Published: {remote['app_version']} ({remote['commit'][:10]}, built {remote.get('built_at', '?')})")
    print("Decision:  " + decide(installed, remote)[1])
    return 0


def cmd_record_local(source_dir: str) -> int:
    """Called by install.sh for a source-checkout install (no release.json)."""
    import re

    source = Path(source_dir)
    match = re.search(r'^APP_VERSION = "([^"]+)"', (source / "launcher.py").read_text(encoding="utf-8"), re.M)
    try:
        commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        commit = ""
    target = version_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "source": "local",
        "app_version": match.group(1) if match else "unknown",
        "commit": commit,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--launch":
        return cmd_launch(argv[1:])
    if len(argv) == 2 and argv[0] == "--record-local":
        return cmd_record_local(argv[1])
    if argv == ["--installed-version"]:
        print(installed_version().get("app_version", "unknown"))
        return 0
    parser = argparse.ArgumentParser(description="Quake Live Launcher installer/updater")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="install the latest release")
    group.add_argument("--check", action="store_true", help="show installed and published versions")
    parser.add_argument("--yes", action="store_true", help="do not ask to launch after installing")
    opts = parser.parse_args(argv)
    if opts.install:
        return cmd_install(opts.yes)
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
