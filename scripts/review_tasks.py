"""
PhD OS Task Review — interactive approval CLI.

Loads the accumulated task queue from ~/.phd_os_queue.json,
walks you through each proposed task, and creates approved ones in Todoist.

Usage: python3 scripts/review_tasks.py
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

CONFIG_FILE = os.path.expanduser("~/.phd_os_config.json")
QUEUE_FILE  = os.path.expanduser("~/.phd_os_queue.json")

TODOIST_BASE = "https://api.todoist.com/api/v1"
PRIORITY_MAP = {1: 4, 2: 3, 3: 2, 4: 1}  # our 1=urgent → Todoist 4=urgent


# ── Config & queue ────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("ERROR: ~/.phd_os_config.json not found. Run setup first.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return {"batches": []}
    with open(QUEUE_FILE) as f:
        return json.load(f)


def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)


# ── Todoist ───────────────────────────────────────────────────────────────────

def todoist_get(path, token):
    req = urllib.request.Request(
        f"{TODOIST_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def todoist_post(path, body, token):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{TODOIST_BASE}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def get_projects(token):
    data = todoist_get("/projects", token)
    items = data.get("results", data) if isinstance(data, dict) else data
    return {p["name"].lower(): p["id"] for p in items}


def create_task(task_text, priority, project_id, due_string, token):
    body = {"content": task_text, "priority": PRIORITY_MAP.get(priority, 1)}
    if project_id:
        body["project_id"] = project_id
    if due_string and due_string not in ("no rush", ""):
        body["due_string"] = due_string
    return todoist_post("/tasks", body, token)


# ── Review UI ─────────────────────────────────────────────────────────────────

def fmt_priority(p):
    return {1: "URGENT", 2: "High", 3: "Normal", 4: "Low"}.get(p, "Normal")


def fmt_date(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return iso


def prompt(msg, options="y/n"):
    while True:
        ans = input(f"  {msg} [{options}] ").strip().lower()
        if ans == "":
            ans = options.split("/")[0]
        if ans in options.split("/"):
            return ans
        print(f"  Please enter one of: {options}")


def review_queue(queue, projects, token):
    batches = queue.get("batches", [])
    if not batches:
        print("Queue is empty — nothing to review.")
        return queue

    total_todos = sum(
        len(e["todos"])
        for b in batches
        for e in b.get("emails", [])
    )
    print(f"\nFound {total_todos} proposed task(s) across {len(batches)} batch run(s).\n")

    # Pre-flight summary so you know exactly what might get created before you decide anything
    total_proposed = sum(len(e["todos"]) for b in batches for e in b.get("emails", []))
    print(f"  {len(batches)} batch run(s) · {total_proposed} proposed task(s) total")
    print(f"  You will approve each task individually before anything is sent to Todoist.\n")

    # Collect all approved tasks first, then create at the end
    # (so a crash mid-review doesn't partially create tasks)
    approved = []
    batches_to_keep = []

    for batch in batches:
        run_at = fmt_date(batch.get("run_at", ""))
        emails = batch.get("emails", [])
        emails_with_remaining_todos = []

        print(f"{'='*60}")
        print(f"Run: {run_at}  ({len(emails)} email(s))")
        print(f"{'='*60}")

        for email in emails:
            subject = email.get("subject", "(no subject)")
            sender  = email.get("from", "")
            summary = email.get("summary", "")
            todos   = email.get("todos", [])

            print(f"\n  From: {sender}")
            print(f"  Re:   {subject}")
            print(f"  Summary: {summary}")

            if not todos:
                print("  (No action items)")
                continue

            remaining_todos = []
            skip_rest = False

            for todo in todos:
                if skip_rest:
                    remaining_todos.append(todo)
                    continue

                task     = todo.get("task", "")
                priority = todo.get("priority", 3)
                hint     = todo.get("project_hint", "").lower()
                due      = todo.get("due_suggestion", "")

                print(f"\n    Task:     {task}")
                print(f"    Priority: {fmt_priority(priority)}  |  Category: {hint}  |  Due: {due}")

                ans = prompt("Add to Todoist?", "y/e/n/s")

                if ans == "y":
                    approved.append((task, priority, hint, due))
                elif ans == "e":
                    new_text = input("    New task text (blank = keep): ").strip()
                    new_due  = input("    Due date (blank = keep): ").strip()
                    approved.append((
                        new_text or task,
                        priority,
                        hint,
                        new_due or due,
                    ))
                elif ans == "s":
                    # Skip remaining todos in this email
                    remaining_todos.append(todo)
                    skip_rest = True
                # "n" = discard

            if remaining_todos:
                emails_with_remaining_todos.append({**email, "todos": remaining_todos})

        if emails_with_remaining_todos:
            batches_to_keep.append({**batch, "emails": emails_with_remaining_todos})

    # Final confirmation before any Todoist writes
    if not approved:
        print("\nNo tasks approved.")
    else:
        print(f"\n{'='*60}")
        print(f"Ready to create {len(approved)} task(s) in Todoist:")
        for task_text, priority, hint, due in approved:
            print(f"  • {task_text}  [{hint}]  {due}")
        print(f"{'='*60}")
        go = input("Create these tasks now? [y/n] ").strip().lower()
        if go != "y":
            print("Cancelled — tasks remain in queue for next time.")
            return queue
        print()
        for task_text, priority, hint, due in approved:
            project_id = projects.get(hint) or projects.get("inbox")
            try:
                create_task(task_text, priority, project_id, due, token)
                print(f"  ✓ {task_text}")
            except Exception as e:
                print(f"  ✗ FAILED ({e}): {task_text}")

    queue["batches"] = batches_to_keep
    return queue


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cfg   = load_config()
    token = cfg.get("todoist_token")
    if not token:
        print("ERROR: todoist_token not set in ~/.phd_os_config.json")
        sys.exit(1)

    print("Loading Todoist projects…")
    try:
        projects = get_projects(token)
    except Exception as e:
        print(f"ERROR: Could not reach Todoist: {e}")
        sys.exit(1)

    queue = load_queue()
    queue = review_queue(queue, projects, token)
    save_queue(queue)
    print("\nQueue saved. Done.")


if __name__ == "__main__":
    main()
