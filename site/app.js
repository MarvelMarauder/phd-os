// PhD OS — shared utilities

const STAGES = [
  'ideation', 'lit-search', 'fleshing-out', 'method',
  'data-collection', 'data-analysis', 'writeup', 'under-review', 'published'
];

const TODOIST_BASE = 'https://api.todoist.com/api/v1';

// ── Token management ──────────────────────────────────
function getToken()      { return localStorage.getItem('phd_todoist_token') || ''; }
function setToken(t)     { localStorage.setItem('phd_todoist_token', t.trim()); }
function clearToken()    { localStorage.removeItem('phd_todoist_token'); }
function hasToken()      { return Boolean(getToken()); }

// ── Todoist API ───────────────────────────────────────
async function apiGet(path) {
  const r = await fetch(TODOIST_BASE + path, {
    headers: { Authorization: `Bearer ${getToken()}` }
  });
  if (r.status === 401) { clearToken(); throw new Error('bad_token'); }
  if (!r.ok) throw new Error(`api_${r.status}`);
  return r.json();
}

async function apiPost(path, body = null) {
  const r = await fetch(TODOIST_BASE + path, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${getToken()}`,
      'Content-Type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  });
  if (r.status === 401) { clearToken(); throw new Error('bad_token'); }
  if (!r.ok) throw new Error(`api_${r.status}`);
  return r.status === 204 ? null : r.json();
}

async function fetchTasks() {
  const data = await apiGet('/tasks?limit=200');
  return Array.isArray(data) ? data : (data.results || []);
}

async function closeTask(id) {
  return apiPost(`/tasks/${id}/close`);
}

async function fetchProjects() {
  const data = await apiGet('/projects?limit=200');
  return Array.isArray(data) ? data : (data.results || []);
}

async function fetchReminders() {
  try {
    const data = await apiGet('/reminders?limit=200');
    return Array.isArray(data) ? data : (data.results || []);
  } catch(e) {
    return []; // reminders not available on this plan
  }
}

// Build task_id → reminders[] map from a reminders array
function buildReminderMap(reminders) {
  const map = {};
  for (const r of reminders) {
    (map[r.item_id] = map[r.item_id] || []).push(r);
  }
  return map;
}

function formatReminder(r) {
  if (r.type === 'relative') {
    const m = r.minute_offset || 0;
    if (m === 0) return 'At due time';
    if (m < 60) return `${m}m before`;
    const h = Math.floor(m / 60), rem = m % 60;
    return rem ? `${h}h ${rem}m before` : `${h}h before`;
  }
  // absolute
  if (!r.due?.date) return 'Reminder set';
  const dateStr = r.due.date.split('T')[0];
  const timePart = r.due.date.includes('T') ? r.due.date.split('T')[1].slice(0,5) : null;
  const d = new Date(dateStr + 'T12:00:00');
  const today = new Date(); today.setHours(12,0,0,0);
  const label = d.toDateString() === today.toDateString()
    ? 'Today'
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return timePart ? `${label} ${timePart}` : label;
}

// ── Date filtering helpers ────────────────────────────
function localDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function todayStr() {
  return localDateStr(new Date());
}

function daysFromNowStr(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return localDateStr(d);
}

// Normalize "2026-05-20T16:00:00" or "2026-05-20" → "2026-05-20"
function dueDate(t) {
  return t.due ? t.due.date.split('T')[0] : null;
}

// Tasks due today or already overdue
function isDueNow(t) {
  const d = dueDate(t);
  return d !== null && d <= todayStr();
}

// Tasks due strictly after today and within n days
function isDueWithin(t, days) {
  const d = dueDate(t);
  if (!d) return false;
  return d > todayStr() && d <= daysFromNowStr(days);
}

// ── Helpers ───────────────────────────────────────────
function formatDue(due) {
  if (!due) return '';
  const dateStr = due.date.split('T')[0]; // normalize datetime → date
  const d = new Date(dateStr + 'T12:00:00'); // noon local to avoid tz shift
  const today = new Date(); today.setHours(12,0,0,0);
  const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function stageIndex(stage) {
  const i = STAGES.indexOf(stage);
  return i === -1 ? 0 : i;
}

function stageClass(stage) {
  return 's-' + (stage || 'ideation').replace(/\s+/g, '-');
}

// ── API status dot ────────────────────────────────────
function setApiStatus(state) { // 'connected' | 'error' | ''
  const dot = document.querySelector('.api-dot');
  const lbl = document.querySelector('.api-label');
  if (!dot) return;
  dot.className = 'api-dot' + (state ? ' ' + state : '');
  if (lbl) {
    lbl.textContent = state === 'connected' ? 'Todoist'
      : state === 'error' ? 'Todoist error'
      : 'Not connected';
  }
}

// ── Token modal ───────────────────────────────────────
function showTokenModal(onConnect) {
  const backdrop = document.getElementById('token-modal');
  if (!backdrop) return;
  backdrop.classList.remove('hidden');

  backdrop.querySelector('#token-connect').onclick = () => {
    const val = backdrop.querySelector('#token-input').value.trim();
    if (!val) return;
    setToken(val);
    backdrop.classList.add('hidden');
    if (onConnect) onConnect();
  };

  backdrop.querySelector('#token-skip').onclick = () => {
    backdrop.classList.add('hidden');
  };

  backdrop.querySelector('#token-input').focus();
}

// ── Settings gear ─────────────────────────────────────
function initSettings() {
  const btn = document.querySelector('.settings-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const ok = confirm('Clear Todoist token?\n\nYou\'ll be prompted to enter it again on the next page load.');
    if (ok) { clearToken(); location.reload(); }
  });
}

// ── Token modal HTML (injected by pages that need Todoist) ──
function tokenModalHTML() {
  return `
<div id="token-modal" class="modal-backdrop hidden">
  <div class="modal">
    <h3>Connect Todoist</h3>
    <p>Enter your Todoist API token to see live tasks. Find it at todoist.com → Settings → Integrations → Developer. It's stored only in your browser.</p>
    <input type="text" id="token-input" placeholder="your-api-token" autocomplete="off" spellcheck="false">
    <div class="modal-actions">
      <button class="btn btn-ghost" id="token-skip">Skip for now</button>
      <button class="btn btn-primary" id="token-connect">Connect</button>
    </div>
  </div>
</div>`;
}

// ── Shared utilities ──────────────────────────────────
function escHtml(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Render a task list with subtask support.
// tasks       = filtered list to display (e.g. due today/this week)
// allTasks    = full task list, used to resolve children
// showDue     = whether to append a due-date badge
// reminderMap = optional task_id → reminders[] from buildReminderMap()
function renderTaskList(containerId, tasks, allTasks, showDue, reminderMap) {
  const el = document.getElementById(containerId);
  if (!tasks.length) {
    el.innerHTML = '<li class="empty">Nothing here.</li>';
    return;
  }

  // Build childMap from the complete task list
  const childMap = {};
  for (const t of (allTasks || [])) {
    if (t.parent_id) {
      (childMap[t.parent_id] = childMap[t.parent_id] || []).push(t);
    }
  }

  // Only show tasks whose parent is not also in the visible list
  const taskIds = new Set(tasks.map(t => t.id));
  const topLevel = tasks.filter(t => !t.parent_id || !taskIds.has(t.parent_id));

  el.innerHTML = topLevel.map(t => {
    const due = showDue && t.due ? `<span class="task-due">${formatDue(t.due)}</span>` : '';
    const children = childMap[t.id] || [];
    const subtasksHtml = children.length ? `
      <ul class="subtask-list">
        ${children.map(c => `
          <li class="subtask-item">
            <button class="subtask-check" data-id="${c.id}" aria-label="Complete subtask"></button>
            <span class="subtask-name">${escHtml(c.content)}</span>
          </li>`).join('')}
      </ul>` : '';

    const reminders = reminderMap ? (reminderMap[t.id] || []) : [];
    const reminderHtml = reminders.length
      ? reminders.map(r =>
          `<span class="task-reminder" title="Reminder">🔔 ${escHtml(formatReminder(r))}</span>`
        ).join('')
      : '';

    return `
      <li class="task-item" data-id="${t.id}">
        <button class="task-check" data-id="${t.id}" aria-label="Complete task"></button>
        <div class="task-body">
          <div class="task-name">${escHtml(t.content)}</div>
          ${t.description ? `<div class="task-meta">${escHtml(t.description.slice(0,80))}</div>` : ''}
          ${reminderHtml}
          ${subtasksHtml}
        </div>
        ${due}
      </li>`;
  }).join('');

  el.querySelectorAll('.task-check').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.classList.add('checked');
      const nameEl = btn.closest('.task-item').querySelector('.task-name');
      if (nameEl) nameEl.classList.add('done');
      try { await closeTask(btn.dataset.id); } catch(e) {
        btn.classList.remove('checked');
        if (nameEl) nameEl.classList.remove('done');
      }
    });
  });

  el.querySelectorAll('.subtask-check').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.classList.add('checked');
      const nameEl = btn.closest('.subtask-item').querySelector('.subtask-name');
      if (nameEl) nameEl.classList.add('done');
      try { await closeTask(btn.dataset.id); } catch(e) {
        btn.classList.remove('checked');
        if (nameEl) nameEl.classList.remove('done');
      }
    });
  });
}

// ── Standard nav HTML (call with current page key) ────
function navHTML(active) {
  const pages = [
    ['index.html', 'home', 'Home'],
    ['papers.html', 'papers', 'Papers'],
    ['church.html', 'church', 'Church'],
    ['projects.html', 'projects', 'Projects'],
    ['recharge.html', 'recharge', 'Recharge'],
  ];
  const links = pages.map(([href, key, label]) =>
    `<a href="${href}"${active === key ? ' class="active"' : ''}>${label}</a>`
  ).join('');
  return `
    <a href="index.html" class="brand">PhD OS</a>
    ${links}
    <span class="nav-spacer"></span>
    <div class="api-status">
      <div class="api-dot"></div>
      <span class="api-label">Todoist</span>
    </div>
    <button class="settings-btn" title="Clear Todoist token">⚙</button>`;
}
