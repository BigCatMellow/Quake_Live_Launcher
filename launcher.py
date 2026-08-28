#!/usr/bin/env python3
"""Loader for the v5 launcher implementation plus the Solo hot-load bridge."""
from pathlib import Path
import base64
import gzip
import json
import random
import subprocess
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

APP_VERSION = "5.0-alpha-hotload1"
SOLO_MATCH_REQUEST_FILE = SOLO_RUNTIME_DIR / "match_request.json"
SOLO_MATCH_STATUS_FILE = SOLO_RUNTIME_DIR / "match_status.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def solo_hot_switch_available() -> bool:
    """True when an already-running local Solo plugin can accept a match handoff."""
    return bool(solo_server_pid() and solo_plugin_ready())


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
            if state in {"loading", "started", "active"} and solo_plugin_ready(mode):
                return payload
        time.sleep(0.10)
    raise RuntimeError(f"Solo Engine did not acknowledge hot-load request for {mode} within {timeout:.0f}s.")


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
        status(f"Solo server verified; PID={pid}; UDP27960={solo_udp_listening()}; plugin={solo_plugin_ready_payload()}.")
        if quake_running():
            status("ERROR: Quake Live is already running but is not attached to this newly started Solo server. Close it once and retry.")
            return
        launch_client()

    threading.Thread(target=coordinator, daemon=True).start()
    return log_path


if _WAS_MAIN:
    raise SystemExit(main())
