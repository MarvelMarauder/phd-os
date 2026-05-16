#!/usr/bin/env bash
# build_app.sh — creates PhD OS Review.app using osacompile.
#
# osacompile produces an AppleScript-based .app signed by Apple's own scripting
# framework, so Gatekeeper accepts it without quarantine issues.
#
# Run once; then drag the app from ~/Applications to your Dock.

set -euo pipefail

VAULT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_PATH="${VAULT_DIR}/scripts/review_server.py"
APP_DEST="$HOME/Applications/PhD OS Review.app"
PORT=7891

# Find the python3 that's on PATH right now (conda/pyenv-aware).
PYTHON3="$(which python3)"

echo "Building PhD OS Review.app (osacompile) ..."
echo "  Vault:   $VAULT_DIR"
echo "  Python:  $PYTHON3"
echo "  App:     $APP_DEST"

TMP_AS="$(mktemp /tmp/phd_os_launcher.XXXXXX.applescript)"

cat > "$TMP_AS" <<ENDSCRIPT
-- PhD OS Review launcher
-- If the review server is already running, open the UI in the browser.
-- If not, start it (the server opens the browser itself after 0.5 s).
set serverPort to ${PORT}
set scriptPath to "${SCRIPT_PATH}"
set python3Path to "${PYTHON3}"

set isRunning to false
try
    do shell script "lsof -iTCP:" & serverPort & " -sTCP:LISTEN -t"
    set isRunning to true
on error
end try

if isRunning then
    open location "http://localhost:" & serverPort
else
    do shell script python3Path & " " & quoted form of scriptPath & " >> /tmp/phd_os_review.log 2>&1 &"
end if
ENDSCRIPT

rm -rf "$APP_DEST"
osacompile -o "$APP_DEST" "$TMP_AS"
rm -f "$TMP_AS"

echo ""
echo "Done!  Drag \"PhD OS Review\" from ~/Applications into your Dock."
echo "Log: /tmp/phd_os_review.log"
