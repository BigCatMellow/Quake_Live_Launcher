#!/usr/bin/env bash
set -u
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"; LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"; PIDFILE="$RUNTIME/server.pid"; mkdir -p "$LOG_DIR"; LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-stop.log"
log(){ printf '[%s] [stop] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
log "Stop requested"
if [ -f "$PIDFILE" ]; then pid=$(cat "$PIDFILE" 2>/dev/null || true); log "PIDFILE=$PIDFILE PID=$pid"; if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>>"$LOG" || true; for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done; if kill -0 "$pid" 2>/dev/null; then log "PID $pid still alive after SIGTERM"; else log "PID $pid stopped"; fi; else log "No live server process found for stored PID"; fi; rm -f "$PIDFILE"; else log "No PID file present"; fi
rm -f "$RUNTIME/plugin_ready.json"
log "Stop complete"
