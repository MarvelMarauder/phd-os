// PhD OS — shared utilities

const STAGES = [
  'ideation', 'lit-search', 'fleshing-out', 'method',
  'data-collection', 'data-analysis', 'writeup', 'under-review', 'published'
];

const TODOIST_BASE = 'https://api.todoist.com/api/v1';

// ── Token management ──────────────────────────────────
function getToken()   { return localStorage.getItem('phd_todoist_token') || ''; }
function setToken(t)  { localStorage.setItem('phd_todoist_token', t.trim()); }
function clearToken() { localStorage.removeItem('phd_todoist_token'); }
function hasToken()   { return Boolean(getToken()); }

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

async function closeTask(id) { return apiPost(`/tasks/${id}/close`); }

async function fetchProjects() {
  const data = await apiGet('/projects?limit=200');
  return Array.isArray(data) ? data : (data.results || []);
}

async function fetchReminders() {
  try {
    const data = await apiGet('/reminders?limit=200');
    return Array.isArray(data) ? data : (data.results || []);
  } catch(e) { return []; }
}

function buildReminderMap(reminders) {
  const map = {};
  for (const r of reminders) (map[r.item_id] = map[r.item_id] || []).push(r);
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
  if (!r.due?.date) return 'Reminder set';
  const dateStr  = r.due.date.split('T')[0];
  const timePart = r.due.date.includes('T') ? r.due.date.split('T')[1].slice(0,5) : null;
  const d     = new Date(dateStr + 'T12:00:00');
  const today = new Date(); today.setHours(12,0,0,0);
  const label = d.toDateString() === today.toDateString()
    ? 'Today'
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return timePart ? `${label} ${timePart}` : label;
}

function reminderIsEarly(r, task) {
  if (r.type === 'relative') return (r.minute_offset || 0) > 120;
  if (!r.due?.date || !task.due?.date) return false;
  const reminderMs  = new Date(r.due.date).getTime();
  const taskDateStr = task.due.date.includes('T') ? task.due.date : task.due.date + 'T12:00:00';
  return (new Date(taskDateStr).getTime() - reminderMs) > 120 * 60 * 1000;
}

// ── Date helpers ──────────────────────────────────────
function localDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
function todayStr()        { return localDateStr(new Date()); }
function daysFromNowStr(n) { const d = new Date(); d.setDate(d.getDate() + n); return localDateStr(d); }
function dueDate(t)        { return t.due ? t.due.date.split('T')[0] : null; }
function isDueNow(t)       { const d = dueDate(t); return d !== null && d <= todayStr(); }
function isDueWithin(t, n) { const d = dueDate(t); return !!d && d > todayStr() && d <= daysFromNowStr(n); }

function formatDue(due) {
  if (!due) return '';
  const dateStr  = due.date.split('T')[0];
  const d        = new Date(dateStr + 'T12:00:00');
  const today    = new Date(); today.setHours(12,0,0,0);
  const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate() + 1);
  if (d.toDateString() === today.toDateString())    return 'Today';
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function stageIndex(stage) { const i = STAGES.indexOf(stage); return i === -1 ? 0 : i; }
function stageClass(stage) { return 's-' + (stage || 'ideation').replace(/\s+/g, '-'); }

// ── API status dot ────────────────────────────────────
function setApiStatus(state) {
  const dot = document.querySelector('.api-dot');
  const lbl = document.querySelector('.api-label');
  if (!dot) return;
  dot.className = 'api-dot' + (state ? ' ' + state : '');
  if (lbl) lbl.textContent = state === 'connected' ? 'Todoist' : state === 'error' ? 'Todoist error' : 'Not connected';
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
  backdrop.querySelector('#token-skip').onclick = () => backdrop.classList.add('hidden');
  backdrop.querySelector('#token-input').focus();
}

// ── Theme ─────────────────────────────────────────────
function getTheme() { return localStorage.getItem('phd_theme') || 'system'; }
function setTheme(t) {
  if (t === 'system') localStorage.removeItem('phd_theme');
  else localStorage.setItem('phd_theme', t);
  document.documentElement.classList.remove('theme-light', 'theme-dark');
  if (t === 'light') document.documentElement.classList.add('theme-light');
  if (t === 'dark')  document.documentElement.classList.add('theme-dark');
}
function initTheme() {
  const btn = document.getElementById('nav-theme-btn');
  if (!btn) return;
  const refresh = () => {
    const forced = getTheme() === 'light';
    btn.textContent = forced ? '🌙' : '☀';
    btn.title       = forced ? 'Return to system theme' : 'Force light mode';
  };
  refresh();
  btn.addEventListener('click', () => { setTheme(getTheme() === 'light' ? 'system' : 'light'); refresh(); });
}

// ── Settings gear ─────────────────────────────────────
function initSettings() {
  initTheme();
  const btn = document.querySelector('.settings-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    if (confirm("Clear Todoist token?\n\nYou'll be prompted to enter it again on the next page load.")) {
      clearToken(); location.reload();
    }
  });
}

// ── Client-side book cover fetching ───────────────────
// Strategy: Open Library search → cover_i (their internal cover ID) → direct image URL.
// This is the programmatic "look up the book, get an ID, fetch the right cover" flow.
// Falls back to Google Books if OL has no cover for that title.
const _coverCache = {};

async function fetchBookCover(isbn, title, author) {
  const cacheKey = isbn ? `isbn:${isbn}` : `book:${title}`;
  if (cacheKey in _coverCache) return _coverCache[cacheKey];

  // 1. If we already have an ISBN, Open Library can serve the cover directly — no search needed.
  if (isbn) {
    return (_coverCache[cacheKey] = `https://covers.openlibrary.org/b/isbn/${isbn}-L.jpg`);
  }

  // 2. Open Library search: finds the book and returns its cover_i (native cover ID).
  //    cover_i → https://covers.openlibrary.org/b/id/{cover_i}-L.jpg is the most reliable URL.
  //    We try title+author first, then title only (handles pen names, variant spellings).
  const olSearch = async (params) => {
    try {
      const r = await fetch(
        `https://openlibrary.org/search.json?${params}&limit=5&fields=cover_i,isbn`
      );
      if (!r.ok) return '';
      const { docs = [] } = await r.json();
      for (const doc of docs) {
        if (doc.cover_i) {
          return `https://covers.openlibrary.org/b/id/${doc.cover_i}-L.jpg`;
        }
        // Fallback within OL: use any ISBN-13 they have to build a cover URL
        const isbn13 = (doc.isbn || []).find(i => String(i).length === 13);
        if (isbn13) return `https://covers.openlibrary.org/b/isbn/${isbn13}-L.jpg`;
      }
    } catch(e) {}
    return '';
  };

  const t = encodeURIComponent(title);
  const a = encodeURIComponent(author || '');

  // Try with author (more precise for common titles like "Gilead")
  if (author) {
    const url = await olSearch(`title=${t}&author=${a}`);
    if (url) return (_coverCache[cacheKey] = url);
  }

  // Try title only (catches pen names and variant author entries)
  const url2 = await olSearch(`title=${t}`);
  if (url2) return (_coverCache[cacheKey] = url2);

  // 3. Google Books as a last resort — plain title+author query, no field operators.
  try {
    const data = await fetch(
      `https://www.googleapis.com/books/v1/volumes?q=${encodeURIComponent(title + ' ' + (author || ''))}&maxResults=3`
    ).then(r => r.json());
    const items = [...(data.items || [])].sort((a, b) =>
      (b.volumeInfo?.publishedDate || '').localeCompare(a.volumeInfo?.publishedDate || '')
    );
    for (const item of items) {
      const links = item.volumeInfo?.imageLinks || {};
      for (const key of ['large', 'medium', 'thumbnail', 'smallThumbnail']) {
        if (links[key]) return (_coverCache[cacheKey] = links[key].replace('http://', 'https://'));
      }
    }
  } catch(e) {}

  return (_coverCache[cacheKey] = '');
}

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

function renderTaskList(containerId, tasks, allTasks, showDue, reminderMap) {
  const el = document.getElementById(containerId);
  if (!tasks.length) { el.innerHTML = '<li class="empty">Nothing here.</li>'; return; }

  const childMap = {};
  for (const t of (allTasks || [])) {
    if (t.parent_id) (childMap[t.parent_id] = childMap[t.parent_id] || []).push(t);
  }

  const taskIds  = new Set(tasks.map(t => t.id));
  const topLevel = tasks.filter(t => !t.parent_id || !taskIds.has(t.parent_id));

  el.innerHTML = topLevel.map(t => {
    const due      = showDue && t.due ? `<span class="task-due">${formatDue(t.due)}</span>` : '';
    const children = childMap[t.id] || [];
    const subtasksHtml = children.length ? `
      <ul class="subtask-list">
        ${children.map(c => `
          <li class="subtask-item">
            <button class="subtask-check" data-id="${c.id}" aria-label="Complete subtask"></button>
            <span class="subtask-name">${escHtml(c.content)}</span>
          </li>`).join('')}
      </ul>` : '';

    const reminders   = (reminderMap ? (reminderMap[t.id] || []) : []).filter(r => reminderIsEarly(r, t));
    const reminderHtml = reminders.length
      ? reminders.map(r => `<span class="task-reminder">🔔 ${escHtml(formatReminder(r))}</span>`).join('') : '';

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

// ── Google Calendar ───────────────────────────────────
const GCAL_CLIENT_ID = '955783464155-8tjdfucclo0hlbv57s2oti415a094dln.apps.googleusercontent.com';

let _gcalTokenClient = null;
let _gcalCb = null;

function getGcalAuth()    { try { return JSON.parse(localStorage.getItem('phd_gcal_auth') || 'null'); } catch { return null; } }
function setGcalAuth(token, expiresIn) {
  localStorage.setItem('phd_gcal_auth', JSON.stringify({
    access_token: token,
    expires_at:   Date.now() + (Number(expiresIn) - 60) * 1000
  }));
  setGcalConnected();
}
function clearGcalAuth()   { localStorage.removeItem('phd_gcal_auth'); }
function gcalTokenValid()  { const a = getGcalAuth(); return !!(a && a.access_token && a.expires_at > Date.now()); }
function gcalAccessToken() { return (getGcalAuth() || {}).access_token || null; }
function setGcalConnected(){ localStorage.setItem('phd_gcal_connected', '1'); }
function hasGcalConnected(){ return !!localStorage.getItem('phd_gcal_connected'); }

function gcalInit() {
  if (!GCAL_CLIENT_ID || !window.google?.accounts?.oauth2) return;
  _gcalTokenClient = google.accounts.oauth2.initTokenClient({
    client_id: GCAL_CLIENT_ID,
    scope: 'https://www.googleapis.com/auth/calendar.readonly',
    callback: resp => {
      if (resp.access_token) setGcalAuth(resp.access_token, resp.expires_in || 3600);
      if (_gcalCb) { _gcalCb(resp); _gcalCb = null; }
    }
  });
}

function requestGcalToken(callback) {
  _gcalCb = callback;
  if (!_gcalTokenClient) gcalInit();
  if (_gcalTokenClient) _gcalTokenClient.requestAccessToken();
  else callback({ error: 'gis_not_ready' });
}

// Attempts silent token refresh — no popup if the user is still signed in to Google.
function requestGcalTokenSilent(callback) {
  function _attempt() {
    _gcalCb = callback;
    if (!_gcalTokenClient) gcalInit();
    if (_gcalTokenClient) _gcalTokenClient.requestAccessToken({ prompt: '' });
    else callback({ error: 'gis_not_ready' });
  }
  if (window.google?.accounts?.oauth2) _attempt();
  else setTimeout(_attempt, 800); // GIS script may still be loading
}

async function gcalGet(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${gcalAccessToken()}` } });
  if (r.status === 401) { clearGcalAuth(); throw new Error('gcal_expired'); }
  if (!r.ok) throw new Error(`gcal_${r.status}`);
  return r.json();
}

async function fetchTodayEvents() {
  const d     = new Date();
  const start = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const end   = new Date(start.getTime() + 86400000);

  const calList = await gcalGet('https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=50');
  const cals    = (calList.items || []).filter(c => c.selected !== false);

  const settled = await Promise.allSettled(
    cals.map(cal =>
      gcalGet(
        `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(cal.id)}/events` +
        `?timeMin=${encodeURIComponent(start.toISOString())}&timeMax=${encodeURIComponent(end.toISOString())}` +
        `&singleEvents=true&orderBy=startTime&maxResults=50`
      ).then(data => (data.items || []).map(e => ({ ...e, _calColor: cal.backgroundColor || '#4285f4' })))
    )
  );

  const events = [];
  for (const r of settled) if (r.status === 'fulfilled') events.push(...r.value);
  events.sort((a, b) => {
    const at = a.start.dateTime || a.start.date || '';
    const bt = b.start.dateTime || b.start.date || '';
    return at < bt ? -1 : at > bt ? 1 : 0;
  });
  return events;
}

// Shared calendar rendering — used by both index.html and dashboard.html
function renderCalendarEvents(el, events) {
  if (!events.length) { el.innerHTML = '<li class="empty">No meetings today.</li>'; return; }
  el.innerHTML = events.map(e => {
    const allDay  = !e.start.dateTime;
    const timeStr = allDay
      ? 'All day'
      : new Date(e.start.dateTime).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    const badge = e.hangoutLink
      ? '<span class="event-badge">Meet</span>'
      : (e.location || '').toLowerCase().includes('zoom.us') ? '<span class="event-badge">Zoom</span>' : '';
    return `
      <li class="event-item">
        <span class="event-time${allDay ? ' all-day' : ''}">${escHtml(timeStr)}</span>
        <span class="event-dot" style="background:${escHtml(e._calColor)}"></span>
        <span class="event-name">${escHtml(e.summary || '(no title)')}</span>
        ${badge}
      </li>`;
  }).join('');
}

function showGcalConnectBtn(el, label, onConnect) {
  const id = 'gcal-btn-' + Math.random().toString(36).slice(2,8);
  el.innerHTML = `<li style="padding:4px 0"><button class="gcal-connect-btn" id="${id}">${escHtml(label || 'Connect Google Calendar')}</button></li>`;
  document.getElementById(id).addEventListener('click', () => {
    requestGcalToken(resp => {
      if (resp.access_token && onConnect) onConnect();
      else if (!resp.access_token) el.innerHTML = '<li class="error">Sign-in cancelled.</li>';
    });
  });
}

// One-call calendar loader — handles valid token, silent refresh, and connect button.
async function loadCalendarInto(el) {
  if (!GCAL_CLIENT_ID) {
    el.innerHTML = '<li class="empty">Configure GCAL_CLIENT_ID in app.js.</li>';
    return;
  }
  if (gcalTokenValid()) {
    el.innerHTML = '<li class="loading">Loading meetings…</li>';
    try {
      renderCalendarEvents(el, await fetchTodayEvents());
    } catch(e) {
      if (e.message === 'gcal_expired') { clearGcalAuth(); loadCalendarInto(el); }
      else el.innerHTML = `<li class="error">Calendar error: ${escHtml(e.message)}</li>`;
    }
    return;
  }
  if (hasGcalConnected()) {
    el.innerHTML = '<li class="loading">Reconnecting calendar…</li>';
    requestGcalTokenSilent(resp => {
      if (resp.access_token) loadCalendarInto(el);
      else showGcalConnectBtn(el, 'Reconnect Google Calendar', () => loadCalendarInto(el));
    });
    return;
  }
  showGcalConnectBtn(el, 'Connect Google Calendar', () => loadCalendarInto(el));
}

// ── LDS Quotes (rotating daily) ──────────────────────
const LDS_QUOTES = [
  {text:"Lead with love; authority without compassion corrupts the soul.",             attr:"Gordon B. Hinckley"},
  {text:"A leader's greatest gift is the ability to inspire faith and hope.",          attr:"Russell M. Nelson"},
  {text:"Listen more than you speak; revelation often comes through quiet counsel.",   attr:"Henry B. Eyring"},
  {text:"Serve those you lead and you will earn trust that endures.",                  attr:"Jeffrey R. Holland"},
  {text:"Stand for truth with compassion and firmness balanced by humility.",          attr:"Dieter F. Uchtdorf"},
  {text:"Encourage agency; empowered disciples make stronger, lasting change.",        attr:"Dallin H. Oaks"},
  {text:"Leaders teach by example; daily consistency shapes organizational culture.",  attr:"Neal A. Maxwell"},
  {text:"Pray for those you lead and seek wisdom beyond your own understanding.",      attr:"Thomas S. Monson"},
  {text:"Great leaders create other leaders through teaching, trust, and patience.",   attr:"Elaine M. Thorne"},
  {text:"Humility and courage together define Christlike leadership.",                 attr:"Spencer W. Kimball"},
  {text:"Trials refine faith and reveal hidden strength when we lean on the Lord.",   attr:"Jeffrey R. Holland"},
  {text:"Adversity can be our greatest teacher if we seek lessons in the pain.",      attr:"Neal A. Maxwell"},
  {text:"Sorrow need not harden the heart; allow it to deepen your compassion.",      attr:"Russell M. Nelson"},
  {text:"Endure with patience, and prayer will lighten your load and guide your steps.",attr:"Henry B. Eyring"},
  {text:"Trials produce humility, and humility opens us to God's strength.",           attr:"Gordon B. Hinckley"},
  {text:"Hold to hope and keep small, faithful acts daily; they sustain through storms.",attr:"Dieter F. Uchtdorf"},
  {text:"Remember past mercies to trust God in present trials.",                       attr:"Spencer W. Kimball"},
  {text:"Let your trial teach you empathy for others who suffer.",                     attr:"Anna P. Rowe"},
  {text:"Faith does not remove trials but lightens their burden by God's presence.",   attr:"Thomas S. Monson"},
  {text:"Cling to covenants in difficult times; they anchor you to eternal truths.",   attr:"Boyd K. Packer"},
  {text:"Scriptures are a mirror to the soul; study them daily and be changed.",       attr:"Russell M. Nelson"},
  {text:"Let the words of Christ dwell in you richly through daily study.",            attr:"Neal A. Maxwell"},
  {text:"Reading the scriptures invites revelation that directs your steps.",          attr:"Richard G. Scott"},
  {text:"Study with prayer and apply what you learn; knowledge without practice stagnates.",attr:"Dieter F. Uchtdorf"},
  {text:"Scripture study anchors faith and provides answers in times of doubt.",       attr:"Henry B. Eyring"},
  {text:"Make the scriptures a daily habit and watch your perspective widen.",         attr:"Gordon B. Hinckley"},
  {text:"When you feast on the words of Christ, you are nourished spiritually.",       attr:"Jeffrey R. Holland"},
  {text:"Ask the Spirit to teach you as you read; revelation often comes line upon line.",attr:"Karen L. Meadows"},
  {text:"Scriptures prepare you to meet trials with faith and wisdom.",                attr:"Boyd K. Packer"},
  {text:"Apply scripture teachings and let them reshape your daily choices.",          attr:"Spencer W. Kimball"},
  {text:"Repentance is the Savior's way of making us whole again.",                    attr:"Jeffrey R. Holland"},
  {text:"Humble yourself, confess, forsake, and the Lord will lift you up.",           attr:"Spencer W. Kimball"},
  {text:"True repentance requires action — change your behavior and change your heart.",attr:"Neal A. Maxwell"},
  {text:"Do not delay repentance; it lightens burdens and opens paths to peace.",      attr:"Henry B. Eyring"},
  {text:"Forgiveness through repentance renews relationships with God and others.",    attr:"Joseph Smith"},
  {text:"Let sorrow for sin lead you to joyful obedience and renewed commitment.",    attr:"Dallin H. Oaks"},
  {text:"Repentance is progress toward becoming the person God intended you to be.",   attr:"Margaret L. Olsen"},
  {text:"The mercies of the Lord are abundant for those who truly repent.",            attr:"Ezra Taft Benson"},
  {text:"Repentance is not punishment; it is the pathway to healing.",                 attr:"Gordon B. Hinckley"},
  {text:"Embrace repentance as a gift of grace that renews your spirit.",              attr:"Thomas S. Monson"},
  {text:"Hope is the anchor of the soul, holding us steady through life's storms.",   attr:"Russell M. Nelson"},
  {text:"Never lose hope; the Lord's timing is perfect though it may not match ours.",attr:"Jeffrey R. Holland"},
  {text:"Hope turns our gaze to eternal outcomes rather than temporary pain.",         attr:"Henry B. Eyring"},
  {text:"When you feel discouraged, remember that God's love and perspective are always larger.",attr:"Gordon B. Hinckley"},
  {text:"Hope is active; it invites us to keep moving forward in faith.",              attr:"Dieter F. Uchtdorf"},
  {text:"Cling to hope in prayer and service; both renew the spirit.",                 attr:"Neal A. Maxwell"},
  {text:"Hope gives patience a purpose and endurance a friend.",                       attr:"Samuel P. Larkin"},
  {text:"Light follows the smallest ember of hope that we refuse to extinguish.",     attr:"Spencer W. Kimball"},
  {text:"Let hope be your companion through the valley to the summit.",                attr:"Thomas S. Monson"},
  {text:"Hope grows when we remember God's past mercies and trust His future care.",   attr:"Brigham Young"},
];

function getQuoteOfDay() {
  const day = Math.floor(Date.now() / 86400000);
  return LDS_QUOTES[day % LDS_QUOTES.length];
}

// ── Article of the Day ────────────────────────────────
function getArticleOfDay(discoverData) {
  const section = (discoverData.sections || []).find(s => s.type === 'journals');
  const papers  = (section?.papers || []).filter(p => p.title && p.abstract);
  if (!papers.length) return null;
  const day = Math.floor(Date.now() / 86400000);
  return papers[day % papers.length];
}

// ── Recharge helpers ──────────────────────────────────
function getRechargeActive()       { try { return JSON.parse(localStorage.getItem('phd_recharge_active')) || null; } catch { return null; } }
function saveRechargeActive(entry) { localStorage.setItem('phd_recharge_active', JSON.stringify(entry)); }
function clearRechargeActive()     { localStorage.removeItem('phd_recharge_active'); }

function getRechargeLog()          { try { return JSON.parse(localStorage.getItem('phd_recharge_log')) || []; } catch { return []; } }
function saveRechargeLog(log)      { localStorage.setItem('phd_recharge_log', JSON.stringify(log.slice(0,100))); }

function addRechargeSession(item, category, startedAt, endedAt) {
  const duration = Math.round((new Date(endedAt) - new Date(startedAt)) / 1000);
  if (duration < 10) return;
  const log = getRechargeLog();
  log.unshift({ item, category, startedAt, endedAt, duration });
  saveRechargeLog(log);
}

function formatElapsed(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function formatAgo(isoString) {
  const diff = Math.round((Date.now() - new Date(isoString).getTime()) / 1000);
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.round(diff/60)}m ago`;
  if (diff < 86400) return `${Math.round(diff/3600)}h ago`;
  return `${Math.round(diff/86400)}d ago`;
}

// ── Site footer ───────────────────────────────────────
function footerHTML() {
  return `
    <div class="footer-inner">
      <span class="footer-brand">PhD OS</span>
      <nav class="footer-links">
        <a href="docs.html">Documentation</a>
      </nav>
    </div>`;
}

document.addEventListener('DOMContentLoaded', () => {
  const footer = document.createElement('footer');
  footer.className = 'site-footer';
  footer.innerHTML = footerHTML();
  document.body.appendChild(footer);

  // ── Easter egg: click the brand logo 7 times quickly ──
  let _brandClicks = 0, _brandTimer;
  document.querySelector('.brand')?.addEventListener('click', e => {
    e.preventDefault();
    _brandClicks++;
    clearTimeout(_brandTimer);
    _brandTimer = setTimeout(() => { _brandClicks = 0; }, 700);
    if (_brandClicks >= 7) {
      _brandClicks = 0;
      _confettiBurst();
    }
  });
});

function _confettiBurst() {
  const colors = ['#7c3aed','#e11d48','#0d9488','#d97706','#2563eb','#9333ea','#14b8a6','#fbbf24'];
  const origin = { x: window.innerWidth / 2, y: 56 };
  for (let i = 0; i < 32; i++) {
    const el    = document.createElement('div');
    el.className = 'confetti-piece';
    const angle  = (i / 32) * Math.PI * 2;
    const spread = 0.6 + Math.random() * 0.4;
    const dist   = 90 + Math.random() * 140;
    const dur    = 1.1 + Math.random() * 0.7;
    const delay  = i * 0.018;
    el.style.cssText = [
      `left:${origin.x + Math.cos(angle) * 12}px`,
      `top:${origin.y}px`,
      `background:${colors[i % colors.length]}`,
      `border-radius:${Math.random() > 0.5 ? '50%' : '2px'}`,
      `--dx:${(Math.cos(angle) * dist * spread).toFixed(1)}px`,
      `--dy:${(Math.sin(angle) * dist * 0.8 + 60 + Math.random() * 60).toFixed(1)}px`,
      `--dur:${dur.toFixed(2)}s`,
      `--delay:${delay.toFixed(3)}s`,
    ].join(';');
    document.body.appendChild(el);
    el.addEventListener('animationend', () => el.remove());
  }
}

// ── Standard nav HTML ─────────────────────────────────
function navHTML(active) {
  const pages = [
    ['index.html',     'home',      'Home'],
    ['papers.html',    'papers',    'Papers'],
    ['lit.html',       'lit',       'Discover'],
    ['library.html',   'library',   'Library'],
    ['books.html',     'books',     'Books'],
    ['projects.html',  'projects',  'Projects'],
    ['church.html',    'church',    'Church'],
    ['recharge.html',  'recharge',  'Recharge'],
    ['docs.html',      'docs',      'Docs'],
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
    <button class="theme-btn" id="nav-theme-btn" title="Force light mode">☀</button>
    <button class="settings-btn" title="Clear Todoist token">⚙</button>`;
}
