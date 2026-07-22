'use strict';
/* ============================================================
   core.js — 共享基础：工具 / 图标 / API / Toast / 浮层 / 状态
   每 2s 轮询 GET /api/state，按 key 原地更新 DOM，不整列表重绘
   ============================================================ */

/* ---------------- 工具 ---------------- */
export const $ = (s, r = document) => r.querySelector(s);

export function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}
/* 仅在文本变化时写 DOM，避免无谓重排 */
export function setText(node, txt) {
  if (node._t !== txt) { node._t = txt; node.textContent = txt; }
}
export function setChildren(node, ...nodes) {
  while (node.firstChild) node.removeChild(node.firstChild);
  nodes.forEach(n => node.appendChild(n));
}
/* KPI 数字变化闪烁：短暂褪为 text-3 再过渡回 text（无发光） */
export function setKpi(node, txt) {
  if (node._t === txt) return;
  node._t = txt;
  node.textContent = txt;
  node.classList.add('flash');
  void node.offsetWidth;
  node.classList.remove('flash');
}
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function shortHome(p) { return p ? p.replace(/^\/Users\/[^/]+/, '~') : ''; }
export function truncateMiddle(s, max = 34) {
  if (!s) return '';
  if (s.length <= max) return s;
  const keep = max - 1;
  return s.slice(0, Math.ceil(keep / 2)) + '…' + s.slice(-Math.floor(keep / 2));
}
/* 秒 → 刚刚 / Nm / NhNm / NdNh */
export function fmtUptime(sec) {
  if (sec == null || isNaN(sec)) return '--';
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return '刚刚';
  const m = Math.floor(sec / 60);
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60), rm = m % 60;
  if (h < 24) return rm ? h + 'h' + rm + 'm' : h + 'h';
  const d = Math.floor(h / 24), rh = h % 24;
  return rh ? d + 'd' + rh + 'h' : d + 'd';
}
/* 批处理耗时：短任务保留到 0.1 秒，长任务使用易读的中文单位。 */
export function fmtDuration(sec) {
  const value = Number(sec);
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 0.1) return '<0.1秒';
  if (value < 10) {
    const rounded = Math.round(value * 10) / 10;
    return (Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)) + '秒';
  }
  const total = Math.round(value);
  if (total < 60) return total + '秒';
  const minutes = Math.floor(total / 60), seconds = total % 60;
  if (minutes < 60) return minutes + '分' + (seconds ? pad2(seconds) + '秒' : '');
  const hours = Math.floor(minutes / 60), remainMinutes = minutes % 60;
  return hours + '小时' + (remainMinutes ? pad2(remainMinutes) + '分' : '');
}
export const pad2 = n => String(n).padStart(2, '0');
export const fmtClock = d => pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
export const fmtPct = v => (typeof v === 'number' && !isNaN(v) ? v : 0).toFixed(1) + '%';

/* ---------------- Lucide 图标 ---------------- */
export function icon(name, size = 15, sw) {
  const span = el('span', 'ic');
  let s = (window.LUCIDE && window.LUCIDE[name]) || '';
  if (!s) return span;
  /* 兼容根标签缺空格的历史生成格式 */
  if (s.indexOf('<svgxmlns=') === 0) s = s.replace('<svgxmlns=', '<svg xmlns=');
  span.innerHTML = s;
  const svg = span.querySelector('svg');
  if (svg) {
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    if (sw) svg.setAttribute('stroke-width', sw);
  }
  return span;
}
export function iconBtn(name, title, cls) {
  const b = el('button', 'ibtn' + (cls ? ' ' + cls : ''));
  b.type = 'button';
  b.title = title;
  b.setAttribute('aria-label', title);
  b.appendChild(icon(name, 15));
  return b;
}
/* 启动台图标库（编辑模态可选） */
export const GLYPHS = ['rocket', 'globe', 'terminal', 'server', 'database', 'bot',
  'gamepad-2', 'film', 'music', 'code', 'folder-git-2', 'zap',
  'container', 'cpu', 'wifi', 'hard-drive', 'package', 'wrench'];

/* ---------------- API ---------------- */
const REQUEST_TIMEOUT_MS = 12000;

async function req(method, path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const opt = { method, signal: controller.signal };
  if (body !== undefined) {
    opt.headers = { 'Content-Type': 'application/json' };
    opt.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(path, opt);
    if (r.status === 204) return { ok: true };
    const type = r.headers.get('content-type') || '';
    const fallbackError = r.status === 401 || r.status === 403
      ? '访问被拒绝，请从总控台页面重试'
      : 'HTTP ' + r.status;
    const data = type.includes('application/json')
      ? await r.json()
      : { ok: false, error: (await r.text()).trim() || fallbackError };
    if (!r.ok && (!data || data.ok !== false)) {
      return { ok: false, error: (data && data.error) || fallbackError };
    }
    return data;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('请求超时，请稍后重试');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
export const post = (p, b = {}) => req('POST', p, b);
export const put = (p, b) => req('PUT', p, b);
export const del = p => req('DELETE', p);

/* 动作请求统一错误提示 */
export async function act(p) {
  try {
    const r = await p;
    if (r && r.ok === false) toast(r.error || '操作失败');
    return r;
  } catch (e) {
    toast('请求失败：' + e.message);
    return null;
  }
}

/* ---------------- Toast ---------------- */
let toastTimer = null;
let toastSeq = 0;
export function toast(msg, duration = 3200) {
  const toastEl = $('#toast');
  const seq = ++toastSeq;
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    if (seq === toastSeq) toastEl.classList.remove('show');
  }, duration);
}

/* ---------------- 浮层焦点管理 ---------------- */
const layerReturnFocus = new WeakMap();
export function openLayer(layer, focusTarget) {
  layerReturnFocus.set(layer, document.activeElement);
  layer.inert = false;
  layer.setAttribute('aria-hidden', 'false');
  layer.classList.add('open');
  setTimeout(() => {
    const target = typeof focusTarget === 'function' ? focusTarget() : focusTarget;
    if (target) target.focus();
  }, 40);
}
export function closeLayer(layer) {
  if (!layer.classList.contains('open')) return;
  layer.classList.remove('open');
  layer.setAttribute('aria-hidden', 'true');
  layer.inert = true;
  const target = layerReturnFocus.get(layer);
  layerReturnFocus.delete(layer);
  if (target && target.isConnected) setTimeout(() => target.focus(), 0);
}
const LAYER_IDS = ['#confirmMask', '#themeMask', '#portDiagMask',
  '#appDiagMask', '#appModalMask', '#paletteMask', '#logDrawer'];
export function activeLayer() {
  for (const id of LAYER_IDS) {
    const layer = $(id);
    if (layer && layer.classList.contains('open')) return layer;
  }
  return null;
}
export function trapLayerFocus(e) {
  const layer = activeLayer();
  if (!layer || e.key !== 'Tab') return;
  const nodes = [...layer.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
    .filter(node => !node.hidden && node.offsetParent !== null);
  if (!nodes.length) { e.preventDefault(); return; }
  const first = nodes[0], last = nodes[nodes.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}

/* ---------------- 按 key 原地更新的 reconcile ---------------- */
export function reconcile(container, items, getKey, createFn, updateFn, stagger) {
  const old = new Map();
  for (const child of container.children) {
    if (child.dataset.key != null) old.set(child.dataset.key, child);
  }
  const seen = new Set();
  items.forEach((item, i) => {
    const key = String(getKey(item));
    seen.add(key);
    let node = old.get(key);
    if (!node) {
      node = createFn(item);
      node.dataset.key = key;
      node.classList.add('anim-in');
      if (stagger) node.style.setProperty('--d', Math.min(i * 30, 600) + 'ms');
    }
    updateFn(node, item);
    const cur = container.children[i];
    if (cur !== node) container.insertBefore(node, cur || null);
  });
  for (const [key, node] of old) if (!seen.has(key)) node.remove();
}
/* 入场动画结束后移除类，避免 fill 状态干扰 hover */
document.addEventListener('animationend', e => {
  if (e.target.classList) e.target.classList.remove('anim-in');
});

/* ---------------- 全局状态 ---------------- */
export const state = {
  view: localStorage.getItem('console-view') === 'services' ? 'services' : 'launchpad',
  data: null,
  lastUpdate: null,
  connected: true,
  restartingFrom: null,
  stopping: false,
};
export const DISCONNECTED_TEXT = '控制台连接断开，正在自动重连…';

export function findApp(id) {
  return ((state.data && state.data.apps) || []).find(a => a.id === id);
}
export function taskExitSignature(lastExit) {
  if (!lastExit) return '';
  return [lastExit.startedAt || '', lastExit.at || '', lastExit.code,
    lastExit.durationSec == null ? '' : lastExit.durationSec].join('|');
}
/* 只提醒本页打开期间新完成的任务；首次加载已有历史时保持安静。 */
export function notifyTaskCompletions(previousData, nextData) {
  if (!previousData) return;
  const previous = new Map((previousData.apps || []).map(app => [app.id, app]));
  for (const app of (nextData.apps || [])) {
    if (app.kind !== 'task' || !app.lastExit) continue;
    const before = previous.get(app.id);
    if (!before || taskExitSignature(before.lastExit) === taskExitSignature(app.lastExit)) continue;
    const duration = fmtDuration(app.lastExit.durationSec);
    const suffix = duration ? '，用时 ' + duration : '';
    if (app.lastExit.code === 0) {
      toast((app.name || '批处理任务') + '运行成功' + suffix, 5000);
    } else {
      const result = app.lastExit.code < 0
        ? '被终止' : '运行失败（exit ' + app.lastExit.code + '）';
      toast((app.name || '批处理任务') + result + suffix + '，可查看日志', 6500);
    }
  }
}

/* ---------------- 深浅色 ---------------- */
const mq = window.matchMedia('(prefers-color-scheme: dark)');
function currentTheme() {
  return localStorage.getItem('console-theme') || (mq.matches ? 'dark' : 'light');
}
export function applyTheme() {
  const themeBtn = $('#themeBtn');
  const t = currentTheme();
  document.documentElement.dataset.theme = t;
  setChildren(themeBtn, icon(t === 'dark' ? 'sun' : 'moon', 15));
  themeBtn.title = t === 'dark' ? '切换到浅色模式' : '切换到深色模式';
  themeBtn.setAttribute('aria-label', themeBtn.title);
}
export function initThemeToggle() {
  $('#themeBtn').addEventListener('click', () => {
    localStorage.setItem('console-theme', currentTheme() === 'dark' ? 'light' : 'dark');
    applyTheme();
  });
  mq.addEventListener('change', () => {
    if (!localStorage.getItem('console-theme')) applyTheme();
  });
}

/* ---------------- UI 主题（themes/ 注册表 + 配置持久化） ---------------- */
export function registeredThemes() { return (state.data && state.data.themes) || []; }
export function currentUiTheme() {
  const candidate = (state.data && state.data.uiTheme)
    || localStorage.getItem('console-ui-theme') || 'apollo';
  const themes = registeredThemes();
  if (themes.length) {
    if (themes.some(theme => theme.id === candidate)) return candidate;
    return (themes.find(theme => theme.id === 'apollo') || themes[0]).id;
  }
  return /^[a-z0-9][a-z0-9_-]{0,63}$/i.test(candidate) ? candidate : 'apollo';
}

function linkedThemeName(link) {
  const match = (link.getAttribute('href') || '').match(/\/themes\/([^/?]+)\.css/);
  return match ? decodeURIComponent(match[1]) : 'apollo';
}

function loadThemeCss(name) {
  const themeCss = $('#themeCss');
  const href = '/themes/' + name + '.css';
  const currentPath = new URL(themeCss.href, location.href).pathname;
  if (currentPath === href && themeCss.sheet) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      themeCss.removeEventListener('load', onLoad);
      themeCss.removeEventListener('error', onError);
      if (error) reject(error);
      else resolve();
    };
    const onLoad = () => finish();
    const onError = () => finish(new Error('无法加载 ' + href));
    const timer = setTimeout(() => finish(new Error('加载主题样式超时')), 8000);
    themeCss.addEventListener('load', onLoad);
    themeCss.addEventListener('error', onError);
    /* 同一路径此前若加载失败，加查询串确保浏览器真正重试。 */
    themeCss.href = href + (currentPath === href ? '?retry=' + Date.now() : '');
  });
}

function commitUiTheme(name) {
  document.documentElement.dataset.uiTheme = name;
  localStorage.setItem('console-ui-theme', name);
  if (state.data) state.data.uiTheme = name;
}

let themeChangeQueue = Promise.resolve(true);
let pendingThemeChange = null;
export function applyUiTheme(name, persist = false) {
  if (pendingThemeChange && pendingThemeChange.name === name
      && (!persist || pendingThemeChange.persist)) {
    return pendingThemeChange.promise;
  }
  const queued = themeChangeQueue.catch(() => false).then(async () => {
    const themes = registeredThemes();
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(name)
        || (themes.length && !themes.some(theme => theme.id === name))) {
      if (persist) toast('主题不存在或已被移除');
      return false;
    }
    const themeCss = $('#themeCss');
    const previous = document.documentElement.dataset.uiTheme || linkedThemeName(themeCss);
    const previousHref = themeCss.getAttribute('href') || ('/themes/' + previous + '.css');
    const previousStored = localStorage.getItem('console-ui-theme');
    const previousState = state.data && state.data.uiTheme;
    try {
      await loadThemeCss(name);
      commitUiTheme(name);
      if (persist) {
        const result = await post('/api/ui/theme', { theme: name });
        if (!result || result.ok === false) {
          throw new Error((result && result.error) || '主题设置未能保存');
        }
      }
      return true;
    } catch (e) {
      themeCss.href = previousHref;
      document.documentElement.dataset.uiTheme = previous;
      if (previousStored == null) localStorage.removeItem('console-ui-theme');
      else localStorage.setItem('console-ui-theme', previousStored);
      if (state.data) state.data.uiTheme = previousState || previous;
      toast('切换主题失败：' + e.message);
      return false;
    }
  });
  themeChangeQueue = queued;
  pendingThemeChange = { name, persist, promise: queued };
  queued.finally(() => {
    if (pendingThemeChange && pendingThemeChange.promise === queued) pendingThemeChange = null;
  });
  return queued;
}
export function openThemePicker() {
  renderThemeGrid();
  openLayer($('#themeMask'), $('#themeMaskClose'));
}
export function closeThemePicker() { closeLayer($('#themeMask')); }
function renderThemeGrid() {
  const themeGrid = $('#themeGrid');
  themeGrid.replaceChildren();
  for (const t of registeredThemes()) {
    const card = el('button', 'theme-card' + (t.id === currentUiTheme() ? ' sel' : ''));
    card.type = 'button';
    card.setAttribute('aria-pressed', String(t.id === currentUiTheme()));
    const dots = el('span', 'theme-dots');
    for (const c of t.colors || []) {
      const d = el('i');
      d.style.background = c;
      dots.appendChild(d);
    }
    const name = el('span', 'theme-name');
    name.textContent = t.name;
    const author = el('span', 'theme-author');
    author.textContent = t.author ? 'by ' + t.author : '';
    const desc = el('span', 'theme-desc');
    desc.textContent = t.desc || '';
    card.append(dots, name, author, desc);
    card.addEventListener('click', async () => {
      card.disabled = true;
      const changed = await applyUiTheme(t.id, true);
      card.disabled = false;
      if (!changed) return;
      closeThemePicker();
      toast('已切换到 ' + t.name);
    });
    themeGrid.appendChild(card);
  }
}
export function initThemePicker() {
  const uiThemeBtn = $('#uiThemeBtn');
  setChildren(uiThemeBtn, icon('sliders-horizontal', 15));
  uiThemeBtn.title = '选择主题';
  uiThemeBtn.setAttribute('aria-label', '选择主题');
  uiThemeBtn.addEventListener('click', openThemePicker);
  $('#themeMaskClose').addEventListener('click', closeThemePicker);
  $('#themeMask').addEventListener('mousedown', e => {
    if (e.target === $('#themeMask')) closeThemePicker();
  });
}
