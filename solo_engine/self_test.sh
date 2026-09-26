#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"
VENV="$RUNTIME/.venv"
SESSION="$HOME/.config/quake-live-launcher/solo_session.json"
PIDFILE="$RUNTIME/server.pid"
READY_JSON="$RUNTIME/plugin_ready.json"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
mkdir -p "$(dirname "$SESSION")" "$LOG_DIR"
TEST_LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-self-test.log"
BACKUP=""

cleanup(){
  if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; sleep 0.5; fi
    rm -f "$PIDFILE"
  fi
  rm -f "$READY_JSON"
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
    mv -f "$BACKUP" "$SESSION"
  else
    rm -f "$SESSION"
  fi
}
trap cleanup EXIT

if [ -f "$SESSION" ]; then
  BACKUP="$SESSION.selftest-backup-$$"
  cp "$SESSION" "$BACKUP"
fi

"$VENV/bin/python" - "$SESSION" "$TEST_LOG" <<'PY'
import json,sys,time
path,log=sys.argv[1:3]
data={
  "version":5,"mode":"horde","map":"campgrounds","maps":["campgrounds"],
  "map_pools":{"normal":["campgrounds"]},"skill":2,"difficulty":"normal",
  "length":2,"seed":424242,"continue_run":False,"game_dir":"",
  "movement":{"air_control":"enhanced","side_thrusters":True,"dash_strength":340,"ground_dash_hop":155,"dash_charges":1},
  "log_path":log,"created_at":time.time(),"self_test":True,
}
open(path,'w').write(json.dumps(data,indent=2)+'\n')
PY

printf '[self-test] launching real QLDS/shinqlx/Director plugin on 127.0.0.1:27961\n' | tee -a "$TEST_LOG"
QLL_SKIP_READY_CHECK=1 QLL_SOLO_PORT=27961 "$SOURCE_DIR/start_solo.sh" >>"$TEST_LOG" 2>&1

"$VENV/bin/python" - "$READY_JSON" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p.get('ready') is True, p
assert p.get('mode') == 'horde', p
print('[self-test] Director plugin handshake verified:', p)
PY

printf '[self-test] PASS: qzeroded + shinqlx + solo_directed initialized successfully\n' | tee -a "$TEST_LOG"