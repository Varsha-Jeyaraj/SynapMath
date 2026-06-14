/* App state */
let currentUser = null;   // { student_id, name }
let quizPaper   = [];
let practicePaper = [];
let currentPage = 'dashboard';
let quizLoadInFlight = false;
let practiceTabs = [];
let activePracticeTabId = '';
let selectedPracticeGrade = null;
let selectedPracticeTopic = '';
let brainGraphLayoutCache = null;
let brainGraphLayoutVersion = 0;
const BRAIN_LAYOUT_VERSION = 6;

/** Closed polygon approximating brainOutlinePathD (extra vertices on curved segments). */
const BRAIN_SILHOUETTE_POLY = [
  [24, 56], [17, 38], [15, 18], [30, 8], [50, 3.5], [72, 6], [88, 18],
  [93, 36], [90, 54], [84, 64], [74, 71], [82, 76], [87, 82],
  [78, 88], [62, 90], [52, 89], [40, 80], [32, 70], [26, 62],
];

function pointInPolygon(x, y, poly) {
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = poly[i][0];
    const yi = poly[i][1];
    const xj = poly[j][0];
    const yj = poly[j][1];
    const dy = yj - yi;
    if (Math.abs(dy) < 1e-9) continue;
    const intersect = (yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / dy + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}
const GAMIFICATION_BADGE_FALLBACK_TOTAL = 13;

/* SVG defs: score ring gradient */
(function injectSvgDefs() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0'); svg.setAttribute('height', '0');
  svg.style.position = 'absolute';
  svg.innerHTML = `<defs>
    <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="#3ecf8e"/>
      <stop offset="100%" stop-color="#5b9cf5"/>
    </linearGradient>
  </defs>`;
  document.body.prepend(svg);
})();

/* Helpers */
function esc(val) {
  const s = document.createElement('span');
  s.textContent = String(val ?? '');
  return s.innerHTML;
}

/** MCQ diagram from server (/static/quiz_images/<uuid>.png only). */
function questionFigureHtml(q) {
  const url = q && typeof q.image_url === 'string' ? q.image_url.trim() : '';
  if (!/^\/static\/quiz_images\/[a-f0-9]{32}\.png$/i.test(url)) return '';
  return `<figure class="question-figure"><img src="${url}" alt="Question diagram" loading="lazy" decoding="async" /></figure>`;
}

function setBtn(id, loading) {
  const btn    = document.getElementById(id);
  const label  = document.getElementById(id + 'Label');
  const spin   = document.getElementById('spin' + id.replace('btn', '').replace(/^./, c => c.toUpperCase()));
  if (!btn) return;
  btn.disabled = loading;
  if (label) label.style.opacity = loading ? '0' : '1';
  if (spin)  spin.classList.toggle('hidden', !loading);
}

function showError(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
}

function hideError(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

function setPracticeLoading(loading, message = 'Loading questions...') {
  const overlay = document.getElementById('practiceLoadingOverlay');
  const textEl = document.getElementById('practiceLoadingText');
  if (!overlay) return;
  if (textEl) textEl.textContent = message;
  overlay.classList.toggle('hidden', !loading);
}

function flashOk(id, msg = 'Saved') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 2500);
}

/* Auth */
function switchAuthTab(tab) {
  ['signin', 'signup'].forEach(t => {
    document.getElementById(`tab-${t}-btn`).classList.toggle('active', t === tab);
    document.getElementById(`panel-${t}`).classList.toggle('active', t === tab);
    document.getElementById(`tab-${t}-btn`).setAttribute('aria-selected', t === tab);
  });
  hideError('siError'); hideError('suError');
}

async function doSignIn() {
  hideError('siError');
  const email    = document.getElementById('siEmail').value.trim();
  const password = document.getElementById('siPassword').value;
  if (!email)    { showError('siError', 'Email is required.'); return; }
  if (!password) { showError('siError', 'Password is required.'); return; }
  setBtn('btnSignIn', true);
  try {
    const res  = await fetch('/api/signin', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ email, password }) });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') { showError('siError', data.message || 'Sign in failed.'); return; }
    onAuthenticated(data);
  } catch (e) { showError('siError', e.message); }
  finally { setBtn('btnSignIn', false); }
}

async function doSignUp() {
  hideError('suError');
  const name     = document.getElementById('suName').value.trim();
  const email    = document.getElementById('suEmail').value.trim();
  const password = document.getElementById('suPassword').value;
  if (!name)     { showError('suError', 'Name is required.'); return; }
  if (!email)    { showError('suError', 'Email is required.'); return; }
  if (!password) { showError('suError', 'Password is required.'); return; }
  setBtn('btnSignUp', true);
  try {
    const res  = await fetch('/api/signup', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name, email, password }) });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') { showError('suError', data.message || 'Sign up failed.'); return; }
    onAuthenticated(data);
  } catch (e) { showError('suError', e.message); }
  finally { setBtn('btnSignUp', false); }
}

async function doSignOut() {
  await fetch('/api/signout', { method: 'POST' });
  currentUser = null;
  brainGraphLayoutCache = null;
  brainGraphLayoutVersion = 0;
  quizPaper   = [];
  practicePaper = [];
  document.getElementById('app').classList.add('hidden');
  document.getElementById('auth-overlay').style.display = 'flex';
  // Clear auth fields
  document.getElementById('siEmail').value    = '';
  document.getElementById('siPassword').value = '';
  document.getElementById('suName').value     = '';
  document.getElementById('suEmail').value    = '';
  document.getElementById('suPassword').value = '';
  switchAuthTab('signin');
}

function onAuthenticated(data) {
  currentUser = { email: data.email, name: data.name || data.email };
  applyUserToUI();
  document.getElementById('auth-overlay').style.display = 'none';
  document.getElementById('app').classList.remove('hidden');
  applyStoredSidebarLayout();
  showPage('dashboard');
  loadDashboard();
}

function applyUserToUI() {
  if (!currentUser) return;
  const initials = currentUser.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  document.getElementById('userAvatar').textContent = initials;
  document.getElementById('userName').textContent   = currentUser.name;
}

/* Navigation */
const PAGE_TITLES = {
  dashboard : 'Dashboard',
  attempts  : 'Attempt Papers',
  quiz      : 'MCQ Quiz',
  guidance  : 'Study Guidance',
  practice  : 'Practice Quiz',
  settings  : 'Settings',
};

function isNarrowNav() {
  return window.matchMedia('(max-width: 1024px)').matches;
}

function showPage(name) {
  currentPage = name;
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === `page-${name}`));
  document.querySelectorAll('.nav-item[data-page]').forEach(n => n.classList.toggle('active', n.dataset.page === name));
  document.getElementById('topbarTitle').textContent = PAGE_TITLES[name] || name;
  if (isNarrowNav()) {
    document.getElementById('sidebar').classList.remove('open');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (backdrop) backdrop.classList.remove('open');
  }
  syncMobileMenuButton();
  syncSidebarPinUi();
  if (name === 'dashboard') loadDashboard();
  if (name === 'attempts') loadAttemptPapers();
  if (name === 'guidance') loadGuidance();
  if (name === 'practice') loadPracticeCatalog();
}

function syncMobileMenuButton() {
  const sidebar = document.getElementById('sidebar');
  const btn = document.getElementById('mobileMenuBtn');
  const icon = document.getElementById('mobileMenuIcon');
  const app = document.getElementById('app');
  if (!sidebar || !btn) return;
  const navOpen = isNarrowNav()
    ? sidebar.classList.contains('open')
    : !app?.classList.contains('sidebar-collapsed');
  btn.setAttribute('aria-expanded', navOpen ? 'true' : 'false');
  btn.setAttribute('aria-label', navOpen ? 'Close menu' : 'Open menu');
  if (icon) icon.textContent = navOpen ? 'close' : 'menu';
}

function syncSidebarPinUi() {
  const app = document.getElementById('app');
  const btn = document.getElementById('sidebarPinBtn');
  if (!btn || !app) return;
  const collapsed = app.classList.contains('sidebar-collapsed');
  btn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
  btn.setAttribute('aria-label', collapsed ? 'Pin sidebar open' : 'Hide sidebar');
  const icon = btn.querySelector('.sidebar-pin-icon');
  const label = btn.querySelector('.sidebar-pin-label');
  if (icon) icon.textContent = collapsed ? 'last_page' : 'first_page';
  if (label) label.textContent = collapsed ? 'Pin sidebar open' : 'Hide sidebar';
}

function applyStoredSidebarLayout() {
  const app = document.getElementById('app');
  if (!app) return;
  try {
    if (localStorage.getItem('synapSidebarCollapsed') === '1' && window.matchMedia('(min-width: 1025px)').matches) {
      app.classList.add('sidebar-collapsed');
    }
  } catch (_) {}
  syncSidebarPinUi();
  syncMobileMenuButton();
}

function toggleSidebarPin() {
  if (isNarrowNav()) return;
  toggleSidebar();
}

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const app = document.getElementById('app');
  if (!sidebar || !app) return;

  if (isNarrowNav()) {
    sidebar.classList.toggle('open');
    const isOpen = sidebar.classList.contains('open');
    if (backdrop) backdrop.classList.toggle('open', isOpen);
  } else {
    app.classList.toggle('sidebar-collapsed');
    sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
    try {
      localStorage.setItem('synapSidebarCollapsed', app.classList.contains('sidebar-collapsed') ? '1' : '0');
    } catch (_) {}
    syncSidebarPinUi();
  }
  syncMobileMenuButton();
}
function formatSystemTime(isoText) {
  if (!isoText) return '';
  const hasTimezone = /([zZ]|[+\-]\d{2}:?\d{2})$/.test(isoText);
  const normalized = hasTimezone ? isoText : `${isoText}Z`;
  const dt = new Date(normalized);
  if (Number.isNaN(dt.getTime())) return String(isoText);
  return dt.toLocaleString();
}

function goToAttempts() {
  showPage('attempts');
}

/* Dashboard */
async function loadDashboard() {
  if (!currentUser) return;
  document.getElementById('welcomeHeading').textContent = `Welcome back, ${currentUser.name}!`;

  // Primary dashboard source: attempts API
  let attempts = [];
  try {
    const res = await fetch('/api/attempts');
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      attempts = data.attempts || [];
    } else {
      attempts = [];
    }
  } catch (_) {
    attempts = [];
  }

  const scores = attempts.map(a => Number(a.score_percent)).filter(v => Number.isFinite(v));
  const avg = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
  const last = scores.length ? scores[0] : null; // latest first
  document.getElementById('statAttempts').textContent = attempts.length || '0';
  document.getElementById('statAvgScore').textContent = avg != null ? `${avg}%` : '-';
  document.getElementById('statLastScore').textContent = last != null ? `${Math.round(last)}%` : '-';

  // Secondary source: recommendation endpoint for weak topics only
  try {
    const res = await fetch('/api/recommend');
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      document.getElementById('statWeakTopics').textContent = (data.weak_topics || []).length || '0';
    } else {
      document.getElementById('statWeakTopics').textContent = '0';
    }
  } catch (_) {
    document.getElementById('statWeakTopics').textContent = '0';
  }

  try {
    const gRes = await fetch('/api/gamification');
    const gData = await gRes.json();
    if (gRes.ok && gData.status === 'ok') {
      renderSynapticBrain(gData.attempts_by_day || {}, gData.stats || {});
      renderAchievementBadges(gData.badges || [], gData.earned_count, gData.total_badges);
    } else {
      renderSynapticBrain({}, {});
      renderAchievementBadges([], 0, GAMIFICATION_BADGE_FALLBACK_TOTAL);
    }
  } catch (_) {
    renderSynapticBrain({}, {});
    renderAchievementBadges([], 0, GAMIFICATION_BADGE_FALLBACK_TOTAL);
  }
}

function hashStr(s) {
  let h = 0;
  const str = String(s ?? '');
  for (let i = 0; i < str.length; i++) h = Math.imul(31, h) + str.charCodeAt(i) | 0;
  return Math.abs(h);
}

function insideBrainSilhouette(x, y) {
  return pointInPolygon(x, y, BRAIN_SILHOUETTE_POLY);
}

function brainOutlinePathD() {
  return [
    'M 24 56',
    'C 14 44 11 28 15 18',
    'C 19 8 34 3 50 3.5',
    'C 68 4 84 12 92 26',
    'C 98 40 96 54 88 62',
    'C 82 68 76 71 70 72',
    'C 78 73 88 78 86 84',
    'C 84 89 76 90 68 87',
    'C 62 88 58 91 52 89',
    'C 46 86 42 80 36 74',
    'C 30 68 26 62 24 56',
    'Z',
  ].join(' ');
}

function brainFissurePathD() {
  return [
    'M 44 6 C 40 18 36 32 34 46',
    'M 18 44 C 32 38 50 34 72 32 C 78 32 84 36 88 42',
  ].join(' ');
}

function brainSulciDecorSvg() {
  return [
    '<path class="brain-sulcus" d="M 76 14 C 74 24 72 34 70 44" fill="none"/>',
    '<path class="brain-sulcus" d="M 84 22 C 86 30 87 40 84 50" fill="none"/>',
    '<path class="brain-sulcus" d="M 62 6 C 60 16 58 26 56 36" fill="none"/>',
    '<path class="brain-sulcus" d="M 28 12 C 34 10 40 11 44 8" fill="none"/>',
    '<path class="brain-sulcus" d="M 22 22 C 30 18 38 17 46 15" fill="none"/>',
    '<path class="brain-sulcus" d="M 20 34 C 28 30 36 28 44 26" fill="none"/>',
    '<path class="brain-sulcus" d="M 36 8 Q 40 12 38 18" fill="none"/>',
    '<path class="brain-sulcus" d="M 50 6 Q 52 14 50 22" fill="none"/>',
    '<path class="brain-sulcus" d="M 48 28 C 46 36 44 44 42 50" fill="none"/>',
    '<path class="brain-sulcus" d="M 58 18 C 56 28 54 38 52 46" fill="none"/>',
    '<path class="brain-sulcus" d="M 32 42 C 38 40 44 41 48 44" fill="none"/>',
    '<path class="brain-sulcus" d="M 26 54 C 34 52 42 53 50 56" fill="none"/>',
    '<path class="brain-sulcus" d="M 28 62 C 36 60 44 60 52 62" fill="none"/>',
    '<path class="brain-sulcus" d="M 30 70 C 38 68 46 68 54 70" fill="none"/>',
    '<path class="brain-sulcus brain-sulcus--cereb" d="M 78 74 C 82 73 86 74 89 76" fill="none"/>',
    '<path class="brain-sulcus brain-sulcus--cereb" d="M 77 78 C 81 77 85 78 88 80" fill="none"/>',
    '<path class="brain-sulcus brain-sulcus--cereb" d="M 76 82 C 80 81 84 82 86 84" fill="none"/>',
    '<path class="brain-sulcus brain-sulcus--cereb" d="M 75 86 C 78 85 81 86 83 87" fill="none"/>',
  ].join('');
}

function buildBrainGraph() {
  const salt = hashStr(currentUser?.email || 'anon');
  let seed = salt || 1;
  const rnd = () => {
    seed = (Math.imul(seed, 1103515245) + 12345) | 0;
    return (seed >>> 0) / 0xffffffff;
  };

  const nodes = [];
  for (let k = 0; k < 24000 && nodes.length < 260; k++) {
    const x = 8 + rnd() * 90;
    const y = 2 + rnd() * 92;
    if (insideBrainSilhouette(x, y)) nodes.push({ x, y });
  }

  const maxD = 7.5;
  const edgeSet = new Set();
  const edges = [];

  const addEdge = (i, j) => {
    const a = Math.min(i, j);
    const b = Math.max(i, j);
    const key = `${a},${b}`;
    if (edgeSet.has(key)) return;
    edgeSet.add(key);
    edges.push([a, b]);
  };

  for (let i = 0; i < nodes.length; i++) {
    const dists = [];
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      const d = Math.hypot(dx, dy);
      if (d < maxD) dists.push({ j, d });
    }
    dists.sort((a, b) => a.d - b.d);
    for (const { j } of dists.slice(0, 5)) addEdge(i, j);
  }

  const frontal = nodes.map((p, idx) => ({ idx, p })).filter(({ p }) => p.x < 42);
  const posterior = nodes.map((p, idx) => ({ idx, p })).filter(({ p }) => p.x > 58);
  for (let b = 0; b < 14; b++) {
    if (!frontal.length || !posterior.length) break;
    const F = frontal[(b * 7 + salt) % frontal.length].idx;
    const P = posterior[(b * 11 + salt * 3) % posterior.length].idx;
    addEdge(F, P);
  }

  return { nodes, edges };
}

function getBrainGraph() {
  if (!brainGraphLayoutCache || brainGraphLayoutVersion !== BRAIN_LAYOUT_VERSION) {
    brainGraphLayoutCache = buildBrainGraph();
    brainGraphLayoutVersion = BRAIN_LAYOUT_VERSION;
  }
  return brainGraphLayoutCache;
}

function nodeWeightsFromAttempts(byDay, nNodes) {
  const w = new Float64Array(nNodes);
  const salt = hashStr(currentUser?.email || 'anon');
  const entries = Object.entries(byDay || {});
  entries.forEach(([date, count]) => {
    const c = Math.max(0, Number(count) || 0);
    const pulses = Math.min(10, 2 + Math.ceil(c));
    for (let t = 0; t < pulses; t++) {
      const idx = (hashStr(`${date}:${t}`) + salt) % nNodes;
      w[idx] += 0.28 + Math.min(1.4, c * 0.12);
    }
  });
  let mx = 0;
  for (let i = 0; i < w.length; i++) if (w[i] > mx) mx = w[i];
  if (mx > 0) for (let i = 0; i < w.length; i++) w[i] /= mx;
  return w;
}

function synapseNodeColor(t) {
  const cold = { r: 40, g: 90, b: 180 };
  const mid = { r: 60, g: 160, b: 255 };
  const hot = { r: 160, g: 230, b: 255 };
  const a = t <= 0.5 ? t / 0.5 : (t - 0.5) / 0.5;
  const A = t <= 0.5 ? cold : mid;
  const B = t <= 0.5 ? mid : hot;
  const r = Math.round(A.r + (B.r - A.r) * a);
  const gCh = Math.round(A.g + (B.g - A.g) * a);
  const bCh = Math.round(A.b + (B.b - A.b) * a);
  return `rgb(${r},${gCh},${bCh})`;
}

function renderSynapticBrain(attemptsByDay, stats) {
  const svg = document.getElementById('synapseBrainSvg');
  const pills = document.getElementById('synapseStatPills');
  if (!svg) return;

  const { nodes, edges } = getBrainGraph();
  const w = nodeWeightsFromAttempts(attemptsByDay, nodes.length);

  const pillParts = [];
  if (stats.current_day_streak != null) {
    pillParts.push(`<span class="synapse-pill">Streak: <strong>${Number(stats.current_day_streak) || 0}</strong> d</span>`);
  }
  if (stats.best_day_streak != null) {
    pillParts.push(`<span class="synapse-pill">Best: <strong>${Number(stats.best_day_streak) || 0}</strong> d</span>`);
  }
  if (stats.distinct_practice_days != null) {
    pillParts.push(`<span class="synapse-pill">Practice days: <strong>${Number(stats.distinct_practice_days) || 0}</strong></span>`);
  }
  if (pills) pills.innerHTML = pillParts.join('');

  const edgeEls = edges.map(([i, j]) => {
    const wi = w[i];
    const wj = w[j];
    const strength = Math.sqrt((0.06 + wi) * (0.06 + wj));
    const opacity = 0.12 + strength * 0.78;
    const sw = 0.15 + strength * 0.85;
    const x1 = nodes[i].x;
    const y1 = nodes[i].y;
    const x2 = nodes[j].x;
    const y2 = nodes[j].y;
    return `<line class="synapse-edge" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke-width="${sw}" style="opacity:${opacity}" />`;
  }).join('');

  const nodeEls = nodes.map((p, idx) => {
    const t = w[idx];
    const fill = synapseNodeColor(t);
    const r = 0.55 + t * 0.95;
    const o = 0.35 + t * 0.65;
    return `<circle class="synapse-node" cx="${p.x}" cy="${p.y}" r="${r}" fill="${fill}" style="opacity:${o}" />`;
  }).join('');

  const clipId = 'synapseBrainMassClip';
  const glowId = 'synapseGlowFilter';
  const outline = brainOutlinePathD();
  const fissure = brainFissurePathD();
  const sulci = brainSulciDecorSvg();

  const outerGlowId = 'brainOuterGlow';
  svg.innerHTML = `
    <defs>
      <clipPath id="${clipId}">
        <path d="${outline}" />
      </clipPath>
      <filter id="${glowId}" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="0.45" result="b" />
        <feMerge>
          <feMergeNode in="b" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
      <filter id="${outerGlowId}" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="1.8" result="glow" />
        <feMerge>
          <feMergeNode in="glow" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>
    <g filter="url(#${outerGlowId})">
      <path class="brain-silhouette-fill" d="${outline}" />
    </g>
    ${sulci}
    <path class="brain-silhouette-stroke" d="${outline}" fill="none" />
    <g clip-path="url(#${clipId})" filter="url(#${glowId})">${edgeEls}${nodeEls}</g>
    <path class="brain-fissure" d="${fissure}" fill="none" />
  `;
}

function renderAchievementBadges(badges, earnedCount, totalBadges) {
  const host = document.getElementById('achievementBadges');
  const progress = document.getElementById('badgeProgressText');
  if (progress) {
    const e = earnedCount != null ? earnedCount : (badges || []).filter(b => b.earned).length;
    const t = totalBadges != null ? totalBadges : (badges || []).length;
    progress.textContent = `${e} / ${t} unlocked`;
  }
  if (!host) return;
  if (!badges || !badges.length) {
    host.innerHTML = '<p class="badges-empty">Complete quizzes to unlock achievements.</p>';
    return;
  }
  host.innerHTML = badges.map(b => {
    const earned = !!b.earned;
    const cls = ['achievement-badge', earned ? 'achievement-badge--earned' : 'achievement-badge--locked'].join(' ');
    const icon = /^[a-z0-9_]+$/.test(String(b.icon || '')) ? b.icon : 'stars';
    return `
      <div class="${cls}" title="${esc(b.description || '')}">
        <span class="material-symbols-outlined achievement-badge-icon" aria-hidden="true">${esc(icon)}</span>
        <span class="achievement-badge-title">${esc(b.title || '')}</span>
        <span class="achievement-badge-desc">${esc(b.description || '')}</span>
      </div>
    `;
  }).join('');
}

async function loadAttemptPapers() {
  if (!currentUser) return;
  const container = document.getElementById('attemptsList');
  if (!container) return;
  try {
    const res = await fetch('/api/attempts');
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      renderAttempts(data.attempts || []);
    } else {
      renderAttempts([]);
    }
  } catch (_) {
    renderAttempts([]);
  }
}

function renderAttempts(attempts) {
  const container = document.getElementById('attemptsList');
  if (!container) return;
  if (!attempts.length) {
    container.innerHTML = '<p class="attempts-empty">No attempts yet. Complete a quiz to see full paper review here.</p>';
    return;
  }

  const letters = ['A', 'B', 'C', 'D'];
  container.innerHTML = attempts.map((attempt) => {
    const created = formatSystemTime(attempt.created_at);
    const questionsHtml = (attempt.questions || []).map((q, idx) => {
      const options = q.options || [];
      const optionsHtml = options.map((opt, oi) => {
        const letter = letters[oi] || '';
        const isSelected = q.student_answer === letter;
        const isCorrect = q.correct_answer === letter;
        const classes = ['attempt-opt'];
        if (isSelected) classes.push('selected');
        if (isCorrect) classes.push('correct');
        return `<li class="${classes.join(' ')}"><strong>${letter}.</strong> ${esc(opt)}</li>`;
      }).join('');

      return `
        <div class="attempt-question ${q.is_correct ? 'correct' : 'incorrect'}">
          <div class="attempt-q-head">
            <span>Q${idx + 1}</span>
            <span>${q.is_correct ? 'Correct' : 'Incorrect'}</span>
          </div>
          <p class="attempt-q-text">${esc(q.question)}</p>
          ${questionFigureHtml(q)}
          <ul class="attempt-options">${optionsHtml}</ul>
          <p class="attempt-ans">Selected: <strong>${esc(q.student_answer || '-')}</strong>${q.student_option_text ? ` (${esc(q.student_option_text)})` : ''}</p>
          <p class="attempt-ans">Correct: <strong>${esc(q.correct_answer || '-')}</strong>${q.correct_option_text ? ` (${esc(q.correct_option_text)})` : ''}</p>
          ${q.explanation ? `<p class="attempt-exp">${esc(q.explanation)}</p>` : ''}
        </div>
      `;
    }).join('');

    return `
      <details class="attempt-card">
        <summary>
          <span>Attempt #${attempt.attempt_id} - ${esc(created)} (system time)</span>
          <span>${attempt.score_percent}% (${attempt.correct}/${attempt.total_questions})</span>
        </summary>
        <div class="attempt-questions">${questionsHtml}</div>
      </details>
    `;
  }).join('');
}
/* Load diagnostic quiz (single /api/quiz/load request). */
async function loadQuiz() {
  if (quizLoadInFlight) return;
  quizLoadInFlight = true;
  hideError('quizLoadError');
  setBtn('btnLoadQuiz', true);
  document.getElementById('quizForm').classList.add('hidden');
  document.getElementById('quizResults').classList.add('hidden');

  try {
    const loadRes = await fetch('/api/quiz/load', { method: 'POST' });
    const generateData = await loadRes.json();
    if (!loadRes.ok || generateData.status !== 'ok') {
      showError('quizLoadError', generateData.message || 'Failed to load quiz.');
      return;
    }

    quizPaper = generateData.paper || [];
    if ((!quizPaper || !quizPaper.length) && generateData.mode && generateData.mode !== 'rag') {
      // Try persisted paper even when generation falls back, so quiz can still start.
      const paperRes = await fetch('/api/paper');
      const paperData = await paperRes.json();
      if (paperRes.ok && paperData.status === 'ok') {
        quizPaper = paperData.paper || [];
      }
    }
    if (generateData.mode && generateData.mode !== 'rag') {
      showError('quizLoadError', generateData.message || 'Using fallback quiz content because RAG generation is unavailable.');
    }
    if (!quizPaper.length) {
      showError('quizLoadError', 'No questions were generated from loadQuizRef content.');
      return;
    }
    renderQuizForm(quizPaper);
    document.getElementById('quiz-landing').classList.add('hidden');
    document.getElementById('quizForm').classList.remove('hidden');
  } catch (e) { showError('quizLoadError', e.message); }
  finally {
    quizLoadInFlight = false;
    setBtn('btnLoadQuiz', false);
  }
}

function openQuizAndLoad() {
  showPage('quiz');
}

function renderQuizForm(paper) {
  const letters = ['A', 'B', 'C', 'D'];
  const container = document.getElementById('quizQuestions');
  container.innerHTML = paper.map((q, i) => `
    <article class="question-card">
      <div class="question-num">Question ${i + 1} of ${paper.length}</div>
      <p class="question-text">${esc(q.question)}</p>
      ${questionFigureHtml(q)}
      ${(q.options || []).map((opt, oi) => {
        const v = letters[oi] || '';
        return `<label class="choice-label">
          <input type="radio" name="q${i}" value="${v}" onchange="updateProgress()">
          <span class="choice-marker">${v}</span>
          <span>${esc(opt)}</span>
        </label>`;
      }).join('')}
    </article>
  `).join('');
  updateProgress();
}

function updateProgress() {
  const answered = quizPaper.filter((_, i) => document.querySelector(`input[name="q${i}"]:checked`)).length;
  const pct = quizPaper.length ? (answered / quizPaper.length) * 100 : 0;
  document.getElementById('quizProgressBar').style.width = `${pct}%`;
}

async function submitQuiz(event) {
  event.preventDefault();
  setBtn('btnSubmitQuiz', true);
  const answers = {};
  quizPaper.forEach((_, i) => {
    const sel = document.querySelector(`input[name="q${i}"]:checked`);
    if (sel) answers[i] = sel.value;
  });
  try {
    const res  = await fetch('/api/submit', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ answers }) });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') { alert(data.message || 'Submission failed.'); return; }
    renderResults(data);
    document.getElementById('quizForm').classList.add('hidden');
    document.getElementById('quizResults').classList.remove('hidden');
    loadDashboard();
    if (currentPage === 'attempts') loadAttemptPapers();
  } catch (e) { alert(e.message); }
  finally { setBtn('btnSubmitQuiz', false); }
}

function renderResults(data) {
  const pct = data.score_percent ?? 0;
  document.getElementById('scorePct').textContent    = `${pct}%`;
  document.getElementById('scoreSubText').textContent = `${data.correct}/${data.total}`;

  // Animate ring
  const circ = 2 * Math.PI * 50;
  const fill = (pct / 100) * circ;
  document.getElementById('scoreRingFill').setAttribute('stroke-dasharray', `${fill} ${circ}`);

  const grade = pct >= 80 ? 'Excellent!' : pct >= 60 ? 'Good work!' : 'Keep going!';
  document.getElementById('resultHeading').textContent    = grade;
  document.getElementById('resultSubHeading').textContent = `Score: ${pct}% — ${data.correct} correct, ${data.incorrect} incorrect`;

  const reviewList = document.getElementById('reviewList');
  reviewList.innerHTML = (data.review || []).map((item, i) => `
    <div class="review-card ${item.is_correct ? 'correct' : 'incorrect'}">
      <div class="review-status">${item.is_correct ? 'Correct' : 'Incorrect'}</div>
      <p class="review-question">Q${i + 1}. ${esc(item.question)}</p>
      ${questionFigureHtml(item)}
      <p class="review-answer">Your answer: <strong>${esc(item.student_answer || '—')}</strong></p>
      ${!item.is_correct ? `<p class="review-answer">Correct answer: <strong>${esc(item.correct_answer)}</strong></p>` : ''}
      ${item.explanation ? `<p class="review-explanation">${esc(item.explanation)}</p>` : ''}
    </div>
  `).join('');
}

function resetQuiz() {
  quizPaper = [];
  document.getElementById('quizResults').classList.add('hidden');
  document.getElementById('quizForm').classList.add('hidden');
  document.getElementById('quizLoadError').classList.add('hidden');
  document.getElementById('quiz-landing').classList.remove('hidden');
  document.getElementById('quizProgressBar').style.width = '0%';
}

/* Guidance page */
async function loadGuidance() {
  hideError('guidanceError');
  setBtn('btnGuidance', true);
  document.getElementById('guidanceContent').classList.add('hidden');
  try {
    const attemptSelect = document.getElementById('guidanceAttemptSelect');
    const selectedAttemptId = attemptSelect && attemptSelect.value ? Number(attemptSelect.value) : null;
    const query = selectedAttemptId ? `?attempt_id=${encodeURIComponent(selectedAttemptId)}` : '';
    const res  = await fetch(`/api/recommend${query}`);
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') { showError('guidanceError', data.message || 'Failed to load guidance.'); return; }
    populateGuidanceAttempts(data.attempts || [], data.selected_attempt_id);
    renderGuidance(data);
    document.getElementById('guidanceContent').classList.remove('hidden');
  } catch (e) { showError('guidanceError', e.message); }
  finally { setBtn('btnGuidance', false); }
}

function populateGuidanceAttempts(attempts, selectedAttemptId) {
  const select = document.getElementById('guidanceAttemptSelect');
  if (!select) return;
  if (!attempts.length) {
    select.innerHTML = '<option value="">No attempts yet</option>';
    select.disabled = true;
    return;
  }

  select.disabled = false;
  select.innerHTML = attempts.map(a => {
    const selected = Number(a.attempt_id) === Number(selectedAttemptId) ? ' selected' : '';
    return `<option value="${Number(a.attempt_id)}"${selected}>${esc(a.label || `Attempt ${a.attempt_id}`)}</option>`;
  }).join('');
}

function renderGuidance(data) {
  // Score trend sparkline
  const trend  = data.score_trend || [];
  const canvas = document.getElementById('trendCanvas');
  const ctx    = canvas.getContext('2d');
  canvas.width = canvas.parentElement.clientWidth - 48 || 260;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (trend.length >= 2) {
    const max = Math.max(...trend, 100);
    const min = Math.min(...trend, 0);
    const px  = i => (i / (trend.length - 1)) * canvas.width;
    const py  = v => canvas.height - ((v - min) / (max - min + 1)) * canvas.height;

    const grad = ctx.createLinearGradient(0, 0, canvas.width, 0);
    grad.addColorStop(0, '#6c63ff');
    grad.addColorStop(1, '#8b5cf6');
    ctx.strokeStyle  = grad;
    ctx.lineWidth    = 2.5;
    ctx.lineJoin     = 'round';
    ctx.beginPath();
    trend.forEach((v, i) => i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v)));
    ctx.stroke();

    // Dots
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--focus-green').trim() || '#3ecf8e';
    trend.forEach((v, i) => { ctx.beginPath(); ctx.arc(px(i), py(v), 4, 0, Math.PI * 2); ctx.fill(); });
  } else {
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--ink3').trim() || '#9aa3b8';
    ctx.font = '0.85rem Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No attempts yet', canvas.width / 2, canvas.height / 2);
  }

  const label = trend.length ? `Scores: ${trend.join(' -> ')}` : 'No attempts yet';
  document.getElementById('trendLabel').textContent = label;

  // Focus areas: ranked weak topics + Bloom-level mix (not chapter paths)
  const weakList = document.getElementById('weakTopicsList');
  weakList.innerHTML = (data.weak_topics || []).length
    ? data.weak_topics.map((t, idx) => {
      const br = t.difficulty_breakdown
        ? `<div class="topic-meta">Bloom levels: ${esc(t.difficulty_breakdown)}</div>`
        : '';
      return `<li class="topic-item"><span class="topic-rank" aria-hidden="true">${idx + 1}</span><span class="topic-dot"></span><div class="topic-item-text"><div class="topic-line"><strong>${esc(t.topic_name || t.topic_id)}</strong> — ${Number(t.mistakes || 0)} mistake(s)</div>${br}</div></li>`;
    }).join('')
    : '<li style="color:var(--ink3);font-size:.9rem">No weak topics identified yet.</li>';

  // Recommendations: summary paragraphs + per-topic practice actions (grades/chapters)
  const summaryEl = document.getElementById('guidanceSummary');
  const notesList = document.getElementById('guidanceNotesList');
  const summaries = (data.guidance_notes || []).map(n => `<p class="guidance-summary-p">${esc(n)}</p>`).join('');
  if (summaryEl) summaryEl.innerHTML = summaries;

  const recs = data.study_recommendations || [];
  const actionItems = recs.map((r) => {
    const actions = (r.practice_actions || []).length
      ? `<ul class="reco-actions">${r.practice_actions.map(a => `<li>${esc(a)}</li>`).join('')}</ul>`
      : '<p class="reco-fallback">No mapped textbook chapters for this topic.</p>';
    const tip = r.tip ? `<div class="reco-tip">${esc(r.tip)}</div>` : '';
    return `
      <li class="note-item reco-item">
        <span class="material-symbols-outlined note-item-icon" aria-hidden="true">school</span>
        <div class="reco-body">
          <div class="reco-head">
            <strong>${esc(r.topic_name || r.topic_id)}</strong>
            <span class="reco-pill">${Number(r.mistakes || 0)} error(s)</span>
          </div>
          ${tip}
          ${actions}
        </div>
      </li>
    `;
  });

  if (recs.length) {
    notesList.innerHTML = actionItems.join('');
  } else {
    notesList.innerHTML = '<li style="color:var(--ink3);font-size:.9rem">Complete a quiz with incorrect answers to see practice targets here.</li>';
  }

}

async function startPracticeQuiz(grade, topic) {
  hideError('practiceError');
  hideError('practiceQuizLoadError');
  setPracticeLoading(true, `Loading questions for Grade ${grade} - ${topic}...`);
  quizLoadInFlight = true;
  document.getElementById('practiceQuizHint').classList.add('hidden');
  document.getElementById('practiceQuizForm').classList.add('hidden');
  document.getElementById('practiceQuizResults').classList.add('hidden');
  try {
    const res = await fetch('/api/practice/quiz', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grade, topic }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') {
      showError('practiceQuizLoadError', data.message || 'Failed to load practice quiz.');
      return;
    }

    practicePaper = data.paper || [];
    if (!practicePaper.length) {
      showError('practiceQuizLoadError', 'No practice questions were generated for this topic.');
      return;
    }

    renderPracticeQuizForm(practicePaper);
    document.getElementById('practiceQuizForm').classList.remove('hidden');
    document.getElementById('practiceQuizForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (data.mode && data.mode !== 'rag' && data.message) {
      showError('practiceQuizLoadError', data.message);
    }
  } catch (e) {
    showError('practiceQuizLoadError', e.message);
  } finally {
    setPracticeLoading(false);
    quizLoadInFlight = false;
  }
}

async function loadPracticeCatalog() {
  hideError('practiceError');
  try {
    const res = await fetch('/api/practice/catalog');
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') {
      showError('practiceError', data.message || 'Failed to load practice topics.');
      return;
    }
    practiceTabs = data.tabs || [];
    if (!practiceTabs.length) {
      document.getElementById('practiceTopicTabs').innerHTML = '';
      document.getElementById('practiceTopicPanel').innerHTML = '<p style="color:var(--ink3);font-size:.9rem">No practice topics available.</p>';
      return;
    }
    if (!practiceTabs.some(t => t.tab_id === activePracticeTabId)) {
      activePracticeTabId = practiceTabs[0].tab_id;
    }
    renderPracticeTabs();
    renderPracticePanel(activePracticeTabId);
  } catch (e) {
    showError('practiceError', e.message);
  }
}

function renderPracticeTabs() {
  const tabsEl = document.getElementById('practiceTopicTabs');
  if (!tabsEl) return;
  tabsEl.innerHTML = (practiceTabs || []).map(tab => `
    <button type="button" class="practice-tab-btn ${tab.tab_id === activePracticeTabId ? 'active' : ''}" data-tab-id="${esc(tab.tab_id)}">
      ${esc(tab.label)}
    </button>
  `).join('');

  tabsEl.querySelectorAll('.practice-tab-btn[data-tab-id]').forEach((btn) => {
    btn.onclick = () => {
      activePracticeTabId = btn.getAttribute('data-tab-id') || '';
      renderPracticeTabs();
      renderPracticePanel(activePracticeTabId);
    };
  });
}

function renderPracticePanel(tabId) {
  const panelEl = document.getElementById('practiceTopicPanel');
  if (!panelEl) return;
  const tab = (practiceTabs || []).find(t => t.tab_id === tabId);
  if (!tab) {
    panelEl.innerHTML = '<p style="color:var(--ink3);font-size:.9rem">Select a topic tab.</p>';
    return;
  }

  panelEl.innerHTML = (tab.grades || []).map(g => {
    const buttons = (g.subtopics || []).map(s => {
      const topic = String(s.topic || '');
      const topicEncoded = encodeURIComponent(String(s.topic || ''));
      const isActive = Number(g.grade) === Number(selectedPracticeGrade) && topic === selectedPracticeTopic;
      return `
        <button type="button" class="practice-subtopic-btn ${isActive ? 'active' : ''}" data-grade="${Number(g.grade)}" data-topic="${topicEncoded}">
          ${esc(s.topic)}
        </button>
      `;
    }).join('');
    return `
      <div class="practice-grade-block">
        <div class="practice-grade-title">Grade ${Number(g.grade)}</div>
        <div class="practice-subtopic-grid">${buttons}</div>
      </div>
    `;
  }).join('');

  panelEl.querySelectorAll('.practice-subtopic-btn[data-grade][data-topic]').forEach((btn) => {
    btn.onclick = () => {
      const grade = Number(btn.getAttribute('data-grade') || '0');
      const topic = decodeURIComponent(btn.getAttribute('data-topic') || '');
      selectedPracticeGrade = grade;
      selectedPracticeTopic = topic;
      renderPracticePanel(activePracticeTabId);
      document.getElementById('practiceQuizHint').scrollIntoView({ behavior: 'smooth', block: 'start' });
      startPracticeQuiz(grade, topic);
    };
  });
}

function renderPracticeQuizForm(paper) {
  const letters = ['A', 'B', 'C', 'D'];
  const container = document.getElementById('practiceQuizQuestions');
  container.innerHTML = paper.map((q, i) => `
    <article class="question-card">
      <div class="question-num">Question ${i + 1} of ${paper.length}</div>
      <p class="question-text">${esc(q.question)}</p>
      ${questionFigureHtml(q)}
      ${(q.options || []).map((opt, oi) => {
        const v = letters[oi] || '';
        return `<label class="choice-label">
          <input type="radio" name="pq${i}" value="${v}" onchange="updatePracticeProgress()">
          <span class="choice-marker">${v}</span>
          <span>${esc(opt)}</span>
        </label>`;
      }).join('')}
    </article>
  `).join('');
  updatePracticeProgress();
}

function updatePracticeProgress() {
  const answered = practicePaper.filter((_, i) => document.querySelector(`input[name="pq${i}"]:checked`)).length;
  const pct = practicePaper.length ? (answered / practicePaper.length) * 100 : 0;
  document.getElementById('practiceQuizProgressBar').style.width = `${pct}%`;
}

async function submitPracticeQuiz(event) {
  event.preventDefault();
  setBtn('btnSubmitPractice', true);
  const answers = {};
  practicePaper.forEach((_, i) => {
    const sel = document.querySelector(`input[name="pq${i}"]:checked`);
    if (sel) answers[i] = sel.value;
  });
  try {
    const res = await fetch('/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') {
      showError('practiceQuizLoadError', data.message || 'Practice submission failed.');
      return;
    }
    renderPracticeResults(data);
    document.getElementById('practiceQuizForm').classList.add('hidden');
    document.getElementById('practiceQuizResults').classList.remove('hidden');
    loadDashboard();
    if (currentPage === 'attempts') loadAttemptPapers();
  } catch (e) {
    showError('practiceQuizLoadError', e.message);
  } finally {
    setBtn('btnSubmitPractice', false);
  }
}

function renderPracticeResults(data) {
  const pct = Number(data.score_percent ?? 0);
  document.getElementById('practiceResultHeading').textContent = pct >= 80 ? 'Excellent Practice' : pct >= 60 ? 'Good Practice' : 'Keep Practicing';
  document.getElementById('practiceResultSubHeading').textContent = `${data.correct} correct, ${data.incorrect} incorrect`;
  document.getElementById('practiceScoreText').textContent = `Score: ${pct}% (${data.correct}/${data.total})`;

  const reviewList = document.getElementById('practiceReviewList');
  reviewList.innerHTML = (data.review || []).map((item, i) => `
    <div class="review-card ${item.is_correct ? 'correct' : 'incorrect'}">
      <div class="review-status">${item.is_correct ? 'Correct' : 'Incorrect'}</div>
      <p class="review-question">Q${i + 1}. ${esc(item.question)}</p>
      ${questionFigureHtml(item)}
      <p class="review-answer">Your answer: <strong>${esc(item.student_answer || '-')}</strong></p>
      ${!item.is_correct ? `<p class="review-answer">Correct answer: <strong>${esc(item.correct_answer)}</strong></p>` : ''}
      ${item.explanation ? `<p class="review-explanation">${esc(item.explanation)}</p>` : ''}
    </div>
  `).join('');
}

function resetPracticeQuiz() {
  practicePaper = [];
  document.getElementById('practiceQuizResults').classList.add('hidden');
  document.getElementById('practiceQuizForm').classList.add('hidden');
  document.getElementById('practiceQuizHint').classList.remove('hidden');
  document.getElementById('practiceQuizProgressBar').style.width = '0%';
  hideError('practiceQuizLoadError');
}

/* Settings */
async function loadSettings() {
  try {
    const res  = await fetch('/api/settings');
    const data = await res.json();
    if (data.status !== 'ok') return;
    applyTheme(data.theme || 'dark');
    document.getElementById('themeToggle').checked = (data.theme === 'dark');
    const diff = document.getElementById('difficultySelect');
    if (diff) diff.value = data.difficulty || 'medium';
  } catch (_) {}

  try {
    const res  = await fetch('/api/profile');
    const data = await res.json();
    if (data.status === 'ok' && data.name) {
      document.getElementById('settingsName').value = data.name;
    }
  } catch (_) {}
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
}

function toggleTheme(checkbox) {
  applyTheme(checkbox.checked ? 'dark' : 'light');
  saveSettings();
}

async function saveName() {
  const name = document.getElementById('settingsName').value.trim();
  if (!name) return;
  try {
    const res  = await fetch('/api/profile', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name }) });
    const data = await res.json();
    if (data.status === 'ok') {
      flashOk('nameStatus');
      if (currentUser) {
        currentUser.name = name;
        applyUserToUI();
        document.getElementById('welcomeHeading').textContent = `Welcome back, ${name}!`;
      }
    }
  } catch (_) {}
}

async function saveSettings() {
  const theme      = document.getElementById('themeToggle').checked ? 'dark' : 'light';
  const difficulty = document.getElementById('difficultySelect').value;
  try {
    await fetch('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ theme, difficulty }) });
    flashOk('settingsStatus');
  } catch (_) {}
}

/* Init */
async function init() {
  try {
    const res  = await fetch('/api/me');
    const data = await res.json();
    if (data.email) {
      currentUser = { email: data.email, name: data.name || data.email };
      applyUserToUI();
      document.getElementById('auth-overlay').style.display = 'none';
      document.getElementById('app').classList.remove('hidden');
      applyStoredSidebarLayout();
      showPage('dashboard');
      loadDashboard();
      loadSettings();
    }
  } catch (_) {}
}

// Settings tab: load when navigating to it
document.getElementById('nav-settings').addEventListener('click', loadSettings);

window.addEventListener('resize', () => {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const app = document.getElementById('app');
  const drawerLayout = window.matchMedia('(max-width: 1024px)').matches;
  if (!drawerLayout) {
    if (sidebar) sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
  }
  if (!window.matchMedia('(min-width: 1025px)').matches) {
    app?.classList.remove('sidebar-collapsed');
  } else {
    try {
      if (localStorage.getItem('synapSidebarCollapsed') === '1') {
        app?.classList.add('sidebar-collapsed');
      }
    } catch (_) {}
  }
  syncSidebarPinUi();
  syncMobileMenuButton();
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const sidebar = document.getElementById('sidebar');
  if (!sidebar || !sidebar.classList.contains('open')) return;
  sidebar.classList.remove('open');
  document.getElementById('sidebarBackdrop')?.classList.remove('open');
  syncMobileMenuButton();
  syncSidebarPinUi();
  document.getElementById('mobileMenuBtn')?.focus();
});

init();


