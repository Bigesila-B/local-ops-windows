'use strict';
/* ============================================================
   launchpad.js — 启动台：应用卡片 / 拖拽排序 / 端口诊断 / 启动诊断
   ============================================================ */
import { $, el, setText, setChildren, icon, iconBtn, escapeHtml,
  post, act, toast, openLayer, closeLayer, reconcile,
  state, findApp, fmtUptime, fmtDuration } from './core.js';
import { openConfirm, confirmKill, openAppModal, openLogs, getIconVer } from './overlays.js';

const svcGrid = $('#svcGrid'), taskGrid = $('#taskGrid');

/* ---------------- 图标取色光晕 ---------------- */
function hueFromString(s) {
  let h = 0;
  for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % 360;
}
function fallbackGlow(id) { return 'hsl(' + hueFromString(id) + ' 75% 60%)'; }
/* 8x8 缩样后按透明度加权取平均色；跨域/解码失败静默回退 */
function glowFromImage(img, cb) {
  const compute = () => {
    try {
      const cv = document.createElement('canvas');
      cv.width = cv.height = 8;
      const cx = cv.getContext('2d', { willReadFrequently: true });
      cx.drawImage(img, 0, 0, 8, 8);
      const d = cx.getImageData(0, 0, 8, 8).data;
      let r = 0, g = 0, b = 0, w = 0;
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3] / 255;
        if (a > 0.2) { r += d[i] * a; g += d[i + 1] * a; b += d[i + 2] * a; w += a; }
      }
      if (!w) return cb(null);
      cb('rgb(' + Math.round(r / w) + ' ' + Math.round(g / w) + ' ' + Math.round(b / w) + ')');
    } catch (e) { cb(null); }
  };
  if (img.complete && img.naturalWidth) compute();
  else img.addEventListener('load', compute, { once: true });
}
function updateCardGlow(card, app) {
  const key = app.icon || app.favicon || ('id:' + app.id);
  if (card._glowKey === key) return;
  card._glowKey = key;
  if (app.icon || app.favicon) {
    glowFromImage(card._r.iconImg, c => {
      if (card._glowKey === key) card.style.setProperty('--glow', c || fallbackGlow(app.id));
    });
  } else {
    card.style.setProperty('--glow', fallbackGlow(app.id));
  }
}

const FAVICON_RETRY_DELAYS = [5000, 15000, 60000];
function maybeFetchFavicon(card, app) {
  if (app.icon || app.glyph || app.favicon || !app.running
      || (app.port && app.listening === false)) {
    if (app.favicon) card._favFetch = null;
    return;
  }
  const port = app.port || (app.ports && app.ports[0]);
  if (!port) return;
  const signature = String(app.pid || app.lastPid || port);
  if (!card._favFetch || card._favFetch.signature !== signature) {
    card._favFetch = { signature, attempts: 0, nextAt: 0, inFlight: false };
  }
  const attempt = card._favFetch;
  if (attempt.inFlight || attempt.attempts >= FAVICON_RETRY_DELAYS.length
      || Date.now() < attempt.nextAt) return;
  attempt.inFlight = true;
  attempt.attempts += 1;
  post('/api/apps/' + app.id + '/favicon', {})
    .then(result => {
      if (result && result.ok) window.__poll();
      else attempt.nextAt = Date.now() + FAVICON_RETRY_DELAYS[attempt.attempts - 1];
    })
    .catch(() => {
      attempt.nextAt = Date.now() + FAVICON_RETRY_DELAYS[attempt.attempts - 1];
    })
    .finally(() => { attempt.inFlight = false; });
}

function createAppCard() {
  const card = el('article', 'app-card');
  card.addEventListener('pointerdown', cardPointerDown);

  const head = el('div', 'app-head');
  const iconBox = el('div', 'app-icon');
  const iconImg = new Image();
  iconImg.alt = '';
  iconImg.hidden = true;
  iconImg.addEventListener('error', () => {
    iconImg._failedSrc = iconImg.getAttribute('src') || '';
    iconImg.hidden = true;
    iconGlyph.hidden = true;
    iconTxt.hidden = false;
    const app = findApp(card.dataset.key);
    setText(iconTxt, app && app.name ? [...app.name][0].toUpperCase() : '?');
  });
  iconImg.addEventListener('load', () => { iconImg._failedSrc = ''; });
  const iconGlyph = el('span', 'app-icon-glyph');
  iconGlyph.hidden = true;
  const iconTxt = el('span', 'app-icon-letter');
  iconBox.append(iconImg, iconGlyph, iconTxt);

  const meta = el('div', 'app-meta');
  const name = el('div', 'app-name');
  const status = el('div', 'app-status');
  const dot = el('span', 'status-dot');
  const stText = el('span', 'st-text');
  const stPort = el('button', 'st-port');
  stPort.type = 'button';
  const stUp = el('span', 'st-up');
  status.append(dot, stText, stPort, stUp);
  const taskHistory = el('div', 'task-history');
  taskHistory.hidden = true;
  meta.append(name, status, taskHistory);
  head.append(iconBox, meta);

  const cmd = el('div', 'app-cmd');

  const actions = el('div', 'app-actions');
  const primary = el('button', 'btn app-primary');
  primary.type = 'button';
  const sub = el('div', 'app-sub-actions');
  const bCopy = iconBtn('copy', '复制链接');
  const bLogs = iconBtn('file-text', '日志');
  const bDiag = iconBtn('activity', '启动诊断');
  bDiag.hidden = true;
  const bRestart = iconBtn('refresh-cw', '重启应用');
  bRestart.hidden = true;
  const bEdit = iconBtn('pencil', '编辑');
  const bDel = iconBtn('trash-2', '删除', 'danger');
  sub.append(bCopy, bLogs, bDiag, bRestart, bEdit, bDel);
  actions.append(primary, sub);

  card.append(head, cmd, actions);
  card._r = { iconBox, iconImg, iconGlyph, iconTxt, name, status, dot,
    stText, stPort, stUp, taskHistory, cmd, primary, copy: bCopy, logs: bLogs,
    diag: bDiag, restart: bRestart, edit: bEdit, del: bDel };

  const id = () => card.dataset.key;
  primary.addEventListener('click', () => toggleApp(id(), primary));
  bCopy.addEventListener('click', async () => {
    const a = findApp(id());
    const p = a && (a.port || (a.ports && a.ports[0]));
    if (!p) return;
    const url = 'http://127.0.0.1:' + p;
    try {
      await navigator.clipboard.writeText(url);
      toast('已复制 ' + url);
    } catch (e) {
      toast('复制失败：' + e.message);
    }
  });
  stPort.addEventListener('click', () => {
    const a = findApp(id());
    const p = a && (a.port || (a.ports && a.ports[0]));
    if (a && (a.portConflict || a.portOccupied)) {
      openPortDiagnostic(a);
      return;
    }
    /* listening 是新后端字段；旧进程热加载前会缺失，缺失时保持兼容。 */
    if (a && a.running && (!a.port || a.listening !== false) && p) {
      window.open('http://127.0.0.1:' + p, '_blank', 'noopener,noreferrer');
    }
  });
  bLogs.addEventListener('click', () => { const a = findApp(id()); if (a) openLogs(a); });
  bDiag.addEventListener('click', () => { const a = findApp(id()); if (a) openAppDiagnosis(a); });
  bRestart.addEventListener('click', () => {
    const a = findApp(id());
    if (a) confirmRestartApp(a);
  });
  bEdit.addEventListener('click', () => { const a = findApp(id()); if (a) openAppModal(a); });
  bDel.addEventListener('click', () => { const a = findApp(id()); if (a) confirmDeleteApp(a); });
  return card;
}

/* 主按钮：服务 = 启动/停止；批处理 = 运行/停止（运行中红色可中止） */
function setPrimary(btn, running, kind) {
  const sig = running + '|' + kind;
  if (btn._sig === sig) return;
  btn._sig = sig;
  const label = running ? '停止' : (kind === 'task' ? '运行' : '启动');
  setChildren(btn, icon(running ? 'square' : 'play', 13));
  btn.appendChild(document.createTextNode(label));
  btn.classList.toggle('btn-stop', running);
  btn.classList.toggle('btn-accent', !running);
}

function updateAppCard(card, app) {
  const r = card._r;
  /* 图标优先级：上传图片 > glyph（Lucide）> 站点 favicon（自动抓取）> 名称首字 */
  const v = getIconVer(app.id);
  if (app.icon) {
    r.iconImg.classList.remove('fav');
    const src = app.icon + (v ? '?v=' + v : '');
    if (r.iconImg.getAttribute('src') !== src) {
      r.iconImg._failedSrc = '';
      r.iconImg.src = src;
    }
    const failed = r.iconImg._failedSrc === src;
    r.iconImg.hidden = failed;
    r.iconGlyph.hidden = true;
    r.iconTxt.hidden = !failed;
    if (failed) setText(r.iconTxt, app.name ? [...app.name][0].toUpperCase() : '?');
  } else if (app.glyph && window.LUCIDE && window.LUCIDE[app.glyph]) {
    if (r._glyph !== app.glyph) {
      r._glyph = app.glyph;
      setChildren(r.iconGlyph, icon(app.glyph, 22));
    }
    r.iconGlyph.hidden = false;
    r.iconImg.hidden = true;
    r.iconTxt.hidden = true;
  } else if (app.favicon) {
    r.iconImg.classList.add('fav');
    if (r.iconImg.getAttribute('src') !== app.favicon) {
      r.iconImg._failedSrc = '';
      r.iconImg.src = app.favicon;
    }
    const failed = r.iconImg._failedSrc === app.favicon;
    r.iconImg.hidden = failed;
    r.iconGlyph.hidden = true;
    r.iconTxt.hidden = !failed;
    if (failed) setText(r.iconTxt, app.name ? [...app.name][0].toUpperCase() : '?');
  } else {
    r._glyph = null;
    r.iconImg.hidden = true;
    r.iconGlyph.hidden = true;
    r.iconTxt.hidden = false;
    setText(r.iconTxt, app.name ? [...app.name][0].toUpperCase() : '?');
  }
  setText(r.name, app.name || '');
  r.name.title = app.name || '';
  setText(r.cmd, app.command || '');
  r.cmd.title = app.command || '';
  /* 状态副行：运行态、端口冲突，以及服务/任务上次退出结果。 */
  const kind = app.kind || 'service';
  const isTask = kind === 'task';
  const taskCompleted = isTask && !app.running && !!app.lastExit;
  const taskFailed = taskCompleted && app.lastExit.code !== 0;
  r.dot.classList.toggle('running', !!app.running);
  r.dot.classList.toggle('success', taskCompleted && !taskFailed);
  r.dot.classList.toggle('danger', taskFailed);
  let stTxt = app.running ? '运行中' : (app.port ? '已停止' : '未运行');
  let stFail = false;
  let taskHistoryText = '';
  if (app.portConflict) {
    stTxt = '配置冲突';
    stFail = true;
  } else if (app.portOccupied) {
    stTxt = '端口被占用';
    stFail = true;
  } else if (app.running && app.port && app.listening === false) {
    stTxt = '等待端口';
  } else if (!app.running && app.lastExit) {
    const ok = app.lastExit.code === 0;
    stFail = !ok;
    const ago = fmtUptime(Date.now() / 1000 - app.lastExit.at);
    const agoText = ago === '刚刚' ? ago : ago + '前';
    const what = app.port
      ? (ok ? '服务已退出'
        : (app.lastExit.code < 0 ? '服务被终止' : '启动失败 exit ' + app.lastExit.code))
      : (ok ? '上次成功'
        : (app.lastExit.code < 0 ? '上次被终止' : '上次失败 exit ' + app.lastExit.code));
    if (isTask) {
      stTxt = what;
      const duration = fmtDuration(app.lastExit.durationSec);
      taskHistoryText = agoText + (duration ? ' · 用时 ' + duration : '');
    } else {
      stTxt = what + ' · ' + agoText;
    }
  }
  setText(r.stText, stTxt);
  r.stText.classList.toggle('fail', stFail);
  setText(r.taskHistory, taskHistoryText);
  r.taskHistory.hidden = !taskHistoryText;
  r.taskHistory.title = taskHistoryText;
  r.status.title = taskHistoryText ? stTxt + ' · ' + taskHistoryText : stTxt;
  /* 端口：配置 port 优先，否则用进程树检测到的实际监听端口（批处理拉起服务的场景） */
  const effPorts = app.port ? [app.port] : (app.ports || []);
  const effPort = effPorts.length ? effPorts[0] : null;
  r.copy.hidden = !effPort;
  if (effPort) {
    r.stPort.hidden = false;
    setText(r.stPort, ':' + effPort + (effPorts.length > 1 ? ' +' + (effPorts.length - 1) : ''));
    const openable = !!app.running && (!app.port || app.listening !== false);
    const diagnostic = !!app.portConflict || !!app.portOccupied;
    r.stPort.classList.toggle('clickable', openable && !diagnostic);
    r.stPort.classList.toggle('diagnostic', diagnostic);
    r.stPort.title = app.portConflict
      ? '与“' + (app.portConflictApps || []).join('、') + '”重复，请编辑端口'
      : app.portOccupied
        ? '端口被 PID ' + (app.portOccupiedPid || '?') + ' 占用'
        : openable
      ? '打开 http://127.0.0.1:' + effPort + (effPorts.length > 1 ? '（全部: ' + effPorts.join(', ') + '）' : '')
      : '端口 ' + effPort;
    r.stPort.setAttribute('aria-label', diagnostic
      ? '诊断 ' + (app.name || '应用') + ' 的端口 ' + effPort
      : openable
        ? '打开 ' + (app.name || '应用') + '，端口 ' + effPort
        : (app.name || '应用') + ' 的端口 ' + effPort);
  } else {
    r.stPort.hidden = true;
    r.stPort.removeAttribute('aria-label');
  }
  if (app.running) {
    r.stUp.hidden = false;
    setText(r.stUp, isTask ? fmtDuration(app.uptimeSec) : fmtUptime(app.uptimeSec));
  } else {
    r.stUp.hidden = true;
    setText(r.stUp, '');
  }
  setPrimary(r.primary, !!app.running, kind);
  const appName = app.name || (isTask ? '任务' : '应用');
  const primaryVerb = app.running ? '停止' : (isTask ? '运行' : '启动');
  r.primary.setAttribute('aria-label', primaryVerb + ' ' + appName);
  r.copy.setAttribute('aria-label', '复制 ' + appName + ' 的链接');
  r.logs.setAttribute('aria-label', (taskFailed ? '查看失败日志：' : '查看日志：') + appName);
  r.diag.setAttribute('aria-label', '诊断启动失败：' + appName);
  r.restart.setAttribute('aria-label', '重启 ' + appName);
  r.edit.setAttribute('aria-label', '编辑 ' + appName);
  r.del.setAttribute('aria-label', '删除 ' + appName);
  card.setAttribute('aria-label', appName + '，' + stTxt);
  r.restart.hidden = !app.running || kind !== 'service';
  const blocked = !app.running && (!!app.portConflict || !!app.portOccupied);
  r.primary.disabled = blocked;
  r.primary.title = app.portConflict
    ? '端口配置重复，请先编辑其中一项'
    : app.portOccupied ? '端口已被其他进程占用' : '';
  const launchFailed = !app.running && !!app.lastExit && app.lastExit.code !== 0;
  card.classList.toggle('running', !!app.running);
  card.classList.toggle('has-error', !!app.portConflict || !!app.portOccupied || launchFailed);
  r.diag.hidden = !launchFailed;
  updateCardGlow(card, app);
  r.logs.classList.toggle('attention', taskFailed);
  r.logs.title = taskFailed ? '查看失败日志' : '日志';
  maybeFetchFavicon(card, app);
}

async function toggleApp(id, button) {
  const app = findApp(id);
  if (!app) return;
  const isTask = (app.kind || 'service') === 'task';
  if (button && button.dataset.busy === 'true') return;
  if (!app.running && app.portConflict) {
    toast('端口配置重复，请先编辑其中一项');
    return;
  }
  if (!app.running && app.portOccupied) {
    toast('端口已被 PID ' + (app.portOccupiedPid || '?') + ' 占用');
    return;
  }
  const starting = !app.running;
  if (button) {
    button.dataset.busy = 'true';
    button.disabled = true;
  }
  const targetName = app.name || (isTask ? '任务' : '应用');
  toast(starting
    ? (isTask ? '正在运行 ' : '正在启动 ') + targetName + '…'
    : '正在停止 ' + targetName + '…');
  try {
    const result = await act(post('/api/apps/' + id + '/' + (starting ? 'start' : 'stop')));
    if (result && result.ok !== false) {
      if (starting) {
        toast(isTask
          ? targetName + '已开始运行'
          : '启动命令已执行，正在等待' + (app.port ? ' :' + app.port : '服务'));
        await window.__poll();
        setTimeout(window.__poll, 700);
        setTimeout(window.__poll, 1800);
      } else {
        await window.__poll();
        toast('已停止 ' + targetName);
      }
    } else {
      await window.__poll();
    }
  } finally {
    if (button) {
      delete button.dataset.busy;
      const latest = findApp(id);
      button.disabled = !!(latest && !latest.running &&
        (latest.portConflict || latest.portOccupied));
    }
  }
}
export { toggleApp };

function confirmRestartApp(app) {
  openConfirm({
    title: '重启应用',
    bodyHtml: '确定要重启 <b>' + escapeHtml(app.name || '') + '</b> 吗？' +
      '<div class="confirm-detail">总控台会等待旧进程完全退出，然后使用当前配置重新启动。</div>',
    okText: '重新启动',
    onOk: async () => {
      const r = await act(post('/api/apps/' + app.id + '/restart'));
      if (r && r.ok !== false) toast('已重启 ' + (app.name || '应用'));
      window.__poll();
    },
  });
}

function confirmDeleteApp(app) {
  openConfirm({
    title: '删除应用',
    bodyHtml: '确定要删除 <b>' + escapeHtml(app.name || '') + '</b> 吗？' +
      '<div class="confirm-detail">将先停止该应用，并删除其图标与日志。</div>',
    okText: '删除',
    onOk: async () => {
      await act(del('/api/apps/' + app.id));
      window.__poll();
    },
  });
}

/* ---------------- 端口诊断模态 ---------------- */
const portDiagMask = $('#portDiagMask'), portDiagTitle = $('#portDiagTitle');
const diagDot = $('#diagDot'), diagSummary = $('#diagSummary'), diagPort = $('#diagPort');
const diagPidRow = $('#diagPidRow'), diagPid = $('#diagPid');
const diagNameRow = $('#diagNameRow'), diagName = $('#diagName');
const diagAppRow = $('#diagAppRow'), diagApp = $('#diagApp');
const diagUptimeRow = $('#diagUptimeRow'), diagUptime = $('#diagUptime');
const diagCwdRow = $('#diagCwdRow'), diagCwd = $('#diagCwd');
const diagCmdRow = $('#diagCmdRow'), diagCmd = $('#diagCmd');
const diagNote = $('#diagNote'), diagCopy = $('#diagCopy');
const diagKill = $('#diagKill'), diagClose = $('#diagClose');

let diagCurrentApp = null;

function setDiagRow(row, node, value) {
  const present = value !== null && value !== undefined && value !== '';
  row.hidden = !present;
  if (present) setText(node, String(value));
}

function openPortDiagnostic(app) {
  diagCurrentApp = app;
  const owner = app.portOwner || null;
  const conflict = !!app.portConflict;
  const occupied = !!app.portOccupied;
  portDiagTitle.textContent = '端口 ' + (app.port || '--') + ' 诊断';
  setText(diagPort, app.port ? ':' + app.port : '--');
  diagDot.classList.toggle('danger', conflict || occupied);
  setText(diagSummary, conflict ? '启动台配置重复'
    : occupied ? '端口被其他进程占用' : '端口状态正常');

  setDiagRow(diagPidRow, diagPid, owner && owner.pid);
  setDiagRow(diagNameRow, diagName, owner && owner.name);
  setDiagRow(diagAppRow, diagApp, owner && owner.appName);
  setDiagRow(diagUptimeRow, diagUptime,
    owner && owner.uptimeSec != null ? fmtUptime(owner.uptimeSec) : null);
  setDiagRow(diagCwdRow, diagCwd, owner && owner.cwd);
  setDiagRow(diagCmdRow, diagCmd, owner && owner.cmd);

  if (conflict) {
    diagNote.textContent = '同一端口还被“' +
      (app.portConflictApps || []).join('、') + '”配置。请编辑或删除其中一项。';
  } else if (owner && owner.pid === (state.data && state.data.consolePid)) {
    diagNote.textContent = '该端口属于当前总控台，不能在这里结束。';
  } else if (owner && owner.currentUser) {
    diagNote.textContent = owner.appId
      ? '占用者是另一个受管应用，可先停止它再启动当前应用。'
      : '该进程属于当前用户，可在确认后结束它。';
  } else if (owner) {
    diagNote.textContent = '该进程不属于当前用户，总控台只提供信息，不会结束它。';
  } else {
    diagNote.textContent = '暂时无法读取占用者详情，可稍后刷新重试。';
  }
  diagKill.hidden = !(occupied && owner && owner.currentUser
    && owner.pid !== (state.data && state.data.consolePid));
  diagKill.textContent = owner && owner.appId ? '停止占用应用' : '结束占用进程';
  openLayer(portDiagMask, diagClose);
}

function closePortDiagnostic() {
  closeLayer(portDiagMask);
  diagCurrentApp = null;
}
export { closePortDiagnostic };

diagClose.addEventListener('click', closePortDiagnostic);
portDiagMask.addEventListener('mousedown', e => {
  if (e.target === portDiagMask) closePortDiagnostic();
});
diagCopy.addEventListener('click', async () => {
  const app = diagCurrentApp;
  if (!app) return;
  const owner = app.portOwner || {};
  const lines = [
    '端口: ' + (app.port || '--'),
    owner.pid ? 'PID: ' + owner.pid : '',
    owner.name ? '程序: ' + owner.name : '',
    owner.cwd ? '目录: ' + owner.cwd : '',
    owner.cmd ? '命令: ' + owner.cmd : '',
    app.portConflict ? '配置冲突: ' + (app.portConflictApps || []).join('、') : '',
  ].filter(Boolean).join('\n');
  try {
    await navigator.clipboard.writeText(lines);
    toast('已复制端口诊断信息');
  } catch (e) {
    toast('复制失败：' + e.message);
  }
});
diagKill.addEventListener('click', () => {
  const app = diagCurrentApp;
  const owner = app && app.portOwner;
  if (!owner) return;
  closePortDiagnostic();
  openConfirm({
    title: owner.appId ? '停止占用应用' : '结束占用进程',
    bodyHtml: '确定要释放端口 <b>:' + escapeHtml(app.port) + '</b> 吗？' +
      '<div class="confirm-detail mono">PID ' + escapeHtml(owner.pid) +
      ' · ' + escapeHtml(owner.name || '') + '</div>',
    okText: owner.appId ? '停止应用' : '结束进程',
    onOk: async () => {
      if (owner.appId) await act(post('/api/apps/' + owner.appId + '/stop'));
      else await act(post('/api/kill', { pid: owner.pid, force: false }));
      window.__poll();
    },
  });
});

/* ---------------- 启动诊断模态 ---------------- */
const appDiagMask = $('#appDiagMask'), appDiagList = $('#appDiagList');
const appDiagTitle = $('#appDiagTitle');
const appDiagSummary = $('#appDiagSummary'), appDiagLogs = $('#appDiagLogs');
const appDiagClose = $('#appDiagClose');
let appDiagApp = null;
let appDiagRequestSeq = 0;

async function openAppDiagnosis(app) {
  const requestSeq = ++appDiagRequestSeq;
  appDiagApp = app;
  setText(appDiagTitle, '启动诊断 · ' + (app.name || '应用'));
  appDiagList.replaceChildren();
  appDiagList.setAttribute('aria-busy', 'true');
  setText(appDiagSummary, '正在分析日志与配置…');
  openLayer(appDiagMask, appDiagClose);
  let r = null;
  try {
    r = await post('/api/apps/' + app.id + '/diagnose', {});
  } catch (e) {
    if (requestSeq === appDiagRequestSeq && appDiagApp && appDiagApp.id === app.id) {
      toast('诊断请求失败：' + e.message);
    }
  }
  if (requestSeq !== appDiagRequestSeq || !appDiagApp || appDiagApp.id !== app.id) return;
  appDiagList.setAttribute('aria-busy', 'false');
  if (!r || r.ok === false) {
    setText(appDiagSummary, (r && r.error) || '诊断失败，请打开日志人工排查');
    return;
  }
  appDiagList.replaceChildren();
  for (const issue of r.issues || []) {
    const box = el('div', 'appdiag-issue');
    const h = el('h4');
    h.textContent = issue.title;
    const d = el('p', 'appdiag-detail');
    d.textContent = issue.detail;
    const f = el('p', 'appdiag-fix');
    f.textContent = '修复建议：' + issue.fix;
    box.append(h, d, f);
    appDiagList.appendChild(box);
  }
  setText(appDiagSummary, r.summary || '');
}
function closeAppDiagnosis() {
  appDiagRequestSeq += 1;
  appDiagList.setAttribute('aria-busy', 'false');
  closeLayer(appDiagMask);
  appDiagApp = null;
}
export { closeAppDiagnosis };

appDiagClose.addEventListener('click', closeAppDiagnosis);
appDiagMask.addEventListener('mousedown', e => {
  if (e.target === appDiagMask) closeAppDiagnosis();
});
appDiagLogs.addEventListener('click', () => {
  const a = appDiagApp;
  closeAppDiagnosis();
  if (a) openLogs(a);
});

/* ---------------- 卡片拖拽排序（pointer 实现：滑块式跟手 + 虚线占位） ---------------- */
let drag = null;  // { card, ph, grid, dx, dy }
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* FLIP 让位动画：重排前记录视觉位置，重排后从旧位置滑到新位置。 */
function flip(grid, mutate) {
  if (reduceMotion) { mutate(); return; }
  const cards = [...grid.querySelectorAll('.app-card, .drop-placeholder')];
  const first = new Map(cards.map(c => [c, c.getBoundingClientRect()]));
  for (const c of cards) {
    clearTimeout(c._flipT);
    c.style.transition = 'none';
    c.style.transform = 'none';
  }
  mutate();
  const moved = [];
  for (const c of cards) {
    if (!c.isConnected) continue;
    const f = first.get(c), l = c.getBoundingClientRect();
    const dx = f.left - l.left, dy = f.top - l.top;
    if (dx || dy) {
      c.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
      moved.push(c);
    } else {
      c.style.transition = '';
      c.style.transform = '';
    }
  }
  if (!moved.length) return;
  requestAnimationFrame(() => {
    for (const c of moved) {
      c.style.transition = 'transform 0.2s ease-out';
      c.style.transform = '';
      c._flipT = setTimeout(() => { c.style.transition = ''; c.style.transform = ''; }, 220);
    }
  });
}

function cardPointerDown(e) {
  if (e.button !== 0 || drag) return;
  if (e.target.closest('button')) return;   // 按钮上不触发拖拽
  const card = e.currentTarget;
  const sx = e.clientX, sy = e.clientY;
  const onMove = ev => {
    if (!drag) {
      if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 6) return;  // 点击阈值
      beginDrag(card, ev);
    }
    moveDrag(ev);
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    if (drag) endDrag();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

function beginDrag(card, e) {
  const grid = card.parentNode;
  const rect = card.getBoundingClientRect();
  const ph = el('div', 'drop-placeholder');
  ph.style.height = rect.height + 'px';
  grid.insertBefore(ph, card);
  document.body.appendChild(card);   // 卡片脱离 grid，fixed 跟随指针
  const s = card.style;
  s.width = rect.width + 'px';
  s.height = rect.height + 'px';
  s.position = 'fixed';
  s.left = '0';
  s.top = '0';
  s.margin = '0';
  s.zIndex = '200';
  s.pointerEvents = 'none';          // 穿透，便于 elementFromPoint 找目标
  card.classList.add('lifted');
  document.body.classList.add('dragging-on');
  drag = { card, ph, grid, dx: e.clientX - rect.left, dy: e.clientY - rect.top };
  moveDrag(e);
}

function moveDrag(e) {
  const d = drag;
  d.card.style.transform =
    'translate(' + (e.clientX - d.dx) + 'px,' + (e.clientY - d.dy) + 'px)';
  const hit = document.elementFromPoint(e.clientX, e.clientY);
  const over = hit && hit.closest('.app-card');
  if (over && d.grid.contains(over) && !over.classList.contains('add-card')) {
    /* 用布局坐标（offsetLeft，不含 FLIP transform）判定插入侧，
       避免让位动画中的视觉位置抖动导致占位框来回振荡 */
    const baseX = over.offsetParent.getBoundingClientRect().left;
    const midX = over.offsetLeft + over.offsetWidth / 2;
    const before = (e.clientX - baseX) < midX;
    const ref = before ? over : over.nextSibling;
    if (d.ph.nextSibling !== ref) {   // 位置没变则跳过，避免 FLIP 动画被重启
      flip(d.grid, () => d.grid.insertBefore(d.ph, ref));
    }
  } else if (over && d.grid.contains(over)) {
    if (d.ph.nextSibling !== over) {  // 添加卡上 → 末尾
      flip(d.grid, () => d.grid.insertBefore(d.ph, over));
    }
  }
}

function endDrag() {
  const d = drag;
  drag = null;
  const finish = () => {
    d.grid.insertBefore(d.card, d.ph);
    d.ph.remove();
    const s = d.card.style;
    s.position = s.left = s.top = s.width = s.height = s.margin =
      s.zIndex = s.transform = s.transition = s.pointerEvents = '';
    d.card.classList.remove('lifted');
    document.body.classList.remove('dragging-on');
    const ids = [...d.grid.querySelectorAll('.app-card:not(.add-card)')]
      .map(c => c.dataset.key);
    post('/api/apps/reorder', { ids }).then(() => window.__poll());
  };
  if (reduceMotion) { finish(); return; }
  const t = d.ph.getBoundingClientRect();   // 滑入占位框
  d.card.style.transition = 'transform 0.18s ease-out';
  d.card.style.transform = 'translate(' + t.left + 'px,' + t.top + 'px)';
  setTimeout(finish, 180);
}

/* ---------------- 黑色 hero 卡（Candy 主题专用，其他主题 CSS 隐藏） ---------------- */
let heroEl = null;
function heroCard() {
  if (heroEl) return heroEl;
  heroEl = el('div', 'app-hero');
  const title = el('div', 'hero-title');
  title.textContent = '本地指挥中心';
  const titleEn = el('span');
  titleEn.textContent = 'LOCAL OPS';
  title.append(titleEn);
  const add = el('button', 'hero-add');
  add.type = 'button';
  add.textContent = '命令面板 ⌘K';
  add.addEventListener('click', () => window.__openPalette && window.__openPalette());
  const media = el('div', 'hero-media');
  const art = new Image();
  art.className = 'hero-art';
  art.src = '/assets/local-ops-bot.webp';
  art.alt = '';
  art.width = 1000;
  art.height = 722;
  art.decoding = 'async';
  art.loading = 'lazy';
  art.draggable = false;
  art.setAttribute('aria-hidden', 'true');
  media.append(art);
  const foot = el('div', 'hero-foot');
  foot.textContent = 'SERVICES / AUTOMATION';
  heroEl.append(title, add, media, foot);
  return heroEl;
}

export function renderLaunchpad(apps, firstRender) {
  if (drag) return;  // 拖拽中轮询不打乱 DOM
  const svcs = apps.filter(a => (a.kind || 'service') !== 'task');
  const tasks = apps.filter(a => a.kind === 'task');
  reconcile(svcGrid, svcs, a => a.id, createAppCard, updateAppCard, firstRender);
  svcGrid.appendChild($('#addSvcCard'));   // 「+ 添加服务」始终位于网格末尾
  svcGrid.prepend(heroCard());             // hero 卡固定在网格首位
  reconcile(taskGrid, tasks, a => a.id, createAppCard, updateAppCard, firstRender);
  taskGrid.appendChild($('#addTaskCard')); // 「+ 添加任务」始终位于网格末尾
}
