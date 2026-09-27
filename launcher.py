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

APP_VERSION = "5.0-alpha-controls1"
SOLO_MATCH_REQUEST_FILE = SOLO_RUNTIME_DIR / "match_request.json"
SOLO_MATCH_STATUS_FILE = SOLO_RUNTIME_DIR / "match_status.json"
SOLO_HOTLOAD_READY_FILE = SOLO_RUNTIME_DIR / "hotload_ready.json"
# Must match solo_directed.HOTLOAD_PROTOCOL. Protocol 2 = permanent-warmup
# anti-forfeit sandbox; 3 = mode overhaul (!again, F5-F7 picks, records);
# 4 = spawn Director (learned spawn points, placement, flankers); 5 = no
# training mode, flood protection off, dedicated dash key.
# A server advertising an older protocol is restarted instead of reused.
SOLO_HOTLOAD_PROTOCOL = 5
GITHUB_DEBUG_REPO = "BigCatMellow/Quake_Live_Launcher"
GITHUB_DEBUG_OWNER = "BigCatMellow"
SOLO_GITHUB_DEBUG_STATUS_FILE = SOLO_RUNTIME_DIR / "last_github_debug.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


# ----------------------------
# Solo client controls (overrides for the retained payload)
# ----------------------------
# The payload's control helpers were stored with one escaping layer too many:
# its bind regexes contain literal "\\s" (so they never match) and the Solo
# controls cfg was joined with a literal backslash-n, producing ONE line that
# starts with "//" -- the whole file was a comment, so the side-thruster keys
# were never bound, and restores appended junk lines to qzconfig.cfg. These
# replacements are picked up by the payload's own callers (detect_strafe_keys,
# restore_strafe_binds, launch_solo_mode) because they share this module's
# globals.

# Arena Run upgrade picks on F5/F6/F7 (-> hidden "qlpick N" client command,
# handled server-side like the qldash side thrusters). Originals (or "" when a
# key was unbound) are restored by the existing restore watcher.
SOLO_PICK_KEYS = ("F5", "F6", "F7")
_BIND_LINE = re.compile(r'^\s*bind\s+(\S+)\s+"([^"]*)"', re.I | re.M)


def _parse_binds(text: str) -> dict:
    binds: dict = {}
    for match in _BIND_LINE.finditer(text):
        binds[match.group(1).upper()] = match.group(2)
    return binds


def _replace_bind_line(text: str, key: str, command: str) -> str:
    safe = str(command).replace('"', "'")
    replacement = f'bind {key} "{safe}"'
    pattern = re.compile(rf'^[ \t]*bind[ \t]+{re.escape(key)}[ \t]+"[^"\n]*".*$', re.I | re.M)
    if pattern.search(text):
        return pattern.sub(lambda _m: replacement, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + replacement + "\n"


def _current_client_binds(game_dir: Path) -> dict:
    binds: dict = {}
    for cfg in reversed(_candidate_client_configs(Path(game_dir))):
        try:
            binds.update(_parse_binds(cfg.read_text(encoding="utf-8", errors="ignore")))
        except Exception:
            continue
    return binds


# Side-thruster dash lives on ONE dedicated key. Earlier builds sent a hidden
# "cmd qldash" on every strafe-key press; strafe is tapped constantly, and
# Quake Live disconnects clients that send commands that fast ("Server
# disconnected - flooding the server"). The first candidate you have not bound
# is used; override with {"dash_key": "MOUSE4"} in solo_controls.json.
SOLO_DASH_KEY_CANDIDATES = ("MOUSE4", "MOUSE5", "SHIFT", "ALT", "V", "G", "X", "Z")
SOLO_CONTROLS_FILE = SOLO_RUNTIME_DIR / "controls.json"
SOLO_CONTROLS_OVERRIDE = Path.home() / ".config/quake-live-launcher/solo_controls.json"
_OLD_STRAFE_WRAPPERS = {"+qll_side_left": "+moveleft", "+qll_side_right": "+moveright"}


def choose_solo_dash_key(binds: dict):
    try:
        override = json.loads(SOLO_CONTROLS_OVERRIDE.read_text(encoding="utf-8")).get("dash_key")
    except Exception:
        override = None
    if override:
        return str(override).upper()
    for key in SOLO_DASH_KEY_CANDIDATES:
        if not str(binds.get(key, "")).strip():
            return key
    return None


def write_solo_controls_cfg(game_dir, enabled: bool = True):
    """Write the temporary Solo controls cfg (dash key + upgrade picks)."""
    if not enabled:
        return None, {}
    game_dir = Path(game_dir)
    binds = _current_client_binds(game_dir)
    originals: dict = {}
    lines = ["// Temporary Solo Engine controls. Original binds are restored after Quake exits."]
    # Repair strafe keys still bound to the old per-tap wrapper (for example
    # after a session that ended in a flood disconnect before the restore ran).
    for key, command in sorted(binds.items()):
        plain = _OLD_STRAFE_WRAPPERS.get(str(command).strip().lower())
        if plain:
            lines.append(f'bind {key} "{plain}"')
            originals[key] = plain
    dash_key = choose_solo_dash_key(binds)
    if dash_key:
        originals.setdefault(dash_key, binds.get(dash_key, ""))
        lines.append(f'bind {dash_key} "cmd qldash auto"')
    for index, key in enumerate(SOLO_PICK_KEYS, 1):
        if dash_key and key.upper() == dash_key:
            continue
        originals.setdefault(key, binds.get(key, ""))
        lines.append(f'bind {key} "cmd qlpick {index}"')
    dash_text = f"hold a strafe key and press {dash_key} to dodge/air-dash" if dash_key else "use !dash left/right"
    lines.append(f'echo "^6Solo:^7 side thrusters: {dash_text}; F5/F6/F7 pick Arena Run upgrades"')
    cfg = game_dir / "baseq3" / "qllauncher_solo_controls.cfg"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        _atomic_json(SOLO_CONTROLS_FILE, {"dash_key": dash_key, "pick_keys": list(SOLO_PICK_KEYS), "written_at": time.time()})
    except Exception:
        pass
    return cfg, originals


def solo_hot_switch_available() -> bool:
    """True only when this exact running server advertises the current hot-load protocol."""
    pid = solo_server_pid()
    if not pid or not solo_plugin_ready():
        return False
    try:
        payload = json.loads(SOLO_HOTLOAD_READY_FILE.read_text(encoding="utf-8"))
        return (
            isinstance(payload, dict)
            and int(payload.get("protocol", 0)) == SOLO_HOTLOAD_PROTOCOL
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
        result["error"] = f"Automatic upload is restricted to GitHub user {GITHUB_DEBUG_OWNER}; authenticated user was {login or '(unknown)'} .".replace(" .", ".")
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


# ----------------------------
# Quake client process detection (overrides for the retained payload)
# ----------------------------
# The payload matched any process whose command line mentions "Quake Live",
# which includes short-lived helpers (map sync, Steam's launch wrapper) that
# carry the game folder in their arguments. The watchers also trusted a single
# sighting, so a blip at launch counted as "Quake started" and the next check
# as "Quake closed": the post-game report fired at launch
# ("quake-client-never-appeared") and binds could be restored before the game
# even ran. Detection now ignores our own helpers, and both watchers require a
# state to hold for several seconds.
_payload_quake_running = quake_running
_NOT_THE_CLIENT = ("quake-live-launcher", "launcher.py", "sync_maps.py", "qzeroded", "steamcmd")
QUAKE_STABLE_SECONDS = 4.0
QUAKE_APPEAR_TIMEOUT = 180.0


def quake_running() -> bool:
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return False
    try:
        proc = subprocess.run([pgrep, "-af", r"(quakelive|quakelive_steam|Quake Live)"],
                              text=True, capture_output=True, timeout=3)
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        lowered = line.lower()
        if not any(marker in lowered for marker in _NOT_THE_CLIENT):
            return True
    return False


def wait_for_quake_state(want: bool, *, stable_for: float = QUAKE_STABLE_SECONDS, timeout=None,
                         poll: float = 1.0, running=None, sleep=time.sleep, clock=time.time) -> bool:
    """Block until Quake has been running (want=True) / gone (want=False) for
    `stable_for` consecutive seconds. Returns False if `timeout` passes first."""
    running = running or quake_running
    start = clock()
    since = None
    while True:
        now = clock()
        if bool(running()) == want:
            if since is None:
                since = now
            if now - since >= stable_for:
                return True
        else:
            since = None
        if timeout is not None and now - start >= timeout:
            return False
        sleep(poll)


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
    if not wait_for_quake_state(True, timeout=QUAKE_APPEAR_TIMEOUT):
        result = upload_solo_exit_debug(game_dir, reason="quake-client-never-appeared")
        if log_path:
            append_solo_log(log_path, f"Post-game debug result: {json.dumps(result, sort_keys=True)}")
        return 0
    wait_for_quake_state(False)
    time.sleep(2.0)
    result = upload_solo_exit_debug(game_dir, reason="quake-exit")
    if log_path:
        append_solo_log(log_path, f"Post-game debug result: {json.dumps(result, sort_keys=True)}")
    return 0


def _restore_watcher_main(payload: str) -> int:
    """Restore the binds the Solo controls cfg touched, after Quake really exits."""
    try:
        data = json.loads(payload)
        game_dir = Path(data["game_dir"])
        originals = dict(data.get("originals") or {})
    except Exception:
        return 2
    if wait_for_quake_state(True, timeout=QUAKE_APPEAR_TIMEOUT):
        wait_for_quake_state(False)
        time.sleep(1.5)  # let Quake finish writing qzconfig.cfg
    restore_strafe_binds(game_dir, originals)
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
            status("ERROR: server is healthy but did not advertise the current hot-load capability handshake.")
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
