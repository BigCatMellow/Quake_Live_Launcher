#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SOURCE_DIR/.." && pwd)"
RUNTIME="${QLL_RUNTIME:-$HOME/.local/share/quake-live-launcher/solo_runtime}"
QLDS="$RUNTIME/qlds"
VENV="$RUNTIME/.venv"
HOME_PATH="$RUNTIME/home"
SESSION="${QLL_SESSION:-$HOME/.config/quake-live-launcher/solo_session.json}"
PLUGIN_DIR="$QLDS/minqlx-plugins"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
PIDFILE="$RUNTIME/server.pid"
PORT="${QLL_PORT:-27960}"

mkdir -p "$LOG_DIR" "$PLUGIN_DIR" "$HOME_PATH/baseq3"
LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-v5.log"
log(){ printf '[%s] [v5-start] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
fail(){ log "ERROR: $*"; exit 1; }

[ -x "$QLDS/qzeroded.x64" ] || fail "Missing $QLDS/qzeroded.x64"
[ -x "$VENV/bin/python" ] || fail "Missing $VENV/bin/python"
[ -f "$SESSION" ] || fail "Missing Solo session: $SESSION"

readarray -t SESSION_VALUES < <("$VENV/bin/python" - "$SESSION" <<'PY'
import json, sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
print(x.get('mode','horde'))
print(x.get('map','campgrounds'))
PY
)
MODE="${SESSION_VALUES[0]}"
MAP="${SESSION_VALUES[1]}"

SHINQLX_LIB="$(find "$VENV" -type f -path '*/shinqlx/*.so' -print -quit)"
[ -n "$SHINQLX_LIB" ] || fail "shinqlx shared library not found in $VENV"

rm -rf "$PLUGIN_DIR/modes"
mkdir -p "$PLUGIN_DIR/modes"
cp "$ROOT_DIR/plugins/solo_arcade.py" "$PLUGIN_DIR/solo_arcade.py"
cp "$ROOT_DIR/plugins/solo_controller.py" "$PLUGIN_DIR/solo_controller.py"
cp "$ROOT_DIR/plugins/modes/horde.py" "$PLUGIN_DIR/modes/horde.py"
cp "$ROOT_DIR/plugins/modes/gun_game.py" "$PLUGIN_DIR/modes/gun_game.py"
touch "$PLUGIN_DIR/modes/__init__.py"
cp "$SOURCE_DIR/server.cfg" "$HOME_PATH/baseq3/server.cfg"

if [ -f "$PIDFILE" ]; then
  oldpid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    log "Stopping previous Solo server PID $oldpid"
    kill "$oldpid" || true
    sleep 1
  fi
fi

log "mode=$MODE map=$MAP port=$PORT"
log "shinqlx=$SHINQLX_LIB"
log "ZMQ stats is enabled on the command line before plugin initialization"

(
  cd "$QLDS"
  export VIRTUAL_ENV="$VENV"
  export PATH="$VENV/bin:$PATH"
  unset PYTHONHOME || true
  export LD_PRELOAD="${SHINQLX_LIB}${LD_PRELOAD:+:$LD_PRELOAD}"
  export LD_LIBRARY_PATH="$QLDS/linux64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

  exec "$QLDS/qzeroded.x64" \
    +set fs_basepath "$QLDS" \
    +set fs_homepath "$HOME_PATH" \
    +set net_ip 127.0.0.1 \
    +set net_port "$PORT" \
    +set sv_pure 0 \
    +set zmq_stats_enable 1 \
    +set zmq_stats_ip 127.0.0.1 \
    +set zmq_stats_port "$PORT" \
    +set bot_minplayers 0 \
    +set qlx_pluginsPath "$PLUGIN_DIR" \
    +set qlx_plugins solo_arcade \
    +exec server.cfg \
    +map "$MAP" ffa
) >>"$LOG" 2>&1 &

pid=$!
printf '%s\n' "$pid" > "$PIDFILE"
log "qzeroded PID=$pid"

for attempt in $(seq 1 30); do
  sleep 0.5
  kill -0 "$pid" 2>/dev/null || { tail -n 100 "$LOG"; fail "qzeroded exited during startup"; }
  if command -v ss >/dev/null 2>&1 && ss -lun 2>/dev/null | grep -Eq "(127\\.0\\.0\\.1|0\\.0\\.0\\.0|\\*):${PORT}\\b"; then
    log "server healthy: UDP $PORT listening"
    exit 0
  fi
done

fail "qzeroded stayed alive but UDP $PORT was not visible after 15 seconds"
