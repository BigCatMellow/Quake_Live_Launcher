#!/usr/bin/env python3
"""Loader for the v5 launcher implementation plus the Solo hot-load bridge."""
from pathlib import Path
import base64
import gzip
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

_BASE = Path(__file__).resolve().parent
_PARTS = sorted(_BASE.glob("launcher_impl.py.gz.b64part*"))
if not _PARTS:
    raise RuntimeError("Quake Live Launcher payload is missing: launcher_impl.py.gz.b64part*")
_SOURCE = gzip.decompress(base64.b64decode("".join(p.read_text(encoding="ascii").strip() for p in _PARTS)))

# Load the retained implementation without letting its __main__ guard run yet;
# the bridge below must be installed before GTK/Tk starts calling core helpers.
_WAS_MAIN = __name__ == "__main__"
_ORIGINAL_NAME = __name__
if _WAS_MAIN:
    globals()["__name__"] = "qll_launcher_payload"
exec(compile(_SOURCE, str(_BASE / "launcher_impl.py"), "exec"), globals(), globals())
globals()["__name__"] = _ORIGINAL_NAME

APP_VERSION = "5.0-alpha-hotload2"
SOLO_MATCH_REQUEST_FILE = SOLO_RUNTIME_DIR / "match_request.json"
SOLO_MATCH_STATUS_FILE = SOLO_RUNTIME_DIR / "match_status.json"
SOLO_HOTLOAD_READY_FILE = SOLO_RUNTIME_DIR / "hotload_ready.json"
GITHUB_DEBUG_REPO = "BigCatMellow/Quake_Live_Launcher"
GITHUB_DEBUG_OWNER = "BigCatMellow"
SOLO_GITHUB_DEBUG_STATUS_FILE = SOLO_RUNTIME_DIR / "last_github_debug.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def solo_hot_switch_available() -> bool:
    """True only when this exact running server advertises hot-load protocol v1."""
    pid = solo_server_pid()
    if not pid or not solo_plugin_ready():
        return False
    try:
        payload = json.loads(SOLO_HOTLOAD_READY_FILE.read_text(encoding="utf-8"))
        return (
            isinstance(payload, dict)
            and int(payload.get("protocol", 0)) == 1
            and int(payload.get("pid", -1)) == int(pid)
            and bool(payload.get("ready"))
        )
    except Exception:
        return False


def solo_match_status() -> dict:
    try:
        payload = json.loads(SOLO_MATCH_STATUS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def request_solo_match_switch() -> str:
    session = load_json(SOLO_SESSION_FILE, {})
    mode = str(session.get("mode", ""))
    map_name = str(session.get("map", ""))
    if not mode or not map_name:
        raise RuntimeError("Solo session is incomplete; cannot hot-load it.")
    request_id = f"{time.time_ns()}-{random.SystemRandom().randint(1000, 9999)}"
    _atomic_json(SOLO_MATCH_REQUEST_FILE, {
        "request_id": request_id,
        "mode": mode,
        "map": map_name,
        "requested_at": time.time(),
    })
    return request_id


def wait_for_solo_match_switch(request_id: str, mode: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        payload = solo_match_status()
        if str(payload.get("request_id") or "") == str(request_id):
            state = str(payload.get("state") or "")
            if state == "failed":
                raise RuntimeError(str(payload.get("error") or "Solo hot-load failed"))
            if state in {"started", "active"} and solo_plugin_ready(mode):
                return payload
        time.sleep(0.10)
    raise RuntimeError(f"Solo Engine did not acknowledge hot-load request for {mode} within {timeout:.0f}s.")


def _debug_file_text(path: Path, lines: int = 300) -> str:
    if not path.exists():
        return "(not available)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-max(1, int(lines)):])
    except Exception as exc:
        return f"(could not read {path}: {exc})"


def _scrub_public_debug_text(text: str) -> str:
    """Remove obvious local identity/secrets before posting to a public issue."""
    value = str(text)
    home = str(Path.home())
    if home:
        value = value.replace(home, "~")
    hostname = socket.gethostname().strip()
    if hostname:
        value = value.replace(hostname, "<hostname>")
    username = os.environ.get("USER", "").strip()
    if username:
        value = re.sub(rf"(?<![A-Za-z0-9]){re.escape(username)}(?![A-Za-z0-9])", "<user>", value)
    secret_patterns = [
        r"github_pat_[A-Za-z0-9_]+",
        r"gh[pousr]_[A-Za-z0-9]+",
        r"(?i)(authorization:\s*(?:token|bearer)\s+)[^\s]+",
    ]
    for pattern in secret_patterns:
        if pattern.startswith("(?i)(authorization"):
            value = re.sub(pattern, r"\1<redacted>", value)
        else:
            value = re.sub(pattern, "<redacted-github-token>", value)
    return value


def write_solo_exit_debug_report(game_dir=None, reason: str = "quake-exit") -> Path:
    """Create a post-game report with the runtime handoff state needed for forfeit debugging."""
    base_report = write_solo_diagnostic_report(Path(game_dir) if game_dir else None)
    session = load_json(SOLO_SESSION_FILE, {})
    extra_paths = [
        ("PLUGIN READY", SOLO_RUNTIME_DIR / "plugin_ready.json"),
        ("HOT-LOAD READY", SOLO_HOTLOAD_READY_FILE),
        ("MATCH REQUEST", SOLO_MATCH_REQUEST_FILE),
        ("MATCH STATUS", SOLO_MATCH_STATUS_FILE),
        ("MINQLX LOG (tail)", SOLO_RUNTIME_DIR / "home" / "minqlx.log"),
        ("SERVER LOG (tail)", SOLO_RUNTIME_DIR / "server.log"),
    ]
    chunks = [
        base_report.read_text(encoding="utf-8", errors="replace"),
        "\nPOST-GAME CAPTURE",
        f"Reason: {reason}",
        f"Quake running at capture: {quake_running()}",
        f"Mode: {session.get('mode', '(unknown)')}",
        f"Map: {session.get('map', '(unknown)')}",
    ]
    for heading, path in extra_paths:
        chunks.extend(["", heading, _debug_file_text(path, 450 if "LOG" in heading else 120)])
    full_text = "\n".join(chunks).rstrip() + "\n"
    base_report.write_text(full_text, encoding="utf-8")

    public_path = base_report.with_name(base_report.stem + "-github.txt")
    public_path.write_text(_scrub_public_debug_text(full_text), encoding="utf-8")
    return public_path


def _record_github_debug_status(payload: dict) -> None:
    try:
        _atomic_json(SOLO_GITHUB_DEBUG_STATUS_FILE, payload)
    except Exception:
        pass


def upload_solo_exit_debug(game_dir=None, reason: str = "quake-exit") -> dict:
    """Post a privacy-scrubbed diagnostic report as a GitHub issue when gh auth exists."""
    report = write_solo_exit_debug_report(game_dir, reason=reason)
    session = load_json(SOLO_SESSION_FILE, {})
    mode = str(session.get("mode") or "unknown")
    map_name = str(session.get("map") or "unknown")
    gh = shutil.which("gh")
    result = {
        "uploaded": False,
        "report": str(report),
        "repo": GITHUB_DEBUG_REPO,
        "mode": mode,
        "map": map_name,
        "reason": reason,
        "time": time.time(),
    }
    if not gh:
        result["error"] = "GitHub CLI (gh) is not installed; report saved locally."
        _record_github_debug_status(result)
        return result

    try:
        auth = subprocess.run(
            [gh, "auth", "status", "--hostname", "github.com"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        result["error"] = f"Could not check GitHub authentication: {exc}"
        _record_github_debug_status(result)
        return result
    if auth.returncode != 0:
        result["error"] = "GitHub CLI is not authenticated; report saved locally. Run: gh auth login"
        _record_github_debug_status(result)
        return result

    # This repository is public. Never make an authenticated GitHub user who
    # merely installed the launcher post diagnostics into the maintainer's repo.
    # Automatic upload is intentionally limited to the repo owner's gh login.
    try:
        who = subprocess.run(
            [gh, "api", "user", "--jq", ".login"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        result["error"] = f"Could not identify the authenticated GitHub account: {exc}"
        _record_github_debug_status(result)
        return result
    login = (who.stdout or "").strip() if who.returncode == 0 else ""
    if login.lower() != GITHUB_DEBUG_OWNER.lower():
        result["error"] = f"Automatic upload is restricted to GitHub user {GITHUB_DEBUG_OWNER}; authenticated user was {login or '(unknown)'} .".replace("(unknown) .", "(unknown).")
        _record_github_debug_status(result)
        return result

    text = report.read_text(encoding="utf-8", errors="replace")
    # GitHub issue bodies have a size limit. Keep the beginning (system/session)
    # and the end (the freshest server/minqlx evidence) if trimming is required.
    max_report = 56000
    if len(text) > max_report:
        head = text[:18000]
        tail = text[-(max_report - 18000):]
        text = head + "\n\n... [middle of diagnostic report trimmed for GitHub] ...\n\n" + tail
    body = (
        "Automatically captured by Quake Live Launcher after the Quake client closed.\n\n"
        f"- Launcher: `{APP_VERSION}`\n"
        f"- Mode: `{mode}`\n"
        f"- Map: `{map_name}`\n"
        f"- Capture reason: `{reason}`\n\n"
        "The uploaded copy is privacy-scrubbed; the complete local report remains in the launcher log folder.\n\n"
        "```text\n" + text.replace("```", "` ` `") + "\n```\n"
    )
    issue_body = report.with_name(report.stem + "-issue.md")
    issue_body.write_text(body, encoding="utf-8")
    title = f"[auto-debug] Solo exit — {mode} — {map_name} — {time.strftime('%Y-%m-%d %H:%M:%S')}"
    try:
        proc = subprocess.run(
            [gh, "issue", "create", "--repo", GITHUB_DEBUG_REPO, "--title", title, "--body-file", str(issue_body)],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:
        result["error"] = f"GitHub upload failed: {exc}"
        _record_github_debug_status(result)
        return result
    if proc.returncode != 0:
        result["error"] = (proc.stderr or proc.stdout or "GitHub upload failed").strip()
        _record_github_debug_status(result)
        return result

    result["uploaded"] = True
    result["url"] = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
    _record_github_debug_status(result)
    return result


def launch_solo_exit_debug_watcher(game_dir=None, log_path=None) -> None:
    payload = json.dumps({
        "game_dir": str(game_dir) if game_dir else "",
        "log_path": str(log_path) if log_path else "",
    })
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--solo-exit-debug", payload],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _solo_exit_debug_watcher_main(payload: str) -> int:
    try:
        data = json.loads(payload)
        game_dir = data.get("game_dir") or None
        log_path = Path(data["log_path"]) if data.get("log_path") else None
    except Exception:
        game_dir = None
        log_path = None

    # A detached watcher survives launcher/plugin failures. It waits for the
    # actual client process, then captures state after Quake has fully exited.
    deadline = time.time() + 120
    while time.time() < deadline and not quake_running():
        time.sleep(0.5)
    if not quake_running():
        result = upload_solo_exit_debug(game_dir, reason="quake-client-never-appeared")
        if log_path:
            append_solo_log(log_path, f"Post-game debug result: {json.dumps(result, sort_keys=True)}")
        return 0
    while quake_running():
        time.sleep(1.0)
    time.sleep(2.0)
    result = upload_solo_exit_debug(game_dir, reason="quake-exit")
    if log_path:
        append_solo_log(log_path, f"Post-game debug result: {json.dumps(result, sort_keys=True)}")
    return 0


def launch_solo_mode(
    steam_cmd,
    game_dir=None,
    side_thrusters=False,
    status_callback=None,
):
    """Start Solo once, then hot-load later Solo matches into the same client."""
    if not solo_engine_ready():
        raise RuntimeError("Solo Engine is not installed yet.")
    starter = SOLO_ENGINE_DIR / "start_solo.sh"
    session = load_json(SOLO_SESSION_FILE, {})
    log_path = Path(session.get("log_path") or new_solo_log_path("solo-start"))
    append_solo_log(log_path, f"launch_solo_mode called; starter={starter}; hotload_bridge=1")
    append_solo_log(log_path, f"Steam command: {' '.join(steam_cmd)}")

    def status(message):
        append_solo_log(log_path, message)
        if status_callback:
            try:
                status_callback(message)
            except Exception:
                pass

    def launch_client():
        controls_cfg = None
        originals = {}
        # Install the reversible wrapper on the first Solo client launch even if
        # this particular mode has dash disabled. A later hot-loaded mode can
        # then enable it without needing to restart Quake.
        if game_dir:
            try:
                controls_cfg, originals = write_solo_controls_cfg(game_dir, enabled=True)
                append_solo_log(log_path, f"Temporary Solo controls written: {controls_cfg}; original binds={originals}")
                launch_control_restore_watcher(game_dir, originals)
            except Exception as exc:
                append_solo_log(log_path, f"WARNING: Solo controls could not be prepared: {exc}")
        args = list(steam_cmd) + ["-applaunch", APP_ID]
        if controls_cfg is not None:
            args += ["+exec", controls_cfg.name]
        args += ["+connect", "127.0.0.1:27960"]
        status(f"Launching Quake Live client: {' '.join(args)}")
        try:
            launch_solo_exit_debug_watcher(game_dir, log_path)
            append_solo_log(log_path, "Post-game GitHub debug watcher started.")
        except Exception as exc:
            append_solo_log(log_path, f"WARNING: post-game debug watcher could not start: {exc}")
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT)
            status("Quake Live client launch request sent to Steam.")
        except Exception as exc:
            status(f"ERROR: Steam client launch failed: {exc}")

    def coordinator():
        mode = str(session.get("mode", ""))
        if solo_hot_switch_available():
            try:
                request_id = request_solo_match_switch()
                status(f"Hot-loading {mode} into the running Solo Engine (request {request_id}).")
                hot_status = wait_for_solo_match_switch(request_id, mode, timeout=15.0)
                status(f"Solo hot-load accepted: state={hot_status.get('state')} mode={mode}.")
                if quake_running():
                    status("New Solo match loaded into the already-running Quake Live client.")
                    return
                launch_client()
                return
            except Exception as exc:
                status(f"WARNING: Solo hot-load failed: {exc}")
                if quake_running():
                    status("ERROR: Quake Live is running but its Solo server could not accept the new match. Close Quake once and retry; later Solo matches can hot-load in place.")
                    return
                status("Restarting the local Solo server as a recovery path.")

        status(f"Starting local Solo Engine server. Log: {log_path}")
        try:
            proc = subprocess.run([str(starter)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=35)
        except subprocess.TimeoutExpired:
            status("ERROR: start_solo.sh did not finish its health check within 35 seconds.")
            return
        except Exception as exc:
            status(f"ERROR: could not run start_solo.sh: {exc}")
            return
        if proc.returncode != 0:
            status(f"ERROR: Solo server startup failed with exit code {proc.returncode}. Open the latest log for details.")
            return
        pid = solo_server_pid()
        if not solo_plugin_ready(mode):
            status(f"ERROR: server returned success but plugin readiness handshake is missing or for the wrong mode ({mode}).")
            return
        if not solo_hot_switch_available():
            status("ERROR: server is healthy but did not advertise the hot-load v1 capability handshake.")
            return
        status(f"Solo server verified; PID={pid}; UDP27960={solo_udp_listening()}; plugin={solo_plugin_ready_payload()}.")
        if quake_running():
            status("ERROR: Quake Live is already running but is not attached to this newly started Solo server. Close it once and retry.")
            return
        launch_client()

    threading.Thread(target=coordinator, daemon=True).start()
    return log_path


if _WAS_MAIN:
    if "--solo-exit-debug" in sys.argv:
        idx = sys.argv.index("--solo-exit-debug")
        if idx + 1 >= len(sys.argv):
            raise SystemExit(2)
        raise SystemExit(_solo_exit_debug_watcher_main(sys.argv[idx + 1]))
    raise SystemExit(main())
