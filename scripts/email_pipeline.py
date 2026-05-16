"""
PhD OS Email Pipeline — headless background runner.

Reads emails from Apple Mail 'LLM Queue' via AppleScript,
parses with a local Ollama model, and appends proposed tasks to the queue file.

SCOPE LIMITS (what this script can and cannot do):
  - Reads ONLY from the folder named in outlook_queue_folder (default: LLM Queue)
  - Moves ONLY to the folder named in outlook_processed_folder (default: LLM Processed)
  - Never sends email, never deletes email, never reads other folders
  - Writes only to: /tmp/phd_* (temp, cleaned up), ~/.phd_os_queue.json, logs
  - Creates Todoist tasks only after human approval in review_tasks.py

Flags:
  --dry-run   Parse emails and print proposed tasks; write nothing, move nothing
  --no-move   Process emails but leave them in LLM Queue (don't move to Processed)

Config: ~/.phd_os_config.json
Queue:  ~/.phd_os_queue.json
Log:    /tmp/phd_os_pipeline.log
Audit:  /tmp/phd_os_audit.log
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

CONFIG_FILE = os.path.expanduser("~/.phd_os_config.json")
QUEUE_FILE  = os.path.expanduser("~/.phd_os_queue.json")
LOG_FILE    = "/tmp/phd_os_pipeline.log"
AUDIT_FILE  = "/tmp/phd_os_audit.log"

DEFAULT_MAX_EMAILS = 20  # safety cap; override with max_emails_per_run in config


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        log("ERROR: Config not found at ~/.phd_os_config.json — run setup.sh first.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def audit(event, detail=""):
    """Separate audit trail: records every read, move, and task-creation action."""
    ts = datetime.now().isoformat()
    line = json.dumps({"ts": ts, "event": event, "detail": detail})
    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Folder guard ──────────────────────────────────────────────────────────────

SAFE_FOLDER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _-]{0,62}[A-Za-z0-9]$|^[A-Za-z0-9]$')
FORBIDDEN_FOLDERS = {
    "inbox", "sent", "sent items", "drafts", "deleted items",
    "trash", "junk", "junk e-mail", "archive", "all mail",
    "spam", "outbox", "flagged", "notes",
}

def assert_safe_folder_name(name, label):
    """
    Validates folder names before embedding them in AppleScript strings.
    Only letters, digits, spaces, hyphens, and underscores are allowed.
    This prevents AppleScript injection via a crafted config value.
    """
    name = name.strip()
    if not name:
        log(f"ERROR: {label} folder name is empty — refusing to run.")
        sys.exit(1)
    if not SAFE_FOLDER_RE.match(name):
        log(f"ERROR: {label} folder name '{name}' contains invalid characters.")
        log("       Only letters, numbers, spaces, hyphens, and underscores are allowed.")
        log("       This prevents AppleScript injection. Edit ~/.phd_os_config.json.")
        sys.exit(1)
    if name.lower() in FORBIDDEN_FOLDERS:
        log(f"ERROR: {label} folder name '{name}' is a system folder — refusing to run.")
        log("       Set a custom name in ~/.phd_os_config.json (e.g. 'LLM Queue').")
        sys.exit(1)


# ── AppleScript helpers ───────────────────────────────────────────────────────

def run_as_file(path):
    r = subprocess.run(["osascript", path], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


# ── Apple Mail ────────────────────────────────────────────────────────────────

def mail_running():
    r = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to return (name of processes) contains "Mail"'],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


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


def build_fetch_script(queue_folder, max_emails):
    """
    Fetches up to max_emails messages from queue_folder ONLY.
    Writes each body to the secure temp directory (mode 700).
    Returns tab-delimited lines: id \\t subject \\t sender \\t date \\t pipe-separated-pdf-names
    Folder name is regex-validated before this is called.
    """
    tmp = _secure_tmp
    return f"""\
tell application "Mail"
    -- SCOPE GUARD: only operate on the explicitly configured folder
    set targetName to "{queue_folder}"
    set queueMailbox to missing value
    repeat with acct in every account
        try
            set mb to first mailbox of acct whose name is targetName
            set queueMailbox to mb
            exit repeat
        on error
        end try
    end repeat
    if queueMailbox is missing value then
        return "ERROR:Mailbox '" & targetName & "' not found in any Mail account"
    end if

    set allMessages to messages of queueMailbox
    set msgCount to count of allMessages
    if msgCount is 0 then return "EMPTY"

    -- Cap to max_emails
    set capCount to {max_emails}
    if msgCount < capCount then set capCount to msgCount

    set output to ""
    repeat with i from 1 to capCount
        set msg to item i of allMessages
        set msgId to (id of msg) as string

        -- Write body to private temp dir (mode 700 — only current user can read)
        set bodyPath to "{tmp}/" & msgId & ".txt"
        try
            set bodyText to content of msg
            set fRef to open for access POSIX file bodyPath with write permission
            set eof of fRef to 0
            write bodyText to fRef as «class utf8»
            close access fRef
        on error
            try
                set fRef to open for access POSIX file bodyPath with write permission
                set eof of fRef to 0
                close access fRef
            end try
        end try

        -- Save PDF attachments to private temp dir
        set pdfNames to ""
        try
            repeat with att in mail attachments of msg
                set attName to name of att
                if attName ends with ".pdf" then
                    set attPath to "{tmp}/att_" & msgId & "_" & attName
                    save att in POSIX file attPath
                    set pdfNames to pdfNames & attName & "|"
                end if
            end repeat
        end try

        set output to output & msgId & tab & (subject of msg) & tab & (sender of msg) & tab & ((date received of msg) as string) & tab & pdfNames & return
    end repeat
    return output
end tell
"""


def build_move_script(queue_folder, processed_folder):
    """
    Moves ALL messages from queue_folder to processed_folder ONLY.
    Both folder names are validated before this is called.
    """
    return f"""\
tell application "Mail"
    set srcName to "{queue_folder}"
    set dstName to "{processed_folder}"

    -- SCOPE GUARD: find both folders before touching anything
    set srcMailbox to missing value
    set dstMailbox to missing value
    repeat with acct in every account
        try
            set srcMailbox to first mailbox of acct whose name is srcName
            try
                set dstMailbox to first mailbox of acct whose name is dstName
            end try
            exit repeat
        on error
        end try
    end repeat

    if srcMailbox is missing value then return "ERROR:Source folder not found: " & srcName
    if dstMailbox is missing value then return "ERROR:Destination folder not found: " & dstName

    -- Only move; never delete
    set theMessages to messages of srcMailbox
    repeat with msg in theMessages
        move msg to dstMailbox
    end repeat
    return "OK:" & (count of theMessages) as string
end tell
"""


# ── Email fetching ────────────────────────────────────────────────────────────

# Private temp directory — mode 700 so other users on the machine cannot read
# email bodies or attachment content written here.
_secure_tmp = None
_temp_files  = []


def make_secure_tmp():
    """Create a private temp directory readable only by the current user."""
    global _secure_tmp
    _secure_tmp = tempfile.mkdtemp(prefix="phd_os_")
    os.chmod(_secure_tmp, stat.S_IRWXU)   # rwx------
    return _secure_tmp


def fetch_emails(queue_folder, max_emails, dry_run):
    script_path = os.path.join(_secure_tmp, "fetch.applescript")
    _temp_files.append(script_path)

    with open(script_path, "w") as f:
        f.write(build_fetch_script(queue_folder, max_emails))

    audit("FETCH_START", f"folder={queue_folder} max={max_emails} dry_run={dry_run}")
    raw, code = run_as_file(script_path)

    if raw.startswith("ERROR:"):
        log(f"AppleScript error: {raw[6:]}")
        audit("FETCH_ERROR", raw[6:])
        return []
    if raw == "EMPTY" or not raw.strip():
        log("LLM Queue is empty.")
        audit("FETCH_EMPTY")
        return []

    emails = []
    for line in raw.strip().splitlines():  # splitlines() handles \r, \n, \r\n
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        msg_id, subject, sender, date_str, pdf_names_raw = (
            parts[0], parts[1], parts[2], parts[3], parts[4]
        )

        body_path = os.path.join(_secure_tmp, f"{msg_id}.txt")
        _temp_files.append(body_path)
        try:
            with open(body_path, encoding="utf-8", errors="replace") as f:
                body = f.read()[:4000]
        except FileNotFoundError:
            body = ""

        pdf_texts = []
        for pdf_name in filter(None, pdf_names_raw.split("|")):
            att_path = os.path.join(_secure_tmp, f"att_{msg_id}_{pdf_name}")
            _temp_files.append(att_path)
            pdf_texts.append(extract_pdf_text(att_path))

        audit("EMAIL_READ", f"id={msg_id} subject={subject!r} from={sender!r}")
        emails.append({
            "id":      msg_id,
            "subject": subject,
            "from":    sender,
            "date":    date_str,
            "body":    body,
            "pdfs":    pdf_texts,
        })

    return emails


def deduplicate_by_thread(emails):
    """
    When a whole conversation is in the queue at once (e.g. manually moved),
    keep only the latest message per thread instead of processing every reply.
    Thread identity = subject with Re:/Fwd: prefixes stripped.
    Apple Mail returns messages in date order, so the last occurrence wins.
    """
    seen = {}
    for i, email in enumerate(emails):
        key = re.sub(r'^(re|fwd?|fw):\s*', '', email["subject"], flags=re.IGNORECASE).strip().lower()
        seen[key] = i
    kept_indices = set(seen.values())
    result = [e for i, e in enumerate(emails) if i in kept_indices]
    dropped = len(emails) - len(result)
    if dropped:
        log(f"  Skipped {dropped} earlier message(s) in same thread(s) — keeping latest per subject.")
        audit("DEDUP", f"dropped={dropped} kept={len(result)}")
    return result


def move_all_to_processed(queue_folder, processed_folder, dry_run):
    if dry_run:
        log("  [dry-run] Would move emails to LLM Processed — skipped.")
        return
    script_path = os.path.join(_secure_tmp, "move.applescript")
    _temp_files.append(script_path)
    with open(script_path, "w") as f:
        f.write(build_move_script(queue_folder, processed_folder))
    out, _ = run_as_file(script_path)
    if out.startswith("ERROR:"):
        log(f"  WARNING: Could not move emails: {out[6:]}")
        audit("MOVE_ERROR", out[6:])
    else:
        count = out.replace("OK:", "")
        log(f"  Moved {count} email(s) to '{processed_folder}'")
        audit("MOVE_OK", f"count={count} dest={processed_folder}")


def cleanup_temp_files():
    for path in _temp_files:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    # Remove the secure temp directory itself
    if _secure_tmp and os.path.isdir(_secure_tmp):
        try:
            os.rmdir(_secure_tmp)
        except OSError:
            pass  # non-empty means cleanup above missed something; leave it


# ── PDF extraction ────────────────────────────────────────────────────────────

def extract_pdf_text(path):
    try:
        from pdfminer.high_level import extract_text
        return extract_text(path).strip()[:3000]
    except ImportError:
        return "[PDF — install pdfminer.six: pip3 install pdfminer.six]"
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
            return json.loads(r.read().decode()).get("response", "")
    except Exception as e:
        log(f"Ollama error: {e}")
        return ""


def parse_email(email, model, ollama_url):
    parts = [
        f"FROM: {email['from']}",
        f"DATE: {email['date']}",
        f"SUBJECT: {email['subject']}",
        "", "BODY:", email["body"],
    ]
    for i, pdf in enumerate(email["pdfs"], 1):
        parts += ["", f"PDF ATTACHMENT {i}:", pdf[:2000]]

    raw = ask_ollama(SYSTEM_PROMPT + "\n\n---\n\n" + "\n".join(parts), model, ollama_url)
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
    # Write to a temp file then atomically rename — prevents corruption on crash.
    # Also ensures the queue file stays 600 (owner-only).
    tmp_path = QUEUE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp_path, QUEUE_FILE)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run  = "--dry-run" in sys.argv
    no_move  = "--no-move" in sys.argv or dry_run

    cfg = load_config()
    model            = cfg.get("ollama_model", "llama3.2:3b")
    ollama_url       = cfg.get("ollama_url", "http://localhost:11434/api/generate")
    queue_folder     = cfg.get("outlook_queue_folder", "LLM Queue")
    processed_folder = cfg.get("outlook_processed_folder", "LLM Processed")
    max_emails       = int(cfg.get("max_emails_per_run", DEFAULT_MAX_EMAILS))

    # Validate folder names before touching anything
    assert_safe_folder_name(queue_folder, "queue")
    assert_safe_folder_name(processed_folder, "processed")

    mode = " [DRY RUN]" if dry_run else (" [NO MOVE]" if no_move else "")
    log(f"Starting pipeline run (model: {model}){mode}")
    audit("RUN_START", f"model={model} queue={queue_folder} max={max_emails} dry_run={dry_run}")

    try:
        make_secure_tmp()

        if not ensure_mail_open():
            return

        emails = fetch_emails(queue_folder, max_emails, dry_run)
        if not emails:
            return

        emails = deduplicate_by_thread(emails)
        log(f"Found {len(emails)} email(s) in '{queue_folder}'")

        queue        = load_queue()
        batch_emails = []
        parse_errors = 0

        for email in emails:
            log(f"  Parsing: {email['subject']}")
            result = parse_email(email, model, ollama_url)
            if result is None:
                log(f"  WARNING: Could not parse LLM response for '{email['subject']}'")
                audit("PARSE_ERROR", f"subject={email['subject']!r}")
                parse_errors += 1
                continue

            todos = result.get("todos", [])
            batch_emails.append({
                "subject": email["subject"],
                "from":    email["from"],
                "date":    email["date"],
                "summary": result.get("summary", ""),
                "todos":   todos,
            })
            log(f"  → {len(todos)} task(s) proposed")
            audit("PARSE_OK", f"subject={email['subject']!r} todos={len(todos)}")

        # Move emails (unless suppressed)
        if not no_move:
            move_all_to_processed(queue_folder, processed_folder, dry_run=False)
        else:
            log(f"  Leaving emails in '{queue_folder}' (--no-move or --dry-run).")

        # Write to queue (unless dry run)
        if batch_emails and not dry_run:
            queue["batches"].append({
                "run_at": datetime.now(timezone.utc).isoformat(),
                "emails": batch_emails,
            })
            save_queue(queue)
            total = sum(len(e["todos"]) for e in batch_emails)
            log(f"Queued {total} task(s) across {len(batch_emails)} email(s). Run review_tasks.py to approve.")
            audit("QUEUE_WRITTEN", f"total_todos={total}")
        elif dry_run:
            # Print proposed tasks for inspection
            print("\n── Dry run results ──────────────────────────────────")
            for e in batch_emails:
                print(f"\n  From: {e['from']}\n  Re:   {e['subject']}")
                print(f"  Summary: {e['summary']}")
                for t in e["todos"]:
                    print(f"    • [{t.get('priority','?')}] {t.get('task','')}  ({t.get('due_suggestion','')})")
            print("\n── No files written, no emails moved ────────────────")
        else:
            log(f"No tasks generated ({parse_errors} parse error(s))." if parse_errors else "No tasks generated.")

        audit("RUN_END", "ok")

    finally:
        cleanup_temp_files()


if __name__ == "__main__":
    main()
