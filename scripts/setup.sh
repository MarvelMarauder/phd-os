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
    read -p "Todoist API token: " TODOIST_TOKEN
    read -p "Ollama model name [phd-email-parser]: " OLLAMA_MODEL
    OLLAMA_MODEL="${OLLAMA_MODEL:-phd-email-parser}"

    cat > "$CONFIG_FILE" <<EOF
{
    "todoist_token": "$TODOIST_TOKEN",
    "ollama_model": "$OLLAMA_MODEL",
    "ollama_url": "http://localhost:11434/api/generate",
    "outlook_queue_folder": "LLM Queue",
    "outlook_processed_folder": "LLM Processed"
}
EOF
    echo "Config saved."
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
sed \
    -e "s|__PYTHON3__|$PYTHON3|g" \
    -e "s|__SCRIPT_PATH__|$SCRIPT_PATH|g" \
    "$REPO_DIR/scripts/com.phd-os.pipeline.plist" > "$PLIST_DST"

# Load it (unload first in case it was already loaded)
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "LaunchAgent installed and running."
echo

echo "=== Setup complete ==="
echo
echo "The pipeline will run every 2 hours while your Mac is on."
echo "To review pending tasks:  python3 scripts/review_tasks.py"
echo "To check the log:         cat /tmp/phd_os_pipeline.log"
echo "To rebuild the model:     bash scripts/model/build_model.sh"
echo "To uninstall:             launchctl unload $PLIST_DST"
