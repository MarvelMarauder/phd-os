"""
PhD OS Email Pipeline — headless background runner.

Reads emails from Apple Mail 'LLM Queue' via AppleScript,
parses with a local Ollama model, and appends proposed tasks to the queue file.
Run automatically via LaunchAgent; review and approve with review_tasks.py.

Config: ~/.phd_os_config.json
Queue:  ~/.phd_os_queue.json
Log:    /tmp/phd_os_pipeline.log
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

CONFIG_FILE = os.path.expanduser("~/.phd_os_config.json")
QUEUE_FILE  = os.path.expanduser("~/.phd_os_queue.json")
LOG_FILE    = "/tmp/phd_os_pipeline.log"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        log("ERROR: Config not found at ~/.phd_os_config.json — run setup.sh first.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── AppleScript helpers ───────────────────────────────────────────────────────

def run_as(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


def run_as_file(path):
    r = subprocess.run(["osascript", path], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


# ── Apple Mail ────────────────────────────────────────────────────────────────

def mail_running():
    out, _ = run_as('tell application "System Events" to return (name of processes) contains "Mail"')
    return out.strip() == "true"


def ensure_mail_open():
    if mail_running():
        return True
    log("Apple Mail not running — launching it...")
    subprocess.run(["open", "-a", "Mail"])
    import time
    for _ in range(12):
        time.sleep(5)
        if mail_running():
            time.sleep(6)  # let accounts sync
            log("Apple Mail ready.")
            return True
    log("WARNING: Apple Mail did not launch in time — skipping this run.")
    return False


# Fetches all messages from LLM Queue, writes each body to /tmp/phd_email_<id>.txt,
# saves PDF attachments to /tmp/phd_att_<id>_<name>.pdf,
# and returns tab-delimited lines: id \t subject \t sender \t date \t pipe-separated-pdf-names
FETCH_SCRIPT = """\
tell application "Mail"
    -- Find LLM Queue mailbox across all accounts
    set queueMailbox to missing value
    repeat with acct in every account
        try
            set queueMailbox to mailbox "LLM Queue" of acct
            exit repeat
        end try
    end repeat
    if queueMailbox is missing value then
        return "ERROR:LLM Queue not found in any Mail account"
    end if

    set theMessages to messages of queueMailbox
    if (count of theMessages) is 0 then return "EMPTY"

    set output to ""
    repeat with msg in theMessages
        set msgId to (id of msg) as string

        -- Write body to temp file
        set bodyPath to "/tmp/phd_email_" & msgId & ".txt"
        try
            set bodyText to content of msg
            set fRef to open for access POSIX file bodyPath with write permission
            set eof of fRef to 0
            write bodyText to fRef as \xc2\xabclass utf8\xc2\xbb
            close access fRef
        on error
            do shell script "touch " & quoted form of bodyPath
        end try

        -- Save any PDF attachments
        set pdfNames to ""
        try
            repeat with att in mail attachments of msg
                set attName to name of att
                if attName ends with ".pdf" then
                    set attPath to "/tmp/phd_att_" & msgId & "_" & attName
                    save att in POSIX file attPath
                    set pdfNames to pdfNames & attName & "|"
                end if
            end repeat
        end try

        set output to output & msgId & "\t" & (subject of msg) & "\t" \\
            & (sender of msg) & "\t" & ((date received of msg) as string) \\
            & "\t" & pdfNames & "\n"
    end repeat
    return output
end tell
"""

# Moves all messages from LLM Queue → LLM Processed in one batch.
# Run after all emails have been processed and queued.
MOVE_ALL_SCRIPT = """\
tell application "Mail"
    set queueMailbox to missing value
    set destMailbox to missing value
    repeat with acct in every account
        try
            set queueMailbox to mailbox "LLM Queue" of acct
            try
                set destMailbox to mailbox "LLM Processed" of acct
            end try
            exit repeat
        end try
    end repeat
    if queueMailbox is missing value then return "ERROR:no queue"
    if destMailbox is missing value then return "ERROR:no processed folder — create it in Apple Mail"
    set theMessages to messages of queueMailbox
    repeat with msg in theMessages
        move msg to destMailbox
    end repeat
    return "OK:" & (count of theMessages) as string
end tell
"""


def fetch_emails(queue_folder):
    """Read emails from Apple Mail LLM Queue. Returns list of email dicts."""
    # Write fetch script to temp file to avoid shell quoting issues
    import tempfile, os
    script_path = "/tmp/phd_fetch.applescript"
    with open(script_path, "w") as f:
        f.write(FETCH_SCRIPT.replace("LLM Queue", queue_folder))

    raw, code = run_as_file(script_path)

    if raw.startswith("ERROR:"):
        log(f"AppleScript error: {raw[6:]}")
        return []
    if raw == "EMPTY" or not raw.strip():
        log("LLM Queue is empty.")
        return []

    emails = []
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        msg_id, subject, sender, date_str, pdf_names_raw = parts[0], parts[1], parts[2], parts[3], parts[4]

        # Read body from temp file
        body_path = f"/tmp/phd_email_{msg_id}.txt"
        try:
            with open(body_path, encoding="utf-8", errors="replace") as f:
                body = f.read()[:4000]
            os.remove(body_path)
        except FileNotFoundError:
            body = ""

        # Read any saved PDF text
        pdf_texts = []
        for pdf_name in filter(None, pdf_names_raw.split("|")):
            att_path = f"/tmp/phd_att_{msg_id}_{pdf_name}"
            pdf_texts.append(extract_pdf_text(att_path))
            try:
                os.remove(att_path)
            except FileNotFoundError:
                pass

        emails.append({
            "id":      msg_id,
            "subject": subject,
            "from":    sender,
            "date":    date_str,
            "body":    body,
            "pdfs":    pdf_texts,
        })

    return emails


def move_all_to_processed(processed_folder):
    script = MOVE_ALL_SCRIPT.replace("LLM Processed", processed_folder)
    script_path = "/tmp/phd_move.applescript"
    with open(script_path, "w") as f:
        f.write(script)
    out, _ = run_as_file(script_path)
    return out


# ── PDF extraction ────────────────────────────────────────────────────────────

def extract_pdf_text(path):
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(path)
        return text.strip()[:3000]
    except ImportError:
        return "[PDF — install pdfminer.six to extract text: pip3 install pdfminer.six]"
    except Exception as e:
        return f"[PDF extraction failed: {e}]"


# ── Ollama ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a PhD research assistant helping Taylor parse emails into actionable to-do items.
Taylor is a 2nd-year IS PhD student. His two research streams are:
  - AI Companions: how people form relationships with AI agents
  - JudgyAI: how algorithmic judgment affects human decision-making

His recurring task categories:
  - readings: papers to read, literature to review
  - research: experiments, writing, IRB, data collection, analysis
  - admin: university admin, forms, bureaucracy, deadlines
  - church: Taylor also helps with church work
  - personal: personal errands, family, health

Return ONLY a JSON object, no markdown fences, no extra text:
{
  "summary": "One sentence: what this email is about and what action it requires",
  "todos": [
    {
      "task": "Clear imperative action under 80 chars",
      "priority": 1,
      "project_hint": "readings | research | admin | church | personal",
      "due_suggestion": "today | this week | YYYY-MM-DD | no rush"
    }
  ]
}

Rules:
- Only create tasks for real action items. Ignore FYI-only content.
- For attached papers or links to papers: task = "Read: [title]", project_hint = "readings"
- priority: 1=urgent (hard deadline soon), 2=high, 3=normal, 4=low
- If no action items exist, return {"summary": "...", "todos": []}
- Tasks should be specific enough to act on without rereading the email\
"""


def ask_ollama(prompt, model, url):
    payload = json.dumps({
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
        return data.get("response", "")
    except Exception as e:
        log(f"Ollama error: {e}")
        return ""


def parse_email(email, model, ollama_url):
    parts = [
        f"FROM: {email['from']}",
        f"DATE: {email['date']}",
        f"SUBJECT: {email['subject']}",
        "",
        "BODY:",
        email["body"],
    ]
    for i, pdf in enumerate(email["pdfs"], 1):
        parts += ["", f"PDF ATTACHMENT {i}:", pdf[:2000]]

    prompt = SYSTEM_PROMPT + "\n\n---\n\n" + "\n".join(parts)
    raw = ask_ollama(prompt, model, ollama_url)
    if not raw:
        return None

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# ── Queue file ────────────────────────────────────────────────────────────────

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return {"batches": []}
    with open(QUEUE_FILE) as f:
        return json.load(f)


def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    model            = cfg.get("ollama_model", "llama3.2:3b")
    ollama_url       = cfg.get("ollama_url", "http://localhost:11434/api/generate")
    queue_folder     = cfg.get("outlook_queue_folder", "LLM Queue")
    processed_folder = cfg.get("outlook_processed_folder", "LLM Processed")

    log(f"Starting pipeline run (model: {model})")

    if not ensure_mail_open():
        return

    emails = fetch_emails(queue_folder)
    if not emails:
        return

    log(f"Found {len(emails)} email(s) in '{queue_folder}'")

    queue       = load_queue()
    batch_emails = []
    parse_errors = 0

    for email in emails:
        log(f"  Parsing: {email['subject']}")
        result = parse_email(email, model, ollama_url)
        if result is None:
            log(f"  WARNING: Could not parse LLM response for '{email['subject']}'")
            parse_errors += 1
            continue

        batch_emails.append({
            "subject": email["subject"],
            "from":    email["from"],
            "date":    email["date"],
            "summary": result.get("summary", ""),
            "todos":   result.get("todos", []),
        })
        n = len(result.get("todos", []))
        log(f"  → {n} task(s) proposed")

    # Move all emails to Processed in one batch
    move_result = move_all_to_processed(processed_folder)
    if move_result.startswith("ERROR:"):
        log(f"  WARNING: Could not move emails: {move_result[6:]}")
    else:
        log(f"  Moved emails to '{processed_folder}'")

    if batch_emails:
        queue["batches"].append({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "emails": batch_emails,
        })
        save_queue(queue)
        total = sum(len(e["todos"]) for e in batch_emails)
        log(f"Queued {total} task(s) across {len(batch_emails)} email(s). Run review_tasks.py to approve.")
    elif parse_errors:
        log(f"No tasks queued ({parse_errors} parse error(s)).")
    else:
        log("No tasks generated this run.")


if __name__ == "__main__":
    main()
