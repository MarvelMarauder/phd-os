"""
Email → Todoist pipeline.

Fetches emails from the 'LLM Queue' folder in Outlook via Microsoft Graph,
runs them through a local Ollama model to extract to-dos, presents them for
approval, then creates Todoist tasks and moves emails to 'LLM Processed'.

Setup:
  1. Register an Azure app with Mail.Read + Mail.ReadWrite delegated permissions
  2. Fill in CLIENT_ID and TENANT_ID below (or set env vars AZURE_CLIENT_ID / AZURE_TENANT_ID)
  3. pip install msal requests
  4. Run: python scripts/email_pipeline.py
"""

import json
import os
import sys
import textwrap
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

CLIENT_ID   = os.environ.get("AZURE_CLIENT_ID",  "YOUR_CLIENT_ID_HERE")
TENANT_ID   = os.environ.get("AZURE_TENANT_ID",  "YOUR_TENANT_ID_HERE")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN",   "YOUR_TODOIST_TOKEN_HERE")

GRAPH_BASE      = "https://graph.microsoft.com/v1.0"
SCOPES          = ["Mail.Read", "Mail.ReadWrite"]
TOKEN_CACHE_FILE = os.path.expanduser("~/.phd_os_token_cache.json")

QUEUE_FOLDER     = "LLM Queue"
PROCESSED_FOLDER = "LLM Processed"

OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL   = "http://localhost:11434/api/generate"

TODOIST_BASE         = "https://api.todoist.com/api/v1"
TODOIST_READINGS_PROJECT = os.environ.get("TODOIST_READINGS_PROJECT", "")  # optional: filter to Readings project


# ── MSAL auth (device code flow) ─────────────────────────────────────────────

def _load_cache():
    import msal
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        cache.deserialize(open(TOKEN_CACHE_FILE).read())
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())


def get_access_token():
    import msal
    cache = _load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    # Try silent first (cached token)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # Device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow failed: {flow}")
    print("\n" + flow["message"] + "\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")
    _save_cache(cache)
    return result["access_token"]


# ── Microsoft Graph helpers ───────────────────────────────────────────────────

def _graph_get(token, path, params=None):
    url = f"{GRAPH_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def _graph_post(token, path, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{GRAPH_BASE}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode()) if r.length else {}


def find_folder(token, name):
    data = _graph_get(token, "/me/mailFolders", {"$filter": f"displayName eq '{name}'", "$select": "id,displayName"})
    items = data.get("value", [])
    return items[0]["id"] if items else None


def fetch_queue_emails(token, folder_id, max_emails=10):
    data = _graph_get(
        token,
        f"/me/mailFolders/{folder_id}/messages",
        {
            "$select": "id,subject,from,receivedDateTime,body,hasAttachments",
            "$top":    max_emails,
            "$orderby": "receivedDateTime asc",
        },
    )
    return data.get("value", [])


def fetch_attachments(token, message_id):
    data = _graph_get(token, f"/me/messages/{message_id}/attachments")
    return data.get("value", [])


def move_to_processed(token, message_id, processed_folder_id):
    _graph_post(token, f"/me/messages/{message_id}/move", {"destinationId": processed_folder_id})


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_body_text(msg):
    body = msg.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")
    if content_type == "html":
        # Strip HTML tags with a simple regex-free approach
        import re
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"&nbsp;", " ", content)
        content = re.sub(r"&amp;", "&", content)
        content = re.sub(r"&lt;", "<", content)
        content = re.sub(r"&gt;", ">", content)
        content = re.sub(r"\s{3,}", "\n\n", content)
    return content.strip()[:4000]  # cap for LLM context


def extract_pdf_text(attachment_data_b64):
    try:
        import base64
        import io
        pdf_bytes = base64.b64decode(attachment_data_b64)
        # Try pdfminer if available
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))
        return text.strip()[:3000]
    except ImportError:
        return "[PDF attachment — install pdfminer.six to extract text]"
    except Exception as e:
        return f"[PDF extraction failed: {e}]"


# ── Ollama LLM ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a PhD research assistant. You will be given the content of an email (and optionally PDF attachments). Your job is to extract actionable to-do items from it.

Return ONLY a JSON object in this exact format — no extra text, no markdown fences:
{
  "summary": "One-sentence summary of what this email is about",
  "todos": [
    {
      "task": "Clear, actionable task description",
      "priority": 1,
      "project_hint": "readings | admin | research | church | personal",
      "due_suggestion": "today | this week | no rush | YYYY-MM-DD"
    }
  ]
}

Rules:
- Only include real action items. Ignore FYI-only content.
- For attached papers: create a 'Read: [paper title]' task with project_hint 'readings'.
- Keep task descriptions concise (under 80 characters).
- If there are no action items, return {"summary": "...", "todos": []}.
- priority: 1=urgent, 2=high, 3=normal, 4=low"""


def ask_ollama(prompt):
    payload = json.dumps({
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    return data.get("response", "")


def parse_email(msg, attachment_texts):
    subject    = msg.get("subject", "(no subject)")
    sender     = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
    received   = msg.get("receivedDateTime", "")
    body_text  = extract_body_text(msg)

    parts = [
        f"FROM: {sender}",
        f"DATE: {received[:10]}",
        f"SUBJECT: {subject}",
        "",
        "BODY:",
        body_text,
    ]
    for i, att_text in enumerate(attachment_texts, 1):
        parts += ["", f"ATTACHMENT {i}:", att_text[:2000]]

    full_prompt = SYSTEM_PROMPT + "\n\n---\n\n" + "\n".join(parts)

    raw = ask_ollama(full_prompt)
    # Find the JSON object in the response
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# ── Todoist ───────────────────────────────────────────────────────────────────

def _todoist_post(path, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{TODOIST_BASE}{path}",
        data=data,
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def _todoist_get(path):
    req = urllib.request.Request(
        f"{TODOIST_BASE}{path}",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def get_todoist_projects():
    data = _todoist_get("/projects")
    return {p["name"].lower(): p["id"] for p in data.get("results", data if isinstance(data, list) else [])}


PRIORITY_MAP = {1: 4, 2: 3, 3: 2, 4: 1}  # Todoist: 4=urgent, 1=normal


def create_todoist_task(task_text, priority, project_id=None, due_string=None):
    body = {
        "content":  task_text,
        "priority": PRIORITY_MAP.get(priority, 1),
    }
    if project_id:
        body["project_id"] = project_id
    if due_string and due_string not in ("no rush",):
        body["due_string"] = due_string
    return _todoist_post("/tasks", body)


# ── Approval loop ─────────────────────────────────────────────────────────────

def present_and_approve(subject, summary, todos, projects):
    print(f"\n{'='*60}")
    print(f"EMAIL: {subject}")
    print(f"SUMMARY: {summary}")
    print(f"{'='*60}")

    if not todos:
        print("  (No action items found)")
        resp = input("\nMark as processed and skip? [Y/n] ").strip().lower()
        return [], resp != "n"

    approved = []
    for i, todo in enumerate(todos):
        task     = todo.get("task", "")
        priority = todo.get("priority", 3)
        hint     = todo.get("project_hint", "").lower()
        due      = todo.get("due_suggestion", "")

        print(f"\n  [{i+1}] {task}")
        print(f"       Priority: {priority}  |  Project hint: {hint}  |  Due: {due}")

        while True:
            resp = input(f"       Add this task? [Y/n/e(dit)] ").strip().lower()
            if resp in ("", "y"):
                approved.append(todo)
                break
            elif resp == "n":
                break
            elif resp == "e":
                new_text = input("       New task text: ").strip()
                if new_text:
                    todo = dict(todo, task=new_text)
                approved.append(todo)
                break

    print(f"\n  Approved {len(approved)}/{len(todos)} tasks.")
    proceed = input("  Create these tasks in Todoist and move email to Processed? [Y/n] ").strip().lower()
    return approved, proceed != "n"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if CLIENT_ID == "YOUR_CLIENT_ID_HERE":
        print("ERROR: Set CLIENT_ID and TENANT_ID at the top of this script (or via env vars).")
        sys.exit(1)

    print("Authenticating with Microsoft…")
    token = get_access_token()
    print("Authenticated.")

    queue_id     = find_folder(token, QUEUE_FOLDER)
    processed_id = find_folder(token, PROCESSED_FOLDER)

    if not queue_id:
        print(f"ERROR: Could not find '{QUEUE_FOLDER}' folder in your mailbox.")
        sys.exit(1)
    if not processed_id:
        print(f"WARNING: '{PROCESSED_FOLDER}' folder not found — emails will not be moved after processing.")

    print(f"\nFetching emails from '{QUEUE_FOLDER}'…")
    emails = fetch_queue_emails(token, queue_id)
    if not emails:
        print("No emails in queue. Done.")
        return

    print(f"Found {len(emails)} email(s).\n")

    print("Loading Todoist projects…")
    projects = get_todoist_projects()

    for msg in emails:
        subject = msg.get("subject", "(no subject)")

        # Fetch PDF attachment text if any
        attachment_texts = []
        if msg.get("hasAttachments"):
            attachments = fetch_attachments(token, msg["id"])
            for att in attachments:
                if att.get("contentType", "").lower() == "application/pdf":
                    att_text = extract_pdf_text(att.get("contentBytes", ""))
                    attachment_texts.append(att_text)

        print(f"\nProcessing: {subject}")
        print("  Running through Ollama… ", end="", flush=True)
        result = parse_email(msg, attachment_texts)
        print("done.")

        if result is None:
            print("  WARNING: Could not parse LLM response — skipping.")
            continue

        summary = result.get("summary", "")
        todos   = result.get("todos", [])

        approved, should_process = present_and_approve(subject, summary, todos, projects)

        if not should_process:
            print("  Skipped.")
            continue

        # Create approved tasks in Todoist
        for todo in approved:
            hint     = todo.get("project_hint", "").lower()
            priority = todo.get("priority", 3)
            due      = todo.get("due_suggestion", "")

            # Map hint → project
            project_id = None
            if hint == "readings":
                project_id = projects.get("readings") or TODOIST_READINGS_PROJECT or None
            elif hint in projects:
                project_id = projects[hint]

            task = create_todoist_task(todo["task"], priority, project_id, due if due != "no rush" else None)
            print(f"  ✓ Created: {todo['task']}")

        # Move email to Processed
        if processed_id:
            move_to_processed(token, msg["id"], processed_id)
            print(f"  → Moved to '{PROCESSED_FOLDER}'")

    print("\nDone.")


if __name__ == "__main__":
    main()
