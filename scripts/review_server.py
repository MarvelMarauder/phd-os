"""
PhD OS Task Review — local web UI

Starts a local server on http://localhost:7891 and opens your browser.
Shows pending tasks, lets you approve/edit/skip them, run the pipeline,
and add training examples — all without touching the terminal.

Launch: double-click PhD OS Review.app  (built by build_app.sh)
Or run:  python3 scripts/review_server.py
"""

import json
import os
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

CONFIG_FILE   = os.path.expanduser("~/.phd_os_config.json")
QUEUE_FILE    = os.path.expanduser("~/.phd_os_queue.json")
REPO_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_FILE    = os.path.join(REPO_DIR, "scripts", "model", "examples.md")
CORRECTIONS_FILE = os.path.join(REPO_DIR, "scripts", "model", "corrections.md")
PIPELINE_SCRIPT = os.path.join(REPO_DIR, "scripts", "email_pipeline.py")
BUILD_MODEL_SCRIPT = os.path.join(REPO_DIR, "scripts", "model", "build_model.sh")
PORT = 7891
TODOIST_BASE = "https://api.todoist.com/api/v1"
PRIORITY_MAP = {1: 4, 2: 3, 3: 2, 4: 1}

# ── Pipeline state ────────────────────────────────────────────────────────────

_lock            = threading.Lock()
_pipeline_running = False
_pipeline_log    = []
_model_running   = False
_model_log       = []


# ── Queue & config ────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return {"batches": []}
    with open(QUEUE_FILE) as f:
        return json.load(f)


def save_queue(q):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, QUEUE_FILE)


# ── Todoist ───────────────────────────────────────────────────────────────────

def get_projects(token):
    req = urllib.request.Request(
        f"{TODOIST_BASE}/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    items = data.get("results", data) if isinstance(data, dict) else data
    return {p["name"].lower(): p["id"] for p in items}


def create_todoist_task(token, content, priority, project_id, due_string):
    body = {"content": content, "priority": PRIORITY_MAP.get(priority, 1)}
    if project_id:
        body["project_id"] = project_id
    if due_string and due_string not in ("no rush", ""):
        body["due_string"] = due_string
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{TODOIST_BASE}/tasks", data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ── Actions ───────────────────────────────────────────────────────────────────

def approve_tasks(approved_tasks):
    """Create approved tasks in Todoist and remove them from the queue."""
    cfg   = load_config()
    token = cfg.get("todoist_token", "")
    if not token:
        return {"ok": False, "error": "No Todoist token in config."}

    try:
        projects = get_projects(token)
    except Exception as e:
        return {"ok": False, "error": f"Could not reach Todoist: {e}"}

    created = []
    errors  = []
    for task in approved_tasks:
        content    = task.get("task", "").strip()
        priority   = task.get("priority", 3)
        hint       = task.get("project_hint", "").lower()
        due        = task.get("due_suggestion", "")
        project_id = projects.get(hint)
        if not content:
            continue
        try:
            create_todoist_task(token, content, priority, project_id, due)
            created.append(content)
        except Exception as e:
            errors.append(f"{content}: {e}")

    # Remove the approved task objects from the queue
    task_contents = {t.get("task", "").strip() for t in approved_tasks}
    queue = load_queue()
    for batch in queue["batches"]:
        for email in batch.get("emails", []):
            email["todos"] = [
                t for t in email.get("todos", [])
                if t.get("task", "").strip() not in task_contents
            ]
    # Drop emails and batches that are now empty
    for batch in queue["batches"]:
        batch["emails"] = [e for e in batch.get("emails", []) if e.get("todos")]
    queue["batches"] = [b for b in queue["batches"] if b.get("emails")]
    save_queue(queue)

    return {"ok": True, "created": created, "errors": errors}


def dismiss_tasks(task_contents):
    """Remove tasks from queue without creating them in Todoist."""
    task_set = set(task_contents)
    queue = load_queue()
    for batch in queue["batches"]:
        for email in batch.get("emails", []):
            email["todos"] = [
                t for t in email.get("todos", [])
                if t.get("task", "").strip() not in task_set
            ]
        batch["emails"] = [e for e in batch.get("emails", []) if e.get("todos")]
    queue["batches"] = [b for b in queue["batches"] if b.get("emails")]
    save_queue(queue)
    return {"ok": True}


def save_correction(task, reason):
    """Append a dismissed task's reason to corrections.md for model retraining."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    line = f"- Don't generate tasks like \"{task}\". Reason: {reason} ({date_str})\n"
    try:
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        log(f"WARNING: Could not save correction: {e}")


def run_pipeline_bg():
    global _pipeline_running, _pipeline_log
    with _lock:
        if _pipeline_running:
            return {"ok": False, "error": "Pipeline is already running."}
        _pipeline_running = True
        _pipeline_log = ["Starting pipeline…"]

    def worker():
        global _pipeline_running, _pipeline_log
        try:
            proc = subprocess.Popen(
                [sys.executable, PIPELINE_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                cwd=REPO_DIR,
            )
            for line in proc.stdout:
                _pipeline_log.append(line.rstrip())
            proc.wait()
            _pipeline_log.append(f"✓ Done (exit {proc.returncode})")
        except Exception as e:
            _pipeline_log.append(f"✗ Error: {e}")
        finally:
            _pipeline_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


def rebuild_model_bg():
    global _model_running, _model_log
    with _lock:
        if _model_running:
            return {"ok": False, "error": "Model is already rebuilding."}
        _model_running = True
        _model_log = ["Rebuilding model…"]

    def worker():
        global _model_running, _model_log
        try:
            proc = subprocess.Popen(
                ["bash", BUILD_MODEL_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                cwd=REPO_DIR,
            )
            for line in proc.stdout:
                _model_log.append(line.rstrip())
            proc.wait()
            _model_log.append(f"✓ Done (exit {proc.returncode})")
        except Exception as e:
            _model_log.append(f"✗ Error: {e}")
        finally:
            _model_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


def add_example(data):
    """Append a new example to examples.md."""
    desc    = data.get("description", "New example").strip()
    frm     = data.get("from", "").strip()
    subject = data.get("subject", "").strip()
    body    = data.get("body", "").strip()
    summary = data.get("summary", "").strip()
    todos   = data.get("todos", [])

    if not subject or not summary:
        return {"ok": False, "error": "Subject and summary are required."}

    todos_json = json.dumps(
        [{"task": t.get("task",""), "priority": t.get("priority", 3),
          "project_hint": t.get("project_hint", "research"),
          "due_suggestion": t.get("due_suggestion", "no rush")} for t in todos],
        indent=2
    )

    summary_safe = summary.replace('"', '\\"')
    block = f"""
## Example — {desc}

INPUT:
FROM: {frm}
SUBJECT: {subject}
BODY:
{body}

OUTPUT:
{{
  "summary": "{summary_safe}",
  "todos": {todos_json}
}}

---
"""

    try:
        with open(EXAMPLES_FILE, "a", encoding="utf-8") as f:
            f.write(block)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Embedded HTML UI ──────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PhD OS Tasks</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f3efe9;--surface:#fffefb;--surface2:#f0ece5;
  --border:#e2dbd1;--text:#1a1714;--muted:#6b6460;--subtle:#aaa39a;
  --accent:#2a7a50;--accent-dim:#ddf0e6;
  --brand:#b04820;--brand-dim:#f5e7e0;
  --gold:#c07808;--gold-dim:#fef3d4;
  --blue:#2c5fa0;--blue-dim:#dde8f8;
  --danger:#c0392b;--danger-dim:#fde8e6;
  --radius:12px;--radius-sm:8px;
  --shadow:0 2px 8px rgba(0,0,0,0.08);
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:var(--bg);color:var(--text);font-size:14px;line-height:1.5;
  -webkit-font-smoothing:antialiased}

/* ── Layout ── */
header{background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 24px;height:56px;display:flex;align-items:center;gap:16px;
  position:sticky;top:0;z-index:100;box-shadow:var(--shadow)}
.brand{font-weight:800;font-size:16px;color:var(--brand);letter-spacing:-.04em}
.header-sub{font-size:12px;color:var(--subtle);flex:1}
main{max-width:860px;margin:0 auto;padding:28px 20px 80px}

/* ── Buttons ── */
.btn{padding:8px 18px;border-radius:var(--radius-sm);font-size:13px;font-weight:700;
  cursor:pointer;border:none;font-family:inherit;transition:opacity .12s,transform .1s;
  display:inline-flex;align-items:center;gap:6px}
.btn:hover{opacity:.85}.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff}
.btn-brand{background:var(--brand);color:#fff}
.btn-ghost{background:var(--surface2);color:var(--muted);border:1px solid var(--border)}
.btn-danger{background:var(--danger);color:#fff}
.btn-sm{padding:5px 12px;font-size:12px}

/* ── Tabs ── */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--border);margin-bottom:24px}
.tab{padding:10px 18px;font-size:13px;font-weight:600;color:var(--muted);
  cursor:pointer;border-radius:var(--radius-sm) var(--radius-sm) 0 0;
  border:1px solid transparent;border-bottom:none;transition:all .12s}
.tab:hover{background:var(--surface2)}
.tab.active{background:var(--surface);color:var(--brand);
  border-color:var(--border);margin-bottom:-2px;border-bottom-color:var(--surface)}
.tab-panel{display:none}.tab-panel.active{display:block}

/* ── Pipeline controls ── */
.pipeline-bar{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px 20px;margin-bottom:24px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.pipeline-status{flex:1;font-size:13px;color:var(--muted)}
.pipeline-log{background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--radius-sm);padding:12px 14px;font-family:monospace;
  font-size:11.5px;color:var(--muted);max-height:140px;overflow-y:auto;
  margin-top:12px;display:none;line-height:1.6}
.pipeline-log.visible{display:block}
.spinner{width:14px;height:14px;border:2px solid var(--border);
  border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Task queue ── */
.queue-empty{text-align:center;padding:48px 20px;color:var(--subtle);font-size:15px}
.queue-empty strong{display:block;font-size:20px;margin-bottom:8px;color:var(--muted)}

.batch-header{font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:var(--subtle);margin:24px 0 10px}
.batch-header:first-child{margin-top:0}

.email-card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);margin-bottom:12px;overflow:hidden}
.email-card-header{padding:12px 16px;background:var(--surface2);
  border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:10px}
.email-from{font-weight:700;font-size:13px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:200px}
.email-subject{font-size:12px;color:var(--muted);flex:1;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.email-summary{font-size:12px;color:var(--muted);padding:8px 16px 0;font-style:italic}

.task-row{display:flex;align-items:flex-start;gap:10px;padding:10px 16px;
  border-top:1px solid var(--border)}
.task-check{width:16px;height:16px;margin-top:2px;flex-shrink:0;cursor:pointer;
  accent-color:var(--accent)}
.task-body{flex:1;min-width:0}
.task-text{font-size:13px;font-weight:500;color:var(--text);
  background:transparent;border:none;outline:none;width:100%;
  font-family:inherit;line-height:1.4;resize:none;padding:0;cursor:text}
.task-text:focus{background:var(--surface2);border-radius:4px;padding:2px 4px;
  outline:2px solid var(--accent);outline-offset:1px}
.task-meta{display:flex;gap:6px;margin-top:4px;flex-wrap:wrap}
.badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:20px;
  text-transform:uppercase;letter-spacing:.04em}
.badge-p1{background:#fde8e6;color:var(--danger)}
.badge-p2{background:var(--gold-dim);color:var(--gold)}
.badge-p3{background:var(--accent-dim);color:var(--accent)}
.badge-p4{background:var(--surface2);color:var(--subtle)}
.badge-hint{background:var(--blue-dim);color:var(--blue)}
.badge-due{background:var(--surface2);color:var(--muted)}
.task-dismiss{font-size:16px;color:var(--subtle);cursor:pointer;line-height:1;
  padding:2px 6px;border-radius:4px;border:none;background:transparent;
  flex-shrink:0;transition:color .1s,background .1s}
.task-dismiss:hover{color:var(--danger);background:var(--danger-dim)}
.dismiss-form{display:flex;align-items:center;gap:8px;padding:8px 16px 10px;
  background:var(--surface2);border-top:1px solid var(--border)}
.dismiss-reason{flex:1;font-size:12px;padding:5px 8px;border:1px solid var(--border);
  border-radius:4px;background:var(--surface);color:var(--text);font-family:inherit;outline:none}
.dismiss-reason:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.dismiss-reason::placeholder{color:var(--subtle)}

/* ── Approve bar ── */
.approve-bar{position:fixed;bottom:0;left:0;right:0;
  background:var(--surface);border-top:1px solid var(--border);
  padding:14px 24px;display:flex;align-items:center;gap:12px;
  box-shadow:0 -2px 12px rgba(0,0,0,.06);z-index:99}
.approve-bar-info{flex:1;font-size:13px;color:var(--muted)}
.approve-bar-info strong{color:var(--text)}

/* ── Toast ── */
.toast{position:fixed;bottom:72px;left:50%;transform:translateX(-50%);
  background:#1a1714;color:#fff;font-size:13px;padding:10px 20px;
  border-radius:var(--radius-sm);box-shadow:var(--shadow);
  opacity:0;transition:opacity .2s;pointer-events:none;white-space:nowrap;z-index:200}
.toast.show{opacity:1}

/* ── Example editor ── */
.example-form{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:24px;margin-bottom:16px}
.form-group{margin-bottom:16px}
.form-label{display:block;font-size:12px;font-weight:700;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px}
.form-input{width:100%;padding:8px 11px;border:1px solid var(--border);
  border-radius:var(--radius-sm);font-family:inherit;font-size:13px;
  background:var(--surface);color:var(--text);outline:none;
  transition:border-color .12s}
.form-input:focus{border-color:var(--accent);outline:2px solid var(--accent-dim)}
textarea.form-input{resize:vertical;min-height:80px;line-height:1.5}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}

.todo-list{border:1px solid var(--border);border-radius:var(--radius-sm);overflow:hidden}
.todo-item{display:grid;grid-template-columns:1fr 80px 120px 120px 32px;
  gap:8px;padding:8px 10px;border-bottom:1px solid var(--border);align-items:center}
.todo-item:last-child{border-bottom:none}
.todo-add{padding:8px 10px;border-top:1px solid var(--border)}
.todo-input{width:100%;padding:4px 8px;border:1px solid var(--border);
  border-radius:4px;font-size:12px;font-family:inherit;background:var(--surface)}
.todo-select{width:100%;padding:4px 6px;border:1px solid var(--border);
  border-radius:4px;font-size:12px;font-family:inherit;background:var(--surface)}
.todo-remove{background:transparent;border:none;color:var(--subtle);
  cursor:pointer;font-size:16px;padding:2px}
.todo-remove:hover{color:var(--danger)}

/* ── Model section ── */
.model-bar{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px 20px;display:flex;
  align-items:center;gap:12px;flex-wrap:wrap}
.model-info{flex:1;font-size:13px;color:var(--muted)}
.model-log{background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--radius-sm);padding:12px 14px;font-family:monospace;
  font-size:11.5px;color:var(--muted);max-height:120px;overflow-y:auto;
  margin-top:12px;display:none;line-height:1.6}
.model-log.visible{display:block}

/* ── Responsive ── */
@media(max-width:600px){
  .form-row{grid-template-columns:1fr}
  .todo-item{grid-template-columns:1fr 1fr;grid-template-rows:auto auto}
}
</style>
</head>
<body>

<header>
  <span class="brand">PhD OS</span>
  <span class="header-sub" id="header-sub">Loading…</span>
  <button class="btn btn-ghost btn-sm" onclick="stopServer()" id="stop-btn">Stop Server</button>
</header>

<main>
  <div class="tabs">
    <div class="tab active" onclick="switchTab('queue')">Task Queue</div>
    <div class="tab" onclick="switchTab('examples')">Add Example</div>
    <div class="tab" onclick="switchTab('model')">Model</div>
  </div>

  <!-- ── Task Queue tab ── -->
  <div class="tab-panel active" id="tab-queue">
    <div class="pipeline-bar">
      <button class="btn btn-brand" id="run-btn" onclick="runPipeline()">
        ▶ Run Pipeline Now
      </button>
      <div class="pipeline-status" id="pipeline-status">
        Reads your LLM Queue and proposes tasks.
      </div>
      <button class="btn btn-ghost btn-sm" onclick="toggleLog()">Log</button>
    </div>
    <div class="pipeline-log" id="pipeline-log"></div>

    <div id="queue-container"><p class="queue-empty"><strong>Loading…</strong></p></div>
  </div>

  <!-- ── Add Example tab ── -->
  <div class="tab-panel" id="tab-examples">
    <p style="color:var(--muted);margin-bottom:20px;font-size:13px">
      Add a real email as a training example so the model learns to handle it correctly.
      The more examples you add, the sharper the model gets for your specific emails.
    </p>
    <div class="example-form">
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Description (for your reference)</label>
          <input class="form-input" id="ex-desc" placeholder="e.g. Advisor deadline email">
        </div>
        <div class="form-group">
          <label class="form-label">Sender email</label>
          <input class="form-input" id="ex-from" placeholder="advisor@university.edu">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Subject line</label>
        <input class="form-input" id="ex-subject" placeholder="RE: IRB submission deadline">
      </div>
      <div class="form-group">
        <label class="form-label">Email body</label>
        <textarea class="form-input" id="ex-body" rows="5"
          placeholder="Paste the email text here…"></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">Correct summary (one sentence)</label>
        <input class="form-input" id="ex-summary"
          placeholder="Advisor needs IRB revision submitted by May 20.">
      </div>
      <div class="form-group">
        <label class="form-label">Correct tasks</label>
        <div class="todo-list" id="ex-todos">
          <div class="todo-add">
            <button class="btn btn-ghost btn-sm" onclick="addExTodo()">+ Add task</button>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:10px;margin-top:4px">
        <button class="btn btn-primary" onclick="saveExample()">Save Example</button>
        <button class="btn btn-ghost" onclick="clearExampleForm()">Clear</button>
      </div>
    </div>
  </div>

  <!-- ── Model tab ── -->
  <div class="tab-panel" id="tab-model">
    <p style="color:var(--muted);margin-bottom:20px;font-size:13px">
      The <code style="background:var(--surface2);padding:1px 6px;border-radius:4px">phd-email-parser</code>
      model is a customized version of <code style="background:var(--surface2);padding:1px 6px;border-radius:4px">llama3.2:3b</code>
      with your context and examples baked in. Rebuild it after adding new examples.
    </p>
    <div class="model-bar">
      <div class="model-info" id="model-info">
        Rebuilding takes about 30–60 seconds.
      </div>
      <button class="btn btn-brand" id="model-btn" onclick="rebuildModel()">
        ↻ Rebuild Model
      </button>
    </div>
    <div class="model-log" id="model-log"></div>
  </div>
</main>

<!-- Approve bar -->
<div class="approve-bar" id="approve-bar" style="display:none">
  <div class="approve-bar-info">
    <strong id="checked-count">0</strong> task(s) selected
  </div>
  <button class="btn btn-ghost" onclick="skipAll()">Skip all</button>
  <button class="btn btn-primary" id="approve-btn" onclick="approveChecked()">
    Add to Todoist
  </button>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ────────────────────────────────────────────────────────────────────

let queueData     = {batches: []};
let pipelineTimer = null;
let modelTimer    = null;
let exTodoCount   = 0;

// ── Init ─────────────────────────────────────────────────────────────────────

refreshQueue();
setInterval(refreshQueue, 5000);

// ── Tabs ─────────────────────────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    const panels = ['queue','examples','model'];
    t.classList.toggle('active', panels[i] === name);
  });
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
}

// ── Queue rendering ──────────────────────────────────────────────────────────

async function refreshQueue() {
  const res = await fetch('/api/queue').then(r => r.json());
  const changed = JSON.stringify(res) !== JSON.stringify(queueData);
  queueData = res;
  if (changed) renderQueue();
  updateHeader();
}

function totalTodos() {
  return queueData.batches.reduce((s,b) =>
    s + b.emails.reduce((s2,e) => s2 + e.todos.length, 0), 0);
}

function updateHeader() {
  const n = totalTodos();
  document.getElementById('header-sub').textContent =
    n ? `${n} task${n===1?'':'s'} pending approval` : 'No pending tasks';
}

function renderQueue() {
  const container = document.getElementById('queue-container');
  const batches   = queueData.batches || [];
  const total     = totalTodos();

  if (!total) {
    container.innerHTML = `
      <div class="queue-empty">
        <strong>All caught up</strong>
        Run the pipeline to check for new emails.
      </div>`;
    updateApproveBar();
    return;
  }

  container.innerHTML = batches.map((batch, bi) => {
    const runAt = batch.run_at
      ? new Date(batch.run_at).toLocaleString('en-US', {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})
      : '';
    const emails = (batch.emails || []).filter(e => e.todos && e.todos.length);
    if (!emails.length) return '';
    return `
      <div class="batch-header">Processed ${runAt}</div>
      ${emails.map((email, ei) => renderEmailCard(email, bi, ei)).join('')}`;
  }).join('');

  updateApproveBar();
}

function renderEmailCard(email, bi, ei) {
  const from    = email.from || '';
  const subject = email.subject || '(no subject)';
  const summary = email.summary || '';
  const todos   = email.todos || [];

  return `
    <div class="email-card" id="card-${bi}-${ei}">
      <div class="email-card-header">
        <span class="email-from" title="${esc(from)}">${esc(displayFrom(from))}</span>
        <span class="email-subject" title="${esc(subject)}">${esc(subject)}</span>
      </div>
      ${summary ? `<div class="email-summary">${esc(summary)}</div>` : ''}
      ${todos.map((t, ti) => renderTaskRow(t, bi, ei, ti)).join('')}
    </div>`;
}

function renderTaskRow(todo, bi, ei, ti) {
  const task  = todo.task || '';
  const prio  = todo.priority || 3;
  const hint  = todo.project_hint || '';
  const due   = todo.due_suggestion || '';
  const pid   = `chk-${bi}-${ei}-${ti}`;
  const prioClass = ['','badge-p1','badge-p2','badge-p3','badge-p4'][prio] || 'badge-p4';
  const prioLabel = ['','URGENT','High','Normal','Low'][prio] || '';

  return `
    <div class="task-row" id="row-${bi}-${ei}-${ti}">
      <input type="checkbox" class="task-check" id="${pid}" checked
        onchange="updateApproveBar()">
      <div class="task-body">
        <textarea class="task-text" rows="1"
          id="text-${bi}-${ei}-${ti}"
          oninput="autoResize(this)">${esc(task)}</textarea>
        <div class="task-meta">
          <span class="badge ${prioClass}">${prioLabel}</span>
          ${hint ? `<span class="badge badge-hint">${esc(hint)}</span>` : ''}
          ${due && due !== 'no rush' ? `<span class="badge badge-due">${esc(due)}</span>` : ''}
        </div>
      </div>
      <button class="task-dismiss" title="Skip this task"
        onclick="skipTask(${bi},${ei},${ti})">×</button>
    </div>`;
}

function displayFrom(raw) {
  const m = raw.match(/^"?([^"<]+)"?\s*</);
  return m ? m[1].trim() : raw.replace(/<[^>]+>/, '').trim();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

// ── Approve bar ──────────────────────────────────────────────────────────────

function updateApproveBar() {
  const checked = document.querySelectorAll('.task-check:checked').length;
  const bar     = document.getElementById('approve-bar');
  bar.style.display = totalTodos() ? 'flex' : 'none';
  document.getElementById('checked-count').textContent = checked;
  document.getElementById('approve-btn').disabled = checked === 0;
}

function gatherChecked() {
  const tasks = [];
  queueData.batches.forEach((batch, bi) => {
    (batch.emails || []).forEach((email, ei) => {
      (email.todos || []).forEach((todo, ti) => {
        const chk  = document.getElementById(`chk-${bi}-${ei}-${ti}`);
        const text = document.getElementById(`text-${bi}-${ei}-${ti}`);
        if (chk && chk.checked) {
          tasks.push({...todo, task: text ? text.value.trim() : todo.task});
        }
      });
    });
  });
  return tasks;
}

async function approveChecked() {
  const tasks = gatherChecked();
  if (!tasks.length) return;
  document.getElementById('approve-btn').disabled = true;
  document.getElementById('approve-btn').textContent = 'Adding…';
  const res = await post('/api/approve', {tasks});
  if (res.ok) {
    showToast(`✓ Added ${res.created.length} task${res.created.length===1?'':'s'} to Todoist`);
  } else {
    showToast(`✗ Error: ${res.error || 'Unknown error'}`);
  }
  document.getElementById('approve-btn').textContent = 'Add to Todoist';
  await refreshQueue();
}

function skipTask(bi, ei, ti) {
  const formId = `dismiss-form-${bi}-${ei}-${ti}`;
  if (document.getElementById(formId)) { cancelSkip(bi, ei, ti); return; }
  const row = document.getElementById(`row-${bi}-${ei}-${ti}`);
  if (!row) return;
  const form = document.createElement('div');
  form.className = 'dismiss-form';
  form.id = formId;
  form.innerHTML = `
    <input class="dismiss-reason" id="reason-${bi}-${ei}-${ti}"
      placeholder="Why skip? Helps train the model (optional)" autocomplete="off">
    <button class="btn btn-sm btn-danger" onclick="confirmSkip(${bi},${ei},${ti})">Skip</button>
    <button class="btn btn-sm btn-ghost" onclick="cancelSkip(${bi},${ei},${ti})">Cancel</button>`;
  row.after(form);
  const inp = document.getElementById(`reason-${bi}-${ei}-${ti}`);
  inp.focus();
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') confirmSkip(bi, ei, ti);
    if (e.key === 'Escape') cancelSkip(bi, ei, ti);
  });
}

async function confirmSkip(bi, ei, ti) {
  const todo = queueData.batches[bi]?.emails[ei]?.todos[ti];
  if (!todo) return;
  const reasonEl = document.getElementById(`reason-${bi}-${ei}-${ti}`);
  const reason = reasonEl ? reasonEl.value.trim() : '';
  const row = document.getElementById(`row-${bi}-${ei}-${ti}`);
  if (row) row.style.opacity = '.3';
  document.getElementById(`dismiss-form-${bi}-${ei}-${ti}`)?.remove();
  await post('/api/dismiss', {task_contents: [todo.task || ''], task: todo.task || '', reason});
  await refreshQueue();
}

function cancelSkip(bi, ei, ti) {
  document.getElementById(`dismiss-form-${bi}-${ei}-${ti}`)?.remove();
}

async function stopServer() {
  const btn = document.getElementById('stop-btn');
  btn.textContent = 'Stopping…';
  btn.disabled = true;
  try { await post('/api/quit', {}); } catch(_) {}
  btn.textContent = 'Stopped';
  document.getElementById('header-sub').textContent = 'Server stopped — close this tab.';
}

async function skipAll() {
  const contents = [];
  queueData.batches.forEach(b =>
    (b.emails||[]).forEach(e =>
      (e.todos||[]).forEach(t => contents.push(t.task||''))
    )
  );
  await post('/api/dismiss', {task_contents: contents});
  await refreshQueue();
}

// ── Pipeline ─────────────────────────────────────────────────────────────────

async function runPipeline() {
  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Running…';
  document.getElementById('pipeline-status').textContent = 'Checking LLM Queue…';
  document.getElementById('pipeline-log').classList.add('visible');

  await post('/api/run-pipeline', {});
  if (pipelineTimer) clearInterval(pipelineTimer);
  pipelineTimer = setInterval(pollPipeline, 1000);
}

async function pollPipeline() {
  const res = await fetch('/api/status').then(r => r.json());
  const logEl = document.getElementById('pipeline-log');
  logEl.innerHTML = res.log.map(l => escLine(l)).join('\\n');
  logEl.scrollTop = logEl.scrollHeight;

  if (!res.running) {
    clearInterval(pipelineTimer);
    pipelineTimer = null;
    document.getElementById('run-btn').disabled = false;
    document.getElementById('run-btn').innerHTML = '▶ Run Pipeline Now';
    document.getElementById('pipeline-status').textContent = 'Done.';
    await refreshQueue();
  }
}

function toggleLog() {
  document.getElementById('pipeline-log').classList.toggle('visible');
}

// ── Examples ─────────────────────────────────────────────────────────────────

function addExTodo() {
  const list = document.getElementById('ex-todos');
  const btn  = list.querySelector('.todo-add');
  const id   = exTodoCount++;
  const div  = document.createElement('div');
  div.className = 'todo-item';
  div.id = `ex-todo-${id}`;
  div.innerHTML = `
    <input class="todo-input" placeholder="Task description" id="ex-td-task-${id}">
    <select class="todo-select" id="ex-td-prio-${id}">
      <option value="3">Normal</option>
      <option value="1">Urgent</option>
      <option value="2">High</option>
      <option value="4">Low</option>
    </select>
    <select class="todo-select" id="ex-td-hint-${id}">
      <option value="research">Research</option>
      <option value="readings">Readings</option>
      <option value="admin">Admin</option>
      <option value="church">Church</option>
      <option value="personal">Personal</option>
    </select>
    <input class="todo-input" placeholder="Due (optional)" id="ex-td-due-${id}">
    <button class="todo-remove" onclick="removeExTodo(${id})">×</button>`;
  list.insertBefore(div, btn);
}

function removeExTodo(id) {
  document.getElementById(`ex-todo-${id}`)?.remove();
}

async function saveExample() {
  const todos = [...document.querySelectorAll('[id^="ex-todo-"]')].map(el => {
    const id = el.id.replace('ex-todo-','');
    return {
      task:           document.getElementById(`ex-td-task-${id}`)?.value.trim() || '',
      priority:       parseInt(document.getElementById(`ex-td-prio-${id}`)?.value || 3),
      project_hint:   document.getElementById(`ex-td-hint-${id}`)?.value || 'research',
      due_suggestion: document.getElementById(`ex-td-due-${id}`)?.value.trim() || 'no rush',
    };
  }).filter(t => t.task);

  const data = {
    description: document.getElementById('ex-desc').value.trim(),
    from:        document.getElementById('ex-from').value.trim(),
    subject:     document.getElementById('ex-subject').value.trim(),
    body:        document.getElementById('ex-body').value.trim(),
    summary:     document.getElementById('ex-summary').value.trim(),
    todos,
  };

  if (!data.subject || !data.summary) {
    showToast('Subject and summary are required.'); return;
  }

  const res = await post('/api/add-example', data);
  if (res.ok) {
    showToast('✓ Example saved to examples.md');
    clearExampleForm();
    switchTab('model');
  } else {
    showToast(`✗ ${res.error}`);
  }
}

function clearExampleForm() {
  ['ex-desc','ex-from','ex-subject','ex-body','ex-summary'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const list = document.getElementById('ex-todos');
  list.querySelectorAll('[id^="ex-todo-"]').forEach(el => el.remove());
}

// ── Model ─────────────────────────────────────────────────────────────────────

async function rebuildModel() {
  const btn = document.getElementById('model-btn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Rebuilding…';
  document.getElementById('model-log').classList.add('visible');
  await post('/api/rebuild-model', {});
  if (modelTimer) clearInterval(modelTimer);
  modelTimer = setInterval(pollModel, 1500);
}

async function pollModel() {
  const res = await fetch('/api/model-status').then(r => r.json());
  const logEl = document.getElementById('model-log');
  logEl.innerHTML = res.log.map(l => escLine(l)).join('\\n');
  logEl.scrollTop = logEl.scrollHeight;
  if (!res.running) {
    clearInterval(modelTimer);
    modelTimer = null;
    document.getElementById('model-btn').disabled = false;
    document.getElementById('model-btn').innerHTML = '↻ Rebuild Model';
    document.getElementById('model-info').textContent = 'Model rebuilt successfully.';
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
function escLine(s) {
  return esc(s).replace(/\\n/g,'<br>');
}

async function post(path, body) {
  const r = await fetch(path, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  return r.json();
}

function showToast(msg, ms=3000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}

// Auto-resize all textareas after render
new MutationObserver(() => {
  document.querySelectorAll('.task-text').forEach(autoResize);
}).observe(document.getElementById('queue-container'), {childList:true, subtree:true});
</script>
</body>
</html>"""


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(HTML)
        elif path == "/api/queue":
            self._send_json(load_queue())
        elif path == "/api/status":
            self._send_json({"running": _pipeline_running, "log": _pipeline_log[-30:]})
        elif path == "/api/model-status":
            self._send_json({"running": _model_running, "log": _model_log[-30:]})
        else:
            self.send_error(404)

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/approve":
            self._send_json(approve_tasks(body.get("tasks", [])))
        elif path == "/api/dismiss":
            reason = body.get("reason", "").strip()
            task   = body.get("task", "").strip()
            result = dismiss_tasks(body.get("task_contents", []))
            if reason and task:
                save_correction(task, reason)
            self._send_json(result)
        elif path == "/api/run-pipeline":
            self._send_json(run_pipeline_bg())
        elif path == "/api/rebuild-model":
            self._send_json(rebuild_model_bg())
        elif path == "/api/add-example":
            self._send_json(add_example(body))
        elif path == "/api/quit":
            self._send_json({"ok": True})
            threading.Timer(0.3, self.server.shutdown).start()
        else:
            self.send_error(404)

    def _send_html(self, html):
        enc = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(enc))
        self.end_headers()
        self.wfile.write(enc)

    def _send_json(self, data):
        enc = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(enc))
        self.end_headers()
        self.wfile.write(enc)

    def log_message(self, *args):
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url    = f"http://localhost:{PORT}"
    # Open browser half a second after server starts
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    print(f"PhD OS Tasks UI → {url}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
