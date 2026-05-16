#!/usr/bin/env bash
# build_app.sh — creates PhD OS Tasks.app in ~/Applications/
# Double-click the app to launch the Task Review web UI.

set -euo pipefail

VAULT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="PhD OS Tasks"
APP_DEST="$HOME/Applications/${APP_NAME}.app"

echo "Building ${APP_NAME}.app ..."
echo "  Vault: $VAULT_DIR"
echo "  Destination: $APP_DEST"

# ── Bundle skeleton ─────────────────────────────────────────────────────────
rm -rf "$APP_DEST"
mkdir -p "$APP_DEST/Contents/MacOS"
mkdir -p "$APP_DEST/Contents/Resources"

# ── Launcher script (the "binary" macOS will run) ────────────────────────────
LAUNCHER="$APP_DEST/Contents/MacOS/${APP_NAME}"
cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Launched by macOS when the user double-clicks the app.
# Activate conda/pyenv shims if present.
export PATH="\$HOME/.pyenv/shims:\$HOME/opt/anaconda3/bin:\$HOME/anaconda3/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH"
exec python3 "${VAULT_DIR}/scripts/review_server.py"
LAUNCHER_EOF
chmod +x "$LAUNCHER"

# ── Info.plist ────────────────────────────────────────────────────────────────
cat > "$APP_DEST/Contents/Info.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key>       <string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key>        <string>com.phd-os.task-review</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleExecutable</key>        <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleSignature</key>         <string>????</string>
  <key>LSUIElement</key>               <false/>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST_EOF

# ── Optional: copy a custom icon if one exists ────────────────────────────────
ICON_SRC="${VAULT_DIR}/scripts/phd_os_tasks.icns"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$APP_DEST/Contents/Resources/AppIcon.icns"
  /usr/libexec/PlistBuddy -c \
    "Add :CFBundleIconFile string AppIcon" \
    "$APP_DEST/Contents/Info.plist" 2>/dev/null || true
  echo "  Custom icon applied."
fi

# ── Refresh Launch Services so Finder picks up the new app ───────────────────
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/\
LaunchServices.framework/Versions/A/Support/lsregister \
  -f "$APP_DEST" 2>/dev/null || true

echo ""
echo "Done!  Open ~/Applications and double-click \"${APP_NAME}\"."
echo ""
echo "To add it to your Dock: drag it from ~/Applications onto the Dock."
echo ""
echo "To set a custom icon:"
echo "  1. Find a PNG you want, open it in Preview."
echo "  2. Edit → Select All, then Edit → Copy."
echo "  3. Right-click the app → Get Info."
echo "  4. Click the small icon in the top-left of the Get Info window, then Cmd-V."
