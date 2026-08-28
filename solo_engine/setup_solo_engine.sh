#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HOME/.local/share/quake-live-launcher/solo_runtime"
LOG_DIR="$HOME/.local/share/quake-live-launcher/logs"
STEAMCMD_DIR="$RUNTIME/steamcmd"; QLDS="$RUNTIME/qlds"; VENV="$RUNTIME/.venv"; HOME_PATH="$RUNTIME/home"; PLUGIN_DIR="$QLDS/minqlx-plugins"
mkdir -p "$RUNTIME" "$LOG_DIR" "$STEAMCMD_DIR" "$HOME_PATH/baseq3" "$PLUGIN_DIR"
rm -f "$RUNTIME/READY" "$RUNTIME/SELF_TEST_OK" "$RUNTIME/plugin_ready.json"
SETUP_PID="$RUNTIME/setup.pid"
SETUP_STATUS="$RUNTIME/setup_status"

# Do not launch two compiler-heavy repair jobs at once.
if [ -f "$SETUP_PID" ]; then
  old_pid="$(cat "$SETUP_PID" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    printf 'Solo Engine setup is already running (PID %s).\n' "$old_pid"
    printf 'Current stage: %s\n' "$(cat "$SETUP_STATUS" 2>/dev/null || echo unknown)"
    exit 2
  fi
  rm -f "$SETUP_PID"
fi
printf '%s\n' "$$" > "$SETUP_PID"
printf '%s\n' "Starting Solo Engine setup" > "$SETUP_STATUS"

LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-solo-setup.log"
printf '%s\n' "$LOG" > "$RUNTIME/last_setup_log"
exec > >(tee -a "$LOG") 2>&1
set_status(){ printf '%s\n' "$*" > "$SETUP_STATUS"; }
say(){ set_status "$*"; printf '\n[%s] [setup] == %s ==\n' "$(date '+%F %T')" "$*"; }
log(){ printf '[%s] [setup] %s\n' "$(date '+%F %T')" "$*"; }
fail(){ set_status "FAILED: $*"; log "ERROR: $*"; log "Full setup log: $LOG"; exit 1; }
cleanup(){ rm -f "$SETUP_PID"; }
trap cleanup EXIT
trap 'rc=$?; set_status "FAILED at line $LINENO (exit $rc)"; log "ERROR: command failed at line $LINENO (exit $rc): $BASH_COMMAND"; log "Full setup log: $LOG"; exit $rc' ERR
say "Solo Engine diagnostic setup log started"
log "Log file: $LOG"; log "SOURCE_DIR=$SOURCE_DIR"; log "RUNTIME=$RUNTIME"; log "Architecture=$(uname -m)"; log "Kernel=$(uname -srmo 2>/dev/null || true)"; log "PATH=$PATH"
[ -f /etc/os-release ] && { log "/etc/os-release:"; cat /etc/os-release; }
[ "$(uname -m)" = "x86_64" ] || fail "The current shinqlx route requires 64-bit x86 Linux."
say "Checking Linux build dependencies"
required_pkgs=(python3 python3-dev python3-venv python3-pip redis-server pkg-config libssl-dev git build-essential curl tar lib32gcc-s1 lib32stdc++6 clang libclang-dev); missing_pkgs=()
if command -v dpkg-query >/dev/null 2>&1; then
  for pkg in "${required_pkgs[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'ok installed'; then log "dependency OK: $pkg"; else log "dependency MISSING: $pkg"; missing_pkgs+=("$pkg"); fi
  done
else
  for cmd in curl tar python3 git gcc pkg-config; do if command -v "$cmd" >/dev/null 2>&1; then log "command OK: $cmd -> $(command -v "$cmd")"; else missing_pkgs+=("$cmd"); fi; done
fi
if [ ${#missing_pkgs[@]} -gt 0 ]; then
  log "Missing dependencies: ${missing_pkgs[*]}"
  if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    echo "The Solo Engine needs these for SteamCMD and for compiling shinqlx."; read -r -p "Install the standard dependencies with sudo apt now? [Y/n] " ans; ans=${ans:-Y}
    if [[ "$ans" =~ ^[Yy]$ ]]; then sudo apt-get update; sudo apt-get install -y "${required_pkgs[@]}"; else fail "Dependencies were not installed. Native Arcade modes still work without the Solo Engine."; fi
  else fail "Install the missing compiler/Python/SteamCMD dependencies, then rerun this setup."; fi
fi
say "Installing local SteamCMD"
if [ ! -x "$STEAMCMD_DIR/steamcmd.sh" ]; then log "Downloading SteamCMD"; curl -L --fail --retry 3 -o "$RUNTIME/steamcmd_linux.tar.gz" https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz; tar -xzf "$RUNTIME/steamcmd_linux.tar.gz" -C "$STEAMCMD_DIR"; else log "SteamCMD already present: $STEAMCMD_DIR/steamcmd.sh"; fi
"$STEAMCMD_DIR/steamcmd.sh" +quit >/dev/null 2>&1 || log "WARNING: SteamCMD smoke test returned non-zero"
say "Installing/updating Quake Live Dedicated Server (Steam app 349090)"
log "Running SteamCMD app_update 349090 validate"; "$STEAMCMD_DIR/steamcmd.sh" +force_install_dir "$QLDS" +login anonymous +app_update 349090 validate +quit
[ -x "$QLDS/qzeroded.x64" ] || fail "QLDS did not install qzeroded.x64 as expected."
log "QLDS binary: $(ls -lh "$QLDS/qzeroded.x64")"; if command -v ldd >/dev/null 2>&1; then log "QLDS ldd output (plain) follows:"; ldd "$QLDS/qzeroded.x64" || true; log "QLDS ldd output with runtime LD_LIBRARY_PATH=$QLDS follows:"; LD_LIBRARY_PATH="$QLDS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ldd "$QLDS/qzeroded.x64" || true; fi
say "Preparing Python environment"
log "system python: $(command -v python3)"; python3 --version; if [ ! -d "$VENV" ]; then python3 -m venv "$VENV"; fi; "$VENV/bin/python" --version; "$VENV/bin/python" -m pip install --upgrade pip wheel
# Prefer an already-installed Rust toolchain. Re-running rustup-init on every
# repair made an otherwise healthy local setup depend on static.rust-lang.org
# being reachable, even when nightly was already installed.
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
export PATH="$HOME/.cargo/bin:$PATH"

if ! command -v rustup >/dev/null 2>&1; then
  say "Installing Rust toolchain locally for shinqlx"
  log "rustup is not installed; downloading rustup-init."
  if ! curl --proto '=https' --tlsv1.2 --fail --retry 3 -sSf https://sh.rustup.rs | sh -s -- -y --profile default; then
    fail "Rust is not installed and rustup could not be downloaded. Check DNS/Internet access to static.rust-lang.org, then run Solo Engine setup again."
  fi
  [ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
  export PATH="$HOME/.cargo/bin:$PATH"
else
  say "Using existing Rust installation"
  log "Existing rustup found; skipping rustup installer download."
fi

command -v rustup >/dev/null 2>&1 || fail "rustup was not found after Rust setup."
log "rustup: $(command -v rustup)"; rustup --version
log "Installed Rust toolchains:"; rustup toolchain list || true

if rustup toolchain list | grep -Eq '^nightly(-[^ ]+)?([[:space:]]|$)'; then
  log "Nightly Rust toolchain already installed; skipping network install."
else
  log "Nightly Rust is missing; downloading it now."
  if ! rustup toolchain install nightly --profile default; then
    fail "Nightly Rust is required for shinqlx but could not be installed. Check DNS/Internet access to static.rust-lang.org and run setup again."
  fi
fi

if rustup +nightly component list --installed 2>/dev/null | grep -q '^rust-src'; then
  log "nightly rust-src component already installed."
else
  log "Installing nightly rust-src component."
  if ! rustup +nightly component add rust-src; then
    fail "The nightly rust-src component is required for shinqlx but could not be installed."
  fi
fi

# shinqlx currently requires nightly-only Cargo -Z functionality. Do not
# change the user's global Rust default; force nightly only for this build and
# for every child process spawned by pip/maturin.
export RUSTUP_TOOLCHAIN=nightly
log "RUSTUP_TOOLCHAIN=$RUSTUP_TOOLCHAIN"
log "cargo selected for shinqlx: $(command -v cargo || true)"
log "rustc selected for shinqlx: $(command -v rustc || true)"
cargo --version
rustc --version
if ! cargo -Z help >/dev/null 2>&1; then
  fail "Nightly Cargo is not active. shinqlx requires nightly Rust (-Z options). Try Solo Engine setup again after rustup finishes installing nightly."
fi
log "Nightly Cargo preflight passed (-Z options accepted)."

say "Locating libclang for Rust bindgen"
LIBCLANG_SO="$(find /usr/lib /usr/local/lib -type f \( -name 'libclang.so' -o -name 'libclang.so.*' -o -name 'libclang-*.so' -o -name 'libclang-*.so.*' \) 2>/dev/null | sort -V | tail -n 1 || true)"
if [ -z "$LIBCLANG_SO" ]; then
  fail "libclang was not found. shinqlx uses Rust bindgen and requires libclang. Install the Ubuntu/Mint package 'libclang-dev' (and preferably 'clang'), then run setup again."
fi
export LIBCLANG_PATH="$(dirname "$LIBCLANG_SO")"
log "libclang selected: $LIBCLANG_SO"
log "LIBCLANG_PATH=$LIBCLANG_PATH"
if command -v clang >/dev/null 2>&1; then
  log "clang: $(command -v clang)"
  clang --version | head -n 2 || true
else
  log "WARNING: clang executable not found. libclang is sufficient for normal bindgen use, but the 'clang' package is recommended."
fi
if [ ! -r "$LIBCLANG_SO" ]; then
  fail "Detected libclang is not readable: $LIBCLANG_SO"
fi

say "Building/installing shinqlx in the private venv"
"$VENV/bin/python" -m pip install --upgrade maturin
log "Installing shinqlx with RUSTUP_TOOLCHAIN=nightly"
"$VENV/bin/python" -m pip install --upgrade -v shinqlx
log "pip show shinqlx:"; "$VENV/bin/python" -m pip show shinqlx || true
log "shinqlx shared libraries:"; find "$VENV" -type f -path '*/shinqlx/*.so' -print || true
say "Installing Solo Engine v5 plugin package"
rm -rf "$PLUGIN_DIR"
mkdir -p "$PLUGIN_DIR/modes"
cp "$SOURCE_DIR/plugins/__init__.py" "$PLUGIN_DIR/__init__.py"
for file in solo_arcade.py solo_directed.py solo_director.py director_runtime.py director_learning.py solo_controller.py solo_core.py; do cp "$SOURCE_DIR/plugins/$file" "$PLUGIN_DIR/$file"; done
cp "$SOURCE_DIR/plugins/modes/__init__.py" "$PLUGIN_DIR/modes/__init__.py"
for file in "$SOURCE_DIR"/plugins/modes/*.py; do [ -f "$file" ] && cp "$file" "$PLUGIN_DIR/modes/$(basename "$file")"; done
cp "$SOURCE_DIR/sync_maps.py" "$RUNTIME/sync_maps.py"; chmod +x "$RUNTIME/sync_maps.py"
log "Plugin directory contents:"; find "$PLUGIN_DIR" -maxdepth 2 -type f -print | sort

cat > "$HOME_PATH/baseq3/server.cfg" <<EOF
set sv_hostname "Quake Live // Solo Engine v5"
set sv_maxClients "16"
set sv_privateClients "0"
set sv_pure "0"
set g_password ""
set bot_enable "1"
set bot_thinktime "0"
set bot_challenge "1"
set bot_minplayers "0"
set g_doWarmup "0"
set g_warmup "0"
set sv_warmupReadyPercentage "0"
set g_warmupDelay "0"
set g_friendlyFire "0"
set g_teamForceBalance "0"
set g_training "1"
set g_teamSizeMin "0"
set g_teamSizeMax "0"
set fraglimit "0"
set timelimit "0"
set capturelimit "0"
set roundlimit "0"
set scorelimit "0"
set g_spawnItemWeapons "0"
set g_spawnItemAmmo "0"
set qlx_pluginsPath "$PLUGIN_DIR"
set qlx_plugins "solo_directed"
set qlx_commandPrefix "!"
EOF
log "server.cfg written: $HOME_PATH/baseq3/server.cfg"

say "Running real QLDS/shinqlx/plugin self-test"
if "$SOURCE_DIR/self_test.sh"; then
  touch "$RUNTIME/SELF_TEST_OK"
  touch "$RUNTIME/READY"
  say "Solo Engine verified and ready"
  set_status "READY"
  log "SELF_TEST_OK=$RUNTIME/SELF_TEST_OK"
  log "READY=$RUNTIME/READY"
else
  fail "The Solo Engine installed but its real QLDS/shinqlx/plugin self-test failed. Open the latest self-test/start log."
fi
log "Full setup log: $LOG"
echo "The Solo Engine v5 is verified. The launcher can now start all advertised Solo modes."; echo; echo "Press Enter to close this terminal."; read -r _ || true
