#!/usr/bin/env bash
# Director capability probe.
#
# Runs the local QLDS with the solo_probe plugin instead of the normal Solo
# runtime and answers four questions the Director depends on:
#   1. custom bot files (scripts/*.bot + botfiles/bots/*_c.c) - loaded?
#   2. fractional bot skill (addbot anarki 2.5) - kept or rounded?
#   3. item IDs for spawn_item - which number is which item?
#   4. item lures - do bots go for a dropped Mega Health?
# Results: ~/.local/share/quake-live-launcher/solo_runtime/director_probe.json
#
# Usage: run_director_probe.sh [--watch]
#   --watch  also launch Quake Live connected to the probe (you spectate).
# Takes about two minutes. Stops any running Solo server; your normal Solo
# session file is restored and the temporary probe bot files are removed.
set -u
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"
HOME_PATH="$RUNTIME/home"
VENV="$RUNTIME/.venv"
SESSION="$HOME/.config/quake-live-launcher/solo_session.json"
RESULT="$RUNTIME/director_probe.json"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
PROBE_LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-director-probe.log"
BOT_DEF="$HOME_PATH/baseq3/scripts/qll_probe.bot"
CHAR_FILE="$HOME_PATH/baseq3/botfiles/bots/qll_probe_c.c"
TIMEOUT="${QLL_PROBE_TIMEOUT:-240}"
WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1
BACKUP=""

say(){ printf '[director-probe] %s\n' "$*" | tee -a "$PROBE_LOG"; }

mkdir -p "$LOG_DIR" "$(dirname "$SESSION")" "$(dirname "$BOT_DEF")" "$(dirname "$CHAR_FILE")"
if [ ! -f "$RUNTIME/READY" ] || [ ! -x "$VENV/bin/python" ]; then
  say "The Solo Engine is not set up yet. Open the launcher's SOLO tab and run SET UP / REPAIR SOLO ENGINE first."
  exit 3
fi

cleanup(){
  "$SOURCE_DIR/stop_solo.sh" >/dev/null 2>&1 || true
  rm -f "$BOT_DEF" "$CHAR_FILE"
  if [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then mv -f "$BACKUP" "$SESSION"; else rm -f "$SESSION"; fi
}
trap cleanup EXIT

say "Stopping any running Solo server; your session is backed up and restored afterwards."
"$SOURCE_DIR/stop_solo.sh" >/dev/null 2>&1 || true
if [ -f "$SESSION" ]; then BACKUP="$SESSION.probe-backup-$$"; cp "$SESSION" "$BACKUP"; fi

# Temporary bot definitions. A points at a stock character, B at our own
# character file (Quake 3 characteristic indices: 0 name, 1 gender,
# 37 jumper, 48 walker; anything missing is filled from the default bot).
cat > "$BOT_DEF" <<'EOF'
{
name		QLLProbeA
funname		"QLL Probe A"
model		sarge
aifile		bots/sarge_c.c
}

{
name		QLLProbeB
funname		"QLL Probe B"
model		sarge
aifile		bots/qll_probe_c.c
}
EOF
{
  echo "// Quake Live Launcher Director probe character (temporary, removed after the probe)."
  for skill in 1 4 5; do
    printf 'skill %s\n{\n\t0\t"QLLProbeB"\n\t1\t"male"\n\t37\t1.0\n\t48\t0.0\n}\n\n' "$skill"
  done
} > "$CHAR_FILE"

"$VENV/bin/python" - "$SESSION" "$PROBE_LOG" <<'PY'
import json, sys, time
path, log = sys.argv[1:3]
json.dump({"version": 5, "mode": "horde", "map": "campgrounds", "maps": ["campgrounds"],
           "seed": 1, "game_dir": "", "log_path": log, "created_at": time.time(), "director_probe": True},
          open(path, "w"), indent=2)
PY
rm -f "$RESULT"

say "Starting the local server with the probe plugin (log: $PROBE_LOG)."
if ! QLL_PLUGINS=solo_probe "$SOURCE_DIR/start_solo.sh" >>"$PROBE_LOG" 2>&1; then
  say "The server did not start. See $PROBE_LOG"
  exit 4
fi

if [ "$WATCH" -eq 1 ]; then
  if command -v steam >/dev/null 2>&1; then
    steam -applaunch 282440 +connect 127.0.0.1:27960 >/dev/null 2>&1 &
  elif command -v flatpak >/dev/null 2>&1 && flatpak info com.valvesoftware.Steam >/dev/null 2>&1; then
    flatpak run com.valvesoftware.Steam -applaunch 282440 +connect 127.0.0.1:27960 >/dev/null 2>&1 &
  else
    say "Steam not found; in Quake Live open the console and type: connect 127.0.0.1"
  fi
fi

say "Probe running (about two minutes). Waiting for results..."
for _ in $(seq 1 "$TIMEOUT"); do
  if [ -f "$RESULT" ] && "$VENV/bin/python" -c "import json,sys; sys.exit(0 if json.load(open('$RESULT')).get('done') else 1)" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [ ! -f "$RESULT" ]; then
  say "No results after ${TIMEOUT}s. If nothing happened at all, the server may not run game frames"
  say "without a connected player: rerun with --watch (or connect to 127.0.0.1 in Quake Live)."
  say "Server log: $PROBE_LOG"
  exit 5
fi

"$VENV/bin/python" - "$RESULT" <<'PY' | tee -a "$PROBE_LOG"
import json, sys
r = json.load(open(sys.argv[1]))
v = r.get("verdicts", {})
labels = {
    "custom_bot_files": "Custom .bot files load",
    "custom_character_file": "Custom character file loads",
    "fractional_skill": "Fractional skill kept",
    "item_ids": "Item IDs",
    "mega_health_id": "Mega Health item ID",
    "bot_item_lure": "Bots take a dropped Mega Health",
}
print("\nDirector probe results")
print("----------------------")
for key, label in labels.items():
    print(f"{label:34} {v.get(key)}")
lure = r.get("lure") or {}
if lure.get("control_min") is not None:
    print(f"{'  closest approach (no item/item)':34} {lure.get('control_min'):.0f} / {lure.get('lure_min') or 0:.0f} units")
print(f"\nFull results: {sys.argv[1]}")
print("Please send that file back so the Director work can use the answers.")
PY
