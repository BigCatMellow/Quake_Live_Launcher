#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="$HOME/.local/share/quake-live-launcher"
if [ -d "$INSTALL_DIR" ]; then
  find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name solo_runtime -exec rm -rf {} +
fi
rm -f "$HOME/.local/bin/quake-live-launcher"
rm -f "$HOME/.local/share/applications/quake-live-launcher.desktop"
echo "Quake Live Launcher removed."
echo "Your Quake Live files and settings were left intact."
if [ -d "$INSTALL_DIR/solo_runtime" ]; then
  echo "The downloaded Solo Engine runtime was preserved at: $INSTALL_DIR/solo_runtime"
fi
