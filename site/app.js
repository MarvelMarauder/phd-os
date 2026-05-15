// PhD OS — shared utilities

const STAGES = [
  'ideation', 'lit-search', 'fleshing-out', 'method',
  'data-collection', 'data-analysis', 'writeup', 'under-review', 'published'
];

const TODOIST_BASE = 'https://api.todoist.com/rest/v2';

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
  return apiGet('/tasks');
}

async function closeTask(id) {
  return apiPost(`/tasks/${id}/close`);
}

async function fetchProjects() {
  return apiGet('/projects');
}

// ── Date filtering helpers ────────────────────────────
function todayStr() {
  return new Date().toISOString().split('T')[0];
}

function daysFromNowStr(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().split('T')[0];
}

// Tasks due today or already overdue
function isDueNow(t) {
  return t.due && t.due.date <= todayStr();
}

// Tasks due strictly after today and within n days
function isDueWithin(t, days) {
  if (!t.due) return false;
  const d = t.due.date;
  return d > todayStr() && d <= daysFromNowStr(days);
}

// ── Helpers ───────────────────────────────────────────
function formatDue(due) {
  if (!due) return '';
  const dateStr = due.date; // e.g. "2026-05-15"
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
