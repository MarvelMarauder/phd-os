#!/bin/bash
# PhD OS Email Pipeline — first-time setup
# Run from the repo root: bash scripts/setup.sh

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_PATH="$REPO_DIR/scripts/email_pipeline.py"
PYTHON3="$(which python3)"
CONFIG_FILE="$HOME/.phd_os_config.json"
PLIST_DST="$HOME/Library/LaunchAgents/com.phd-os.email-pipeline.plist"

echo "=== PhD OS Email Pipeline Setup ==="
echo

# 1. Config file
if [ -f "$CONFIG_FILE" ]; then
    echo "Config file already exists at $CONFIG_FILE"
else
    echo "Creating config file at $CONFIG_FILE"
    echo "You will need your Todoist API token."
    echo "Get it at: https://app.todoist.com/app/settings/integrations/developer"
    echo

    # -s hides the token from terminal display while typing
    read -s -p "Todoist API token: " TODOIST_TOKEN; echo
    read -p "Ollama model name [phd-email-parser]: " OLLAMA_MODEL
    OLLAMA_MODEL="${OLLAMA_MODEL:-phd-email-parser}"

    # Write config via Python so the token is JSON-encoded correctly
    # (handles special characters) and the file is created with 600 permissions.
    # The token is passed through an env var — never interpolated into shell code.
    TODOIST_TOKEN="$TODOIST_TOKEN" OLLAMA_MODEL="$OLLAMA_MODEL" \
    python3 - <<'PYEOF'
import json, os, stat

config = {
    "todoist_token":        os.environ["TODOIST_TOKEN"],
    "ollama_model":         os.environ.get("OLLAMA_MODEL", "phd-email-parser"),
    "ollama_url":           "http://localhost:11434/api/generate",
    "outlook_queue_folder":     "LLM Queue",
    "outlook_processed_folder": "LLM Processed",
    "max_emails_per_run":   20
}

cfg_path = os.path.expanduser("~/.phd_os_config.json")
with open(cfg_path, "w") as f:
    json.dump(config, f, indent=4)
# 600 = owner read/write only
os.chmod(cfg_path, stat.S_IRUSR | stat.S_IWUSR)
print("Config saved (permissions: 600 — readable only by you).")
PYEOF
fi
echo

# 2. Pull base model
echo "Pulling llama3.2:3b from Ollama (this may take a few minutes)..."
ollama pull llama3.2:3b
echo

# 3. Build custom model
echo "Building custom phd-email-parser model..."
bash "$REPO_DIR/scripts/model/build_model.sh"
echo

# 4. Install LaunchAgent
echo "Installing LaunchAgent (runs pipeline every 2 hours)..."
# Use Python for substitution so paths with special chars are handled safely
PYTHON3="$PYTHON3" SCRIPT_PATH="$SCRIPT_PATH" \
python3 - <<'PYEOF'
import os, re

src = os.path.join(os.environ.get("REPO_DIR", "."), "scripts", "com.phd-os.pipeline.plist")
# Resolve relative path
repo_dir = os.path.abspath(os.path.dirname(os.environ.get("SCRIPT_PATH", ".")))
src = os.path.normpath(os.path.join(repo_dir, "..", "scripts", "com.phd-os.pipeline.plist"))

with open(src) as f:
    plist = f.read()

plist = plist.replace("__PYTHON3__", os.environ["PYTHON3"])
plist = plist.replace("__SCRIPT_PATH__", os.environ["SCRIPT_PATH"])

dst = os.path.expanduser("~/Library/LaunchAgents/com.phd-os.email-pipeline.plist")
with open(dst, "w") as f:
    f.write(plist)
print(f"Plist written to {dst}")
PYEOF

# Load it (unload first in case it was already loaded)
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "LaunchAgent installed and running."
echo

echo "=== Setup complete ==="
echo
echo "The pipeline will run every 2 hours while your Mac is on."
echo "To test right now (safe — reads only, moves nothing):"
echo "  python3 scripts/email_pipeline.py --dry-run"
echo "To review pending tasks:  python3 scripts/review_tasks.py"
echo "To check the log:         cat /tmp/phd_os_pipeline.log"
echo "To check the audit log:   cat /tmp/phd_os_audit.log"
echo "To rebuild the model:     bash scripts/model/build_model.sh"
echo "To uninstall:             launchctl unload $PLIST_DST"
