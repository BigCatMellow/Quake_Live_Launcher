#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/quake-live-launcher"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APP_DIR"

cp "$SOURCE_DIR/launcher.py" "$INSTALL_DIR/launcher.py"
cp "$SOURCE_DIR/launcher_gui.py" "$INSTALL_DIR/launcher_gui.py"
cp "$SOURCE_DIR/qll_update.py" "$INSTALL_DIR/qll_update.py"
rm -f "$INSTALL_DIR"/launcher_impl.py.gz.b64part* "$INSTALL_DIR"/launcher_gui_impl.py.gz.b64part*
cp "$SOURCE_DIR"/launcher_impl.py.gz.b64part* "$INSTALL_DIR/"
cp "$SOURCE_DIR"/launcher_gui_impl.py.gz.b64part* "$INSTALL_DIR/"
rm -rf "$INSTALL_DIR/resources" "$INSTALL_DIR/solo_engine"
cp -R "$SOURCE_DIR/resources" "$INSTALL_DIR/resources"
cp -R "$SOURCE_DIR/solo_engine" "$INSTALL_DIR/solo_engine"
chmod +x "$INSTALL_DIR/launcher.py" "$INSTALL_DIR/launcher_gui.py" "$INSTALL_DIR/qll_update.py"
chmod +x "$INSTALL_DIR/solo_engine"/*.sh "$INSTALL_DIR/solo_engine/sync_maps.py"

# Record what was installed. A release archive carries release.json (written by
# CI) and keeps auto-updating from GitHub; a source checkout is recorded as a
# local install, which the updater never overwrites automatically.
if [ -f "$SOURCE_DIR/release.json" ]; then
    cp "$SOURCE_DIR/release.json" "$INSTALL_DIR/version.json"
else
    python3 "$SOURCE_DIR/qll_update.py" --record-local "$SOURCE_DIR"
fi
INSTALLED_VERSION="$(python3 "$SOURCE_DIR/qll_update.py" --installed-version)"

# The shortcut checks GitHub for a newer release (release installs only),
# updates if needed, then starts the launcher.
cat > "$BIN_DIR/quake-live-launcher" <<EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/qll_update.py" --launch "\$@"
EOF
chmod +x "$BIN_DIR/quake-live-launcher"

cat > "$APP_DIR/quake-live-launcher.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Quake Live Launcher
Comment=Quick Play, Arcade and Solo Quake Live modes
Exec=$BIN_DIR/quake-live-launcher
Icon=applications-games
Terminal=false
Categories=Game;
StartupNotify=true
EOF
chmod +x "$APP_DIR/quake-live-launcher.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo
echo "Installed/updated Quake Live Launcher $INSTALLED_VERSION."
echo "Open your application menu and search for: Quake Live Launcher"
echo
echo "No sudo/admin access was used."
