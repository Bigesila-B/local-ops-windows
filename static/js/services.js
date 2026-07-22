'use strict';
/* ============================================================
   services.js — 服务监控：表格行 / 关注进程 / 折叠分区
   ============================================================ */
import { $, el, setText, setChildren, setKpi, icon, iconBtn,
  post, act, reconcile, state, truncateMiddle, shortHome,
  fmtUptime, fmtPct, fmtClock } from './core.js';
import { confirmKill, openAppModal } from './overlays.js';

const mineList = $('#mineList'), mineEmpty = $('#mineEmpty');
const bgHeader = $('#bgHeader'), bgBody = $('#bgBody'), bgList = $('#bgList');
const bgEmpty = $('#bgEmpty'), bgCount = $('#bgCount');
const hiddenPanel = $('#hiddenPanel'), hiddenHeader = $('#hiddenHeader');
const hiddenBody = $('#hiddenBody'), hiddenList = $('#hiddenList'), hiddenCount = $('#hiddenCount');
const watchChips = $('#watchChips'), watchInput = $('#watchInput');
const watchList = $('#watchList'), watchEmpty = $('#watchEmpty'), watchEmptyText = $('#watchEmptyText');
const statMine = $('#statMine'), statBg = $('#statBg'), statTime = $('#statTime');
const statCpu = $('#statCpu'), statMem = $('#statMem'), statWarn = $('#statWarn');

function findSvc(key) { return ((state.data && state.data.services) || []).find(s => s.key === key); }
function findWatch(key) { return ((state.data && state.data.watched) || []).find(w => String(w.pid) === key); }

/* pinned 在前，其余按端口升序 */
function svcSort(a, b) {
  if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
  const pa = a.port == null ? Infinity : a.port;
  const pb = b.port == null ? Infinity : b.port;
  if (pa !== pb) return pa - pb;
  return (a.name || '').localeCompare(b.name || '');
}

/* ---------------- 负载单元格（CPU / MEM 纯文本） ---------------- */
function loadCell() {
  const wrap = el('span', 'c-load');
  const mk = lab => {
    const s = el('span', 'ld');
    const l = el('span', 'ld-lab');
    l.textContent = lab;
    const v = el('span', 'ld-v');
    s.append(l, v);
    return { s, v };
  };
  const cpu = mk('CPU'), mem = mk('MEM');
  wrap.append(cpu.s, mem.s);
  return { wrap, cpu: cpu.v, mem: mem.v };
}

function createServiceRow(kind) {
  const row = el('div', 'tr');

  const cDot = el('span', 'c-dot');
  /* 运行绿，后台灰 */
  const dot = el('span', 'status-dot' + (kind === 'background' ? '' : ' running'));
  cDot.append(dot);

  const cTitle = el('span', 'c-title');
  const tMain = el('span', 't-main');
  const mark = el('span', 'app-mark');
  mark.title = '来自启动台';
  mark.hidden = true;
  mark.appendChild(icon('link-2', 12));
  const name = el('span', 't-name');
  tMain.append(mark, name);
  const sub = el('span', 't-sub');
  cTitle.append(tMain, sub);

  const cPort = el('button', 'c-port');
  cPort.type = 'button';
  const cCwd = el('span', 'c-cwd');
  const load = loadCell();
  const cUp = el('span', 'c-up');
  const cAct = el('span', 'c-act');
  const cmdRow = el('div', 'tr-cmd');
  const cmdCode = el('code');
  cmdRow.append(cmdCode);

  row.append(cDot, cTitle, cPort, cCwd, load.wrap, cUp, cAct, cmdRow);
  row._r = { name, mark, sub, port: cPort, cwd: cCwd, ldCpu: load.cpu, ldMem: load.mem, up: cUp, cmdCode };

  const key = () => row.dataset.key;
  cPort.addEventListener('click', () => {
    const s = findSvc(key());
    if (s && s.port) window.open('http://127.0.0.1:' + s.port, '_blank', 'noopener,noreferrer');
  });

  const flag = async (f, value) => {
    await act(post('/api/services/flag', { key: key(), flag: f, value }));
    window.__poll();
  };

  if (kind === 'mine') {
    const bAdd = iconBtn('plus', '添加到启动台');
    bAdd.addEventListener('click', () => {
      const s = findSvc(key());
      if (s) openAppModal({
        name: s.appName || s.project || s.name || '',
        command: s.cmd || '',
        cwd: s.cwd || null,
        port: s.port != null ? s.port : null,
      });
    });
    const bDemote = iconBtn('download', '移回应用后台');
    bDemote.hidden = true;   // 仅被提升(promoted)的服务显示
    bDemote.addEventListener('click', () => flag('promoted', false));
    row._r.demote = bDemote;
    const bCmd = iconBtn('terminal', '展开完整命令');
    bCmd.setAttribute('aria-expanded', 'false');
    bCmd.addEventListener('click', () => {
      row.classList.toggle('expanded');
      bCmd.classList.toggle('active', row.classList.contains('expanded'));
      bCmd.setAttribute('aria-expanded', String(row.classList.contains('expanded')));
    });
    row._r.pin = iconBtn('pin-off', '置顶 / 取消置顶');
    row._r.pin.addEventListener('click', async () => {
      const s = findSvc(key());
      if (s) flag('pinned', !s.pinned);
    });
    const bHide = iconBtn('eye-off', '移入已隐藏列表');
    bHide.addEventListener('click', () => flag('hidden', true));
    const bKill = iconBtn('power', '结束进程', 'danger');
    bKill.addEventListener('click', () => {
      const s = findSvc(key());
      if (s) confirmKill(s);
    });
    cAct.append(bAdd, bDemote, bCmd, row._r.pin, bHide, bKill);
    Object.assign(row._r, {
      add: bAdd, demote: bDemote, command: bCmd, hide: bHide, kill: bKill,
    });
  } else if (kind === 'background') {
    const bPromote = iconBtn('upload', '移到我的服务');
    bPromote.addEventListener('click', () => flag('promoted', true));
    const bKill = iconBtn('power', '结束进程', 'danger');
    bKill.addEventListener('click', () => {
      const s = findSvc(key());
      if (s) confirmKill(s);
    });
    cAct.append(bPromote, bKill);
    Object.assign(row._r, { promote: bPromote, kill: bKill });
  } else { // hidden
    const bUnhide = iconBtn('eye', '取消隐藏');
    bUnhide.addEventListener('click', () => flag('hidden', false));
    cAct.append(bUnhide);
    row._r.unhide = bUnhide;
  }
  return row;
}

function updateServiceRow(row, svc) {
  const r = row._r;
  /* 主标题：关联启动台应用 > 项目名 > 进程名；副标题：进程名 · PID */
  const title = svc.appName || svc.project || svc.name || '';
  setText(r.name, title);
  r.name.title = title;
  r.mark.hidden = !svc.appId;
  setText(r.sub, (svc.name || '') + (svc.pid ? ' · PID ' + svc.pid : ''));
  if (svc.port != null) {
    r.port.hidden = false;
    setText(r.port, ':' + svc.port);
    r.port.title = '打开 http://127.0.0.1:' + svc.port;
    r.port.setAttribute('aria-label', '打开 ' + title + '，端口 ' + svc.port);
  } else {
    r.port.hidden = true;
    r.port.removeAttribute('aria-label');
  }
  const full = svc.cwd || '';
  setText(r.cwd, full ? truncateMiddle(shortHome(full)) : '');
  r.cwd.title = full;
  setText(r.ldCpu, fmtPct(svc.cpu));
  setText(r.ldMem, fmtPct(svc.mem));
  setText(r.up, fmtUptime(svc.uptimeSec));
  if (r.pin) {
    if (r._pinned !== !!svc.pinned) {
      r._pinned = !!svc.pinned;
      setChildren(r.pin, icon(svc.pinned ? 'pin' : 'pin-off', 15));
    }
    r.pin.classList.toggle('active', !!svc.pinned);
  }
  if (r.demote) r.demote.hidden = !svc.promoted;
  setText(r.cmdCode, svc.cmd || '');
  const label = (action, button) => {
    if (!button) return;
    button.title = action + '：' + title;
    button.setAttribute('aria-label', action + '：' + title);
  };
  label('添加到启动台', r.add);
  label('移回应用后台', r.demote);
  label(row.classList.contains('expanded') ? '收起完整命令' : '展开完整命令', r.command);
  label(svc.pinned ? '取消置顶' : '置顶', r.pin);
  label('移入已隐藏列表', r.hide);
  label('结束进程', r.kill);
  label('移到我的服务', r.promote);
  label('取消隐藏', r.unhide);
}

/* ---------------- 关注的进程 ---------------- */
function createWatchRow() {
  const row = el('div', 'tr');
  const cTitle = el('span', 'w-title');
  const name = el('span', 'w-name');
  const kw = el('span', 'kw-tag');
  cTitle.append(name, kw);
  const cPid = el('span', 'c-pid');
  const load = loadCell();
  const cUp = el('span', 'c-up');
  const cAct = el('span', 'c-act');
  const bKill = iconBtn('power', '结束进程', 'danger');
  bKill.addEventListener('click', () => {
    const w = findWatch(row.dataset.key);
    if (w) confirmKill({ pid: w.pid, name: w.name, port: null });
  });
  cAct.append(bKill);
  row.append(cTitle, cPid, load.wrap, cUp, cAct);
  row._r = { name, kw, pid: cPid, ldCpu: load.cpu, ldMem: load.mem, up: cUp, kill: bKill };
  return row;
}

function updateWatchRow(row, w) {
  const r = row._r;
  setText(r.name, w.name || '');
  r.name.title = w.cmd || w.name || '';
  setText(r.kw, w.keyword || '');
  setText(r.pid, String(w.pid));
  setText(r.ldCpu, fmtPct(w.cpu));
  setText(r.ldMem, fmtPct(w.mem));
  setText(r.up, fmtUptime(w.uptimeSec));
  const target = w.name || ('PID ' + w.pid);
  r.kill.title = '结束进程：' + target;
  r.kill.setAttribute('aria-label', '结束进程：' + target);
}

function createChip(kw) {
  const c = el('span', 'chip');
  const t = el('span');
  t.textContent = kw;
  const x = el('button');
  x.type = 'button';
  x.textContent = '×';
  x.title = '删除关键字';
  x.setAttribute('aria-label', '删除关注关键字：' + kw);
  x.addEventListener('click', async () => {
    await act(post('/api/watch', { keyword: kw, action: 'remove' }));
    window.__poll();
  });
  c.append(t, x);
  return c;
}

/* ---------------- 服务监控渲染 ---------------- */
export function renderServices(d, firstRender) {
  const svcs = d.services || [];
  const mine = svcs.filter(s => s.group === 'mine' && !s.hidden).sort(svcSort);
  const bg = svcs.filter(s => s.group === 'background' && !s.hidden).sort(svcSort);
  const hid = svcs.filter(s => s.hidden).sort(svcSort);
  const watched = d.watched || [];
  const keywords = d.watchedKeywords || [];

  setKpi(statMine, String(mine.length));
  setKpi(statBg, String(bg.length));
  setText(statTime, state.lastUpdate ? fmtClock(state.lastUpdate) : '--:--:--');
  /* 全局概览：我的服务负载合计 + 启动台端口警告数 */
  let cpuSum = 0, memSum = 0;
  for (const s of mine) { cpuSum += s.cpu || 0; memSum += s.mem || 0; }
  setText(statCpu, cpuSum.toFixed(1) + '%');
  setText(statMem, memSum.toFixed(1) + '%');
  const warnCount = ((state.data && state.data.apps) || [])
    .filter(a => a.portConflict || a.portOccupied).length;
  setText(statWarn, String(warnCount));
  statWarn.classList.toggle('bad', warnCount > 0);

  reconcile(mineList, mine, s => s.key, () => createServiceRow('mine'), updateServiceRow, firstRender);
  reconcile(bgList, bg, s => s.key, () => createServiceRow('background'), updateServiceRow, firstRender);
  reconcile(hiddenList, hid, s => s.key, () => createServiceRow('hidden'), updateServiceRow, firstRender);
  reconcile(watchList, watched, w => w.pid, createWatchRow, updateWatchRow, firstRender);
  reconcile(watchChips, keywords, k => k, createChip, () => {}, firstRender);

  setText(bgCount, String(bg.length));
  hiddenPanel.hidden = hid.length === 0;
  setText(hiddenCount, '已隐藏 ' + hid.length + ' 个');

  mineEmpty.hidden = mine.length > 0;
  bgEmpty.hidden = bg.length > 0;
  watchEmpty.hidden = watched.length > 0;
  if (!watched.length) {
    setText(watchEmptyText, keywords.length
      ? '暂无匹配「' + keywords.join('、') + '」的进程'
      : '添加关键字后，匹配的进程会显示在这里');
  }
}

/* 折叠分区 */
bgHeader.addEventListener('click', () => {
  bgBody.hidden = !bgBody.hidden;
  bgHeader.classList.toggle('open', !bgBody.hidden);
  bgHeader.setAttribute('aria-expanded', String(!bgBody.hidden));
});
hiddenHeader.addEventListener('click', () => {
  hiddenBody.hidden = !hiddenBody.hidden;
  hiddenHeader.classList.toggle('open', !hiddenBody.hidden);
  hiddenHeader.setAttribute('aria-expanded', String(!hiddenBody.hidden));
});

/* 添加关注关键字 */
watchInput.addEventListener('keydown', async e => {
  if (e.key !== 'Enter') return;
  const kw = watchInput.value.trim();
  if (!kw) return;
  const r = await act(post('/api/watch', { keyword: kw, action: 'add' }));
  if (r && r.ok !== false) watchInput.value = '';
  window.__poll();
});
