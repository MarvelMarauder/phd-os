"""
PhD OS Email Pipeline — headless background runner.

Reads emails from Outlook 'LLM Queue' via AppleScript (no network auth needed),
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
import tempfile
import urllib.request
from datetime import datetime, timezone

CONFIG_FILE = os.path.expanduser("~/.phd_os_config.json")
QUEUE_FILE  = os.path.expanduser("~/.phd_os_queue.json")
LOG_FILE    = "/tmp/phd_os_pipeline.log"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        log("ERROR: Config not found. Run setup first.")
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


# ── AppleScript / Outlook ─────────────────────────────────────────────────────

def run_as(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


def outlook_running():
    out, _ = run_as('tell application "System Events" to return (name of processes) contains "Microsoft Outlook"')
    return out.strip() == "true"


def ensure_outlook_open():
    if outlook_running():
        return True
    log("Outlook not running — launching it...")
    subprocess.run(["open", "-a", "Microsoft Outlook"])
    # Wait for Outlook to fully load and sync before we try to read folders
    import time
    for i in range(18):   # up to 90 seconds
        time.sleep(5)
        if outlook_running():
            time.sleep(10)  # extra settle time for folder sync
            log("Outlook ready.")
            return True
    log("WARNING: Outlook did not launch in time — skipping this run.")
    return False


FETCH_SCRIPT = """\
tell application "Microsoft Outlook"
    try
        set theAccount to first exchange account
        set queueFolder to mail folder "LLM Queue" of theAccount
        set theMessages to messages of queueFolder
        if (count of theMessages) is 0 then return "EMPTY"
        set output to ""
        repeat with msg in theMessages
            set msgId to (id of msg) as string
            -- Write body to temp file to avoid delimiter escaping issues
            set bodyPath to "/tmp/phd_email_" & msgId & ".txt"
            try
                set bodyText to plain text content of msg
                set fRef to open for access POSIX file bodyPath with write permission
                set eof of fRef to 0
                write bodyText to fRef as \xc2\xabclass utf8\xc2\xbb
                close access fRef
            on error
                do shell script "touch " & quoted form of bodyPath
            end try
            set hasAtt to (count of attachments of msg) > 0
            set attStr to "0"
            if hasAtt then set attStr to "1"
            set output to output & msgId & "\t" & (subject of msg) & "\t" \\
                & (address of sender of msg) & "\t" \\
                & ((time received of msg) as string) & "\t" & attStr & "\n"
        end repeat
        return output
    on error errMsg
        return "ERROR:" & errMsg
    end try
end tell
"""


def move_msg_script(msg_id, dest_folder):
    return f"""\
tell application "Microsoft Outlook"
    try
        set theAccount to first exchange account
        set destFolder to mail folder "{dest_folder}" of theAccount
        set msg to message id {msg_id}
        move msg to destFolder
    on error
    end try
end tell
"""


def save_attachments_script(msg_id, tmp_dir):
    return f"""\
tell application "Microsoft Outlook"
    try
        set msg to message id {msg_id}
        set atts to attachments of msg
        repeat with att in atts
            set attName to name of att
            if attName ends with ".pdf" then
                set savePath to "{tmp_dir}/" & attName
                save att in POSIX file savePath
            end if
        end repeat
    on error
    end try
end tell
"""


def fetch_outlook_emails(queue_folder, processed_folder):
    raw, code = run_as(FETCH_SCRIPT)
    if code != 0 or raw.startswith("ERROR:"):
        log(f"AppleScript error: {raw}")
        return []
    if raw == "EMPTY" or not raw.strip():
        log("LLM Queue is empty.")
        return []

    emails = []
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        msg_id, subject, sender, date_str, has_att = parts[0], parts[1], parts[2], parts[3], parts[4]

        # Read body from temp file
        body_path = f"/tmp/phd_email_{msg_id}.txt"
        try:
            with open(body_path, encoding="utf-8", errors="replace") as f:
                body = f.read()[:4000]
            os.remove(body_path)
        except FileNotFoundError:
            body = ""

        # Save PDF attachments if any
        pdf_texts = []
        if has_att == "1":
            with tempfile.TemporaryDirectory() as tmp_dir:
                run_as(save_attachments_script(msg_id, tmp_dir))
                for fname in os.listdir(tmp_dir):
                    if fname.lower().endswith(".pdf"):
                        pdf_texts.append(extract_pdf_text(os.path.join(tmp_dir, fname)))

        emails.append({
            "id":      msg_id,
            "subject": subject,
            "from":    sender,
            "date":    date_str,
            "body":    body,
            "pdfs":    pdf_texts,
        })

    return emails


def move_to_processed(msg_id, processed_folder):
    run_as(move_msg_script(msg_id, processed_folder))


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
- For attached papers or links to papers: task = "Read: [title or description]", project_hint = "readings"
- priority: 1=urgent (hard deadline soon), 2=high, 3=normal, 4=low
- If no action items exist, return {"summary": "...", "todos": []}
- Keep tasks specific enough to be actionable without rereading the email\
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
    model       = cfg.get("ollama_model", "llama3.2:3b")
    ollama_url  = cfg.get("ollama_url", "http://localhost:11434/api/generate")
    queue_folder     = cfg.get("outlook_queue_folder", "LLM Queue")
    processed_folder = cfg.get("outlook_processed_folder", "LLM Processed")

    log(f"Starting pipeline run (model: {model})")

    if not ensure_outlook_open():
        return

    emails = fetch_outlook_emails(queue_folder, processed_folder)
    if not emails:
        log("Nothing to process.")
        return

    log(f"Found {len(emails)} email(s) in {queue_folder!r}")

    queue = load_queue()
    batch_emails = []

    for email in emails:
        log(f"  Parsing: {email['subject']}")
        result = parse_email(email, model, ollama_url)
        if result is None:
            log(f"  WARNING: Could not parse response for '{email['subject']}' — skipping.")
            # Still move it so it doesn't block the queue
            move_to_processed(email["id"], processed_folder)
            continue

        batch_emails.append({
            "subject": email["subject"],
            "from":    email["from"],
            "date":    email["date"],
            "summary": result.get("summary", ""),
            "todos":   result.get("todos", []),
        })
        move_to_processed(email["id"], processed_folder)
        log(f"  → {len(result.get('todos', []))} task(s) queued, moved to {processed_folder!r}")

    if batch_emails:
        queue["batches"].append({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "emails": batch_emails,
        })
        save_queue(queue)
        total = sum(len(e["todos"]) for e in batch_emails)
        log(f"Queued {total} task(s) across {len(batch_emails)} email(s). Run review_tasks.py to approve.")
    else:
        log("No tasks generated this run.")


if __name__ == "__main__":
    main()
