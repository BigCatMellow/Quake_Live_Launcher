#!/usr/bin/env bash
# Stops the Solo server, and any stale Solo server still holding the port.
# SIGTERM first; a server that ignores it for 5 seconds gets SIGKILL. shinqlx's
# stats listener is a non-daemon thread that never exits, so a polite stop is
# not always enough. Only processes running our own qzeroded.x64 are touched.
set -u
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
QLDS_BIN="$RUNTIME/qlds/qzeroded.x64"
PIDFILE="$RUNTIME/server.pid"
PORT="${QLL_SOLO_PORT:-27960}"
TERM_WAIT="${QLL_STOP_TERM_WAIT:-5}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-stop.log"
log(){ printf '[%s] [stop] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
PY="$RUNTIME/.venv/bin/python"; [ -x "$PY" ] || PY=python3
ports(){ "$PY" "$SOURCE_DIR/solo_ports.py" "$@"; }

alive(){ kill -0 "$1" 2>/dev/null && ! grep -qs '^State:[[:space:]]*Z' "/proc/$1/status"; }

stop_pid(){
  local pid="$1" why="$2" i
  alive "$pid" || return 0
  log "Stopping Solo server PID $pid ($why)"
  kill -TERM "$pid" 2>>"$LOG" || true
  for i in $(seq 1 $((TERM_WAIT * 5))); do alive "$pid" || { log "PID $pid stopped"; return 0; }; sleep 0.2; done
  log "PID $pid ignored SIGTERM for ${TERM_WAIT}s; sending SIGKILL"
  kill -KILL "$pid" 2>>"$LOG" || true
  for i in $(seq 1 25); do alive "$pid" || { log "PID $pid killed"; return 0; }; sleep 0.2; done
  log "ERROR: PID $pid survived SIGKILL"
  return 1
}

log "Stop requested (port $PORT)"
rc=0
if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  log "PIDFILE=$PIDFILE PID=$pid"
  if [ -n "$pid" ] && alive "$pid"; then
    if ports is-solo "$pid" "$QLDS_BIN"; then
      stop_pid "$pid" "from PID file" || rc=1
    else
      log "PID $pid is not a Solo server (stale PID file); leaving it alone"
    fi
  else
    log "No live server process for stored PID"
  fi
  rm -f "$PIDFILE"
else
  log "No PID file present"
fi

# Servers the PID file no longer knows about: an older launcher's server that
# survived its stop, or one that fell back to port+1 because this one was taken.
for pid in $(ports servers "$PORT" "$QLDS_BIN" 2>>"$LOG"); do
  stop_pid "$pid" "stale server for port $PORT" || rc=1
done
rm -f "$RUNTIME/plugin_ready.json"
log "Stop complete"
exit "$rc"
