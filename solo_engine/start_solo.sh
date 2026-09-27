#!/usr/bin/env bash
set -u
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
QLDS="$RUNTIME/qlds"
VENV="$RUNTIME/.venv"
HOME_PATH="$RUNTIME/home"
PIDFILE="$RUNTIME/server.pid"
SESSION="$HOME/.config/quake-live-launcher/solo_session.json"
PLUGIN_READY="$RUNTIME/plugin_ready.json"
PORT="${QLL_SOLO_PORT:-27960}"
SKIP_READY_CHECK="${QLL_SKIP_READY_CHECK:-0}"
# The Director probe (run_director_probe.sh) loads solo_probe instead.
PLUGINS="${QLL_PLUGINS:-solo_directed}"
# shinqlx's stats listener (the ONLY source of death/kill events) connects with
# ZMQ PLAIN auth using zmq_stats_password; libzmq rejects an empty PLAIN
# password with EINVAL ("zmq error: InvalidArgument"), which silently killed
# the listener on every start. Use a fresh random password per launch.
ZMQ_PASSWORD="${QLL_ZMQ_PASSWORD:-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}"

mkdir -p "$LOG_DIR" "$RUNTIME"
fallback_log="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-start.log"
LOG="$fallback_log"
if [ -f "$SESSION" ] && [ -x "$VENV/bin/python" ]; then
  candidate=$("$VENV/bin/python" - "$SESSION" 2>/dev/null <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get('log_path',''))
except Exception: print('')
PY
)
  [ -n "$candidate" ] && LOG="$candidate"
fi
mkdir -p "$(dirname "$LOG")"; touch "$LOG"
printf '%s\n' "$LOG" > "$RUNTIME/last_server_log"
ln -sfn "$LOG" "$RUNTIME/server.log" 2>/dev/null || true
log(){ printf '[%s] [start] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
fail(){ code="$1"; shift; log "ERROR: $*"; log "Startup failed; log=$LOG"; exit "$code"; }
run_logged(){ log "RUN: $*"; "$@" >>"$LOG" 2>&1; rc=$?; log "EXIT $rc: $*"; return $rc; }

log "---- Solo Engine v5 startup begin ----"
log "SOURCE_DIR=$SOURCE_DIR"
log "RUNTIME=$RUNTIME"
log "QLDS=$QLDS"
log "PORT=$PORT"
log "PLUGINS=$PLUGINS"
log "SESSION=$SESSION"

if [ "$SKIP_READY_CHECK" != "1" ]; then
  [ -f "$RUNTIME/READY" ] || fail 3 "Solo Engine is not verified. Run setup_solo_engine.sh first."
fi
[ -f "$SESSION" ] || fail 4 "Solo session file missing."
[ -x "$VENV/bin/python" ] || fail 4 "Solo Python missing: $VENV/bin/python"
[ -x "$QLDS/qzeroded.x64" ] || fail 4 "QLDS binary missing: $QLDS/qzeroded.x64"

readarray -t vals < <("$VENV/bin/python" - "$SESSION" <<'PY'
import json,sys
x=json.load(open(sys.argv[1]))
print(x.get('mode','horde'))
print(x.get('map','campgrounds'))
print(x.get('game_dir',''))
print(x.get('log_path',''))
PY
)
MODE="${vals[0]}"; MAP="${vals[1]}"; GAME_DIR="${vals[2]}"
log "Session: mode=$MODE map=$MAP game_dir=$GAME_DIR"
log "Session JSON:"; cat "$SESSION" >>"$LOG" 2>&1

log "Synchronizing Solo plugin package"
rm -rf "$QLDS/minqlx-plugins"
mkdir -p "$QLDS/minqlx-plugins"
run_logged cp "$SOURCE_DIR/plugins/__init__.py" "$QLDS/minqlx-plugins/__init__.py" || fail 10 "Could not copy plugin package __init__.py"
for file in solo_arcade.py solo_directed.py solo_director.py director_runtime.py director_learning.py solo_controller.py solo_core.py spawn_director.py solo_probe.py; do
  run_logged cp "$SOURCE_DIR/plugins/$file" "$QLDS/minqlx-plugins/$file" || fail 10 "Could not copy $file"
done
mkdir -p "$QLDS/minqlx-plugins/modes"
run_logged cp "$SOURCE_DIR/plugins/modes/__init__.py" "$QLDS/minqlx-plugins/modes/__init__.py" || fail 10 "Could not copy modes package"
for file in "$SOURCE_DIR"/plugins/modes/*.py; do
  [ -f "$file" ] || continue
  run_logged cp "$file" "$QLDS/minqlx-plugins/modes/$(basename "$file")" || fail 10 "Could not copy mode module $(basename "$file")"
done

if [ -n "$GAME_DIR" ]; then
  log "Syncing installed/base/Workshop maps"
  if ! run_logged "$VENV/bin/python" "$SOURCE_DIR/sync_maps.py" "$GAME_DIR" "$QLDS/baseq3"; then
    log "WARNING: map sync failed; base QLDS maps may still work"
  fi
else
  log "WARNING: no game_dir in session; skipping map sync"
fi

# A previous server that is still up keeps the port; the new one then falls
# back to PORT+1 while the game client connects to the OLD one on PORT. Stop it
# properly (SIGTERM, then SIGKILL) and refuse to start while anything else
# holds the port.
log "Stopping any previous Solo server on port $PORT"
QLL_SOLO_PORT="$PORT" "$SOURCE_DIR/stop_solo.sh" >>"$LOG" 2>&1 || log "WARNING: stop_solo.sh reported a problem (see above)"
rm -f "$PIDFILE" "$PLUGIN_READY"
holders=$("$VENV/bin/python" "$SOURCE_DIR/solo_ports.py" owners "$PORT" "$QLDS/qzeroded.x64" 2>>"$LOG" || true)
if [ -n "$holders" ]; then
  log "Port $PORT is still in use:"
  printf '%s\n' "$holders" | sed 's/^/[port-owner] /' | tee -a "$LOG"
  if printf '%s\n' "$holders" | grep -q ' foreign '; then
    fail 9 "Port $PORT is held by another program (PID/name above). Close it, or set QLL_SOLO_PORT to a free port."
  fi
  fail 9 "A previous Solo server still holds port $PORT and could not be stopped. Run: kill -9 $(printf '%s\n' "$holders" | awk '{print $1}' | sort -u | tr '\n' ' ')"
fi

log "Locating shinqlx preload shared library"
SHINQLX_LIB=$(find "$VENV" -type f -path '*/shinqlx/*.so' -print -quit)
[ -n "$SHINQLX_LIB" ] || fail 5 "shinqlx shared library not found in $VENV"
log "SHINQLX_LIB=$SHINQLX_LIB"

if command -v ldd >/dev/null 2>&1; then
  log "qzeroded.x64 ldd with runtime library path:"
  LD_LIBRARY_PATH="$QLDS/linux64:$QLDS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd "$QLDS/qzeroded.x64" >>"$LOG" 2>&1 || true
  log "shinqlx shared-library ldd output:"
  ldd "$SHINQLX_LIB" >>"$LOG" 2>&1 || true
fi

# Anti-forfeit contract: the scripted TDM sandbox stays in WARMUP for its whole
# life. Quake Live's forfeit rules (e.g. a live match with an empty BLUE team)
# only run after warmup ends, and the Solo plugin owns scoring/completion.
# These are set AFTER +exec server.cfg so an older installed server.cfg that
# still says g_doWarmup "0" cannot switch the match layer back on.
# sv_floodProtect 0: Quake 3 servers accept one client command per second and
# Quake Live disconnects clients that go faster ("flooding the server"). This
# server only listens on 127.0.0.1, so flood protection has nothing to protect.
log "Launching qzeroded.x64 on 127.0.0.1:$PORT using TDM combat sandbox (permanent warmup) + encounter Director"
LOG_OFFSET=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
(
  cd "$QLDS" || exit 91
  export VIRTUAL_ENV="$VENV"
  export PATH="$VENV/bin:$PATH"
  unset PYTHONHOME || true
  export LD_PRELOAD="${SHINQLX_LIB}${LD_PRELOAD:+:$LD_PRELOAD}"
  export LD_LIBRARY_PATH="$QLDS/linux64:$QLDS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  exec "$QLDS/qzeroded.x64" \
    +set fs_basepath "$QLDS" \
    +set fs_homepath "$HOME_PATH" \
    +set net_ip 127.0.0.1 \
    +set net_port "$PORT" \
    +set sv_pure 0 \
    +set qlx_pluginsPath "$QLDS/minqlx-plugins" \
    +set qlx_plugins "$PLUGINS" \
    +set qlx_soloMode "$MODE" \
    +set zmq_stats_enable 1 \
    +set zmq_stats_port "$PORT" \
    +set zmq_stats_password "$ZMQ_PASSWORD" \
    +set bot_minplayers 0 \
    +set g_friendlyFire 0 \
    +set g_teamForceBalance 0 \
    +exec server.cfg \
    +set qlx_plugins "$PLUGINS" \
    +set g_doWarmup 1 \
    +set sv_warmupReadyPercentage 1 \
    +set g_warmupReadyDelay 0 \
    +set g_warmupDelay 0 \
    +set g_training 0 \
    +set sv_floodProtect 0 \
    +map "$MAP" tdm
) >>"$LOG" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"
log "qzeroded launched as PID $pid"

# Engine log lines that mean this server did not get the port it was given.
BIND_FAILURES='UDP_OpenSocket: bind: Address already in use|zmq PUB socket error'
kill_new_server(){
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PIDFILE"
}
port_owners(){ "$VENV/bin/python" "$SOURCE_DIR/solo_ports.py" owners "$PORT" "$QLDS/qzeroded.x64" 2>/dev/null | sed 's/^/[port-owner] /' | tee -a "$LOG"; }
plugin_ok=0
socket_ok=0
for attempt in $(seq 1 "${QLL_START_ATTEMPTS:-50}"); do
  sleep 0.4
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null; rc=$?
    log "ERROR: qzeroded exited during startup (attempt=$attempt, exit=$rc)"
    tail -n 120 "$LOG" | sed 's/^/[server-tail] /' | tee -a "$LOG" >/dev/null
    exit 6
  fi

  if [ -f "$PLUGIN_READY" ]; then
    if "$VENV/bin/python" - "$PLUGIN_READY" "$MODE" "$pid" >>"$LOG" 2>&1 <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
mode=sys.argv[2]
if p.get('ready') is not True: raise SystemExit(2)
if p.get('mode') != mode: raise SystemExit(3)
# Written by the plugin inside qzeroded, so it must be THIS server's PID.
if int(p.get('pid') or 0) != int(sys.argv[3]): raise SystemExit(4)
print('plugin handshake ok:', p)
PY
    then
      plugin_ok=1
    fi
  fi

  # The port must belong to THIS server, not just to somebody: a leftover
  # server on the port passed the old "is anything listening" check.
  if "$VENV/bin/python" "$SOURCE_DIR/solo_ports.py" owns "$pid" "$PORT" 2>>"$LOG"; then
    socket_ok=1
  fi

  if tail -c +"$((LOG_OFFSET + 1))" "$LOG" 2>/dev/null | grep -Eq "$BIND_FAILURES"; then
    log "ERROR: the new server could not bind port $PORT ('Address already in use' / 'zmq PUB socket error' above); another process holds it:"
    port_owners
    kill_new_server
    exit 9
  fi

  # A dead stats listener means the plugin never sees a kill or a death.
  if tail -c +"$((LOG_OFFSET + 1))" "$LOG" 2>/dev/null | grep -q "zmq error"; then
    log "ERROR: shinqlx's stats listener failed to connect (see 'zmq error' above); kills and deaths would never reach the Solo plugin."
    kill_new_server
    exit 8
  fi

  if [ "$plugin_ok" -eq 1 ] && [ "$socket_ok" -eq 1 ]; then
    # The listener thread starts right after plugin load; give it a moment to fail.
    sleep 1
    if tail -c +"$((LOG_OFFSET + 1))" "$LOG" 2>/dev/null | grep -q "zmq error"; then
      log "ERROR: shinqlx's stats listener failed to connect (see 'zmq error' above); kills and deaths would never reach the Solo plugin."
      kill_new_server
      exit 8
    fi
    log "HEALTH OK: PID alive, Director plugin handshake verified for $MODE, server owns 127.0.0.1:$PORT"
    log "---- Solo Engine startup successful ----"
    exit 0
  fi
done

log "ERROR: startup timed out; plugin_ok=$plugin_ok socket_ok=$socket_ok"
log "Port $PORT owners:"; port_owners
kill_new_server
if [ -f "$PLUGIN_READY" ]; then log "plugin_ready.json:"; cat "$PLUGIN_READY" >>"$LOG" 2>&1; fi
if [ -f "$HOME_PATH/minqlx.log" ]; then log "minqlx.log tail:"; tail -n 120 "$HOME_PATH/minqlx.log" >>"$LOG" 2>&1; fi
tail -n 160 "$LOG" | sed 's/^/[server-tail] /' | tee -a "$LOG" >/dev/null
exit 7
