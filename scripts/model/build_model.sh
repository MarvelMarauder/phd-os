#!/bin/bash
# Rebuilds the custom Ollama model from Modelfile + examples.md
# Run this after editing either file.
# Usage: bash scripts/model/build_model.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELFILE="$SCRIPT_DIR/Modelfile"
EXAMPLES="$SCRIPT_DIR/examples.md"
MODEL_NAME="phd-email-parser"

# Extract INPUT/OUTPUT pairs from examples.md and append as few-shot messages
EXAMPLES_BLOCK=""
if [ -f "$EXAMPLES" ]; then
    # Parse example blocks and append to Modelfile as MESSAGE pairs
    python3 - <<'PYEOF'
import re, sys

with open("scripts/model/examples.md") as f:
    content = f.read()

# Find all INPUT/OUTPUT pairs
pairs = re.findall(
    r"INPUT:\n(.*?)\nOUTPUT:\n(\{.*?\})",
    content,
    re.DOTALL
)

# Load corrections (lines starting with "- " in corrections.md)
corrections = []
try:
    with open("scripts/model/corrections.md") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("- "):
                corrections.append(stripped[2:])
except FileNotFoundError:
    pass

with open("scripts/model/Modelfile") as f:
    base = f.read().rstrip()

# Remove any existing MESSAGE lines
base = re.sub(r"\nMESSAGE.*", "", base, flags=re.DOTALL)

# Inject corrections into the SYSTEM block before the closing """
if corrections:
    corr_block = "\nLEARNED CORRECTIONS (patterns to avoid, from rejected tasks):\n"
    corr_block += "\n".join(f"- {c}" for c in corrections)
    base = base.replace('\n"""', corr_block + '\n"""', 1)

lines = [base, ""]
for user_text, assistant_text in pairs:
    user_text = user_text.strip().replace('"', '\\"').replace('\n', '\\n')
    assistant_text = assistant_text.strip().replace('"', '\\"').replace('\n', '\\n')
    lines.append(f'MESSAGE user "{user_text}"')
    lines.append(f'MESSAGE assistant "{assistant_text}"')

with open("scripts/model/Modelfile.built", "w") as f:
    f.write("\n".join(lines) + "\n")

print(f"Built Modelfile with {len(pairs)} example(s) and {len(corrections)} correction(s).")
PYEOF
    MODELFILE="$SCRIPT_DIR/Modelfile.built"
fi

echo "Creating Ollama model '$MODEL_NAME'..."
ollama create "$MODEL_NAME" -f "$MODELFILE"
echo "Done. Model '$MODEL_NAME' is ready."
echo "Test it with: ollama run $MODEL_NAME"
