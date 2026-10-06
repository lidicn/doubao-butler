// 豆包管家看板 PWA
const API = '';
const AUTH = ''; /* WO-ME-206：改走登录会话 cookie，浏览器里不再印管理员口令 */

let currentRoom = '客厅';
let boardData = null;
let chatHistory = [];

// ========== API ==========
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json; charset=utf-8', ...(opts.headers || {}) };
  if (!opts.noAuth && AUTH) headers['Authorization'] = AUTH;
  try {
    const r = await fetch(API + path, { ...opts, headers });
    if (r.status === 401) return { ok: false, error: '未登录：请先在管家主界面登录，再重新打开本页面' };
    return await r.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ========== Toast ==========
function toast(msg, ms = 2000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}

// ========== Tab ==========
function switchTab(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelector(`.tab[data-page="${page}"]`).classList.add('active');
  const titles = { board: '任务看板', roadmap: '路线图', chat: '对话', notification: '通知中心', decision: '决策请示', settings: '设置' };
  document.getElementById('page-title').textContent = titles[page];
  if (page === 'board') loadBoard();
  if (page === 'roadmap') loadRoadmap();
  if (page === 'chat') loadChat();
  if (page === 'notification') loadNotifications();
  if (page === 'decision') loadDecisions();
  if (page === 'settings') loadSettings();
}

// ========== 看板 ==========
async function loadBoard() {
  const boardEl = document.getElementById('board');
  const statsEl = document.getElementById('stats');
  boardEl.innerHTML = '<div class="loading">加载中...</div>';

  const r = await api('/api/task/board');
  if (!r.ok) {
    boardEl.innerHTML = '<div class="empty">加载失败：' + (r.error || '') + '</div>';
    return;
  }
  boardData = r.data;
  const cols = boardData.columns || {};
  const stats = boardData.stats || {};
  const overdue = boardData.overdue || {};

  // 统计卡片
  statsEl.innerHTML = `
    <div class="stat-card pending"><div class="num">${stats['待接手']||0}</div><div class="label">待接手</div></div>
    <div class="stat-card active"><div class="num">${stats['进行中']||0}</div><div class="label">进行中</div></div>
    <div class="stat-card done"><div class="num">${stats['已完成']||0}</div><div class="label">已完成</div></div>
    <div class="stat-card closed"><div class="num">${stats['已关闭']||0}</div><div class="label">已关闭</div></div>
  `;

  // 看板列
  const colDefs = [
    { key: '待接手', cls: 'pending' },
    { key: '进行中', cls: 'active' },
    { key: '已完成', cls: 'done' },
    { key: '已关闭', cls: 'closed' },
  ];
  boardEl.innerHTML = colDefs.map(col => {
    const tasks = cols[col.key] || [];
    const overdueIds = new Set((overdue.pending_over_30min || []).concat(overdue.active_over_24h || []).map(t => t.id));
    return `
      <div class="board-col ${col.cls}">
        <div class="col-header">${col.key}<span class="count">${tasks.length}</span></div>
        <div class="col-body">
          ${tasks.length === 0 ? '<div class="empty" style="padding:20px;">暂无</div>' : tasks.map(t => `
            <div class="task-card ${overdueIds.has(t.id) ? 'overdue' : ''}" onclick="showTaskDetail('${t.id}')">
              <div class="task-title">${esc(t.title)}</div>
              <div class="task-meta">
                <span class="tag ${t.priority.toLowerCase()}">${t.priority}</span>
                <span class="assignee">${t.assignee}</span>
                <span>${t.task_type}</span>
              </div>
              <div class="task-time">${fmtTime(t.created_at)}</div>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }).join('');
}

async function showTaskDetail(id) {
  const r = await api('/api/task/' + id);
  if (!r.ok) { toast('加载失败'); return; }
  const t = r.data;
  const events = (t.events || []).map(e =>
    `${fmtTime(e.ts)} ${e.status}${e.note ? ' - ' + e.note : ''}`
  ).join('\n');
  alert(`${t.title}\n\nID: ${t.id}\n状态: ${t.status}\n优先级: ${t.priority}\n负责人: ${t.assignee}\n类型: ${t.task_type}\n\n描述:\n${t.description}\n\n事件历史:\n${events}`);
}

// ========== 决策 ==========
async function loadDecisions() {
  const el = document.getElementById('decision-list');
  el.innerHTML = '<div class="loading">加载中...</div>';
  // 决策 API 待 M2 实现
  el.innerHTML = '<div class="empty">决策请示功能即将上线（M2）</div>';
}

// ========== 设置 ==========
async function loadSettings() {
  // 房间列表
  const r = await api('/api/dashboard/rooms');
  const rooms = r.ok ? (r.data.rooms || []) : [];
  const grid = document.getElementById('room-grid');
  grid.innerHTML = rooms.map(room =>
    `<button class="room-btn ${room === currentRoom ? 'active' : ''}" onclick="setRoom('${room}')">${room}</button>`
  ).join('');

  // 当前房间
  const r2 = await api('/api/dashboard/location');
  if (r2.ok) {
    currentRoom = r2.data.room;
    document.getElementById('current-room').textContent = currentRoom;
    document.querySelectorAll('.room-btn').forEach(b => {
      b.classList.toggle('active', b.textContent === currentRoom);
    });
  }

  // API 状态
  const r3 = await api('/api/task/board');
  document.getElementById('api-status').textContent = r3.ok ? '已连接' : '未连接';
  document.getElementById('api-status').style.color = r3.ok ? 'var(--green)' : 'var(--red)';
}

async function setRoom(room) {
  const r = await api('/api/dashboard/location', {
    method: 'POST',
    body: JSON.stringify({ room })
  });
  if (r.ok) {
    currentRoom = room;
    document.getElementById('current-room').textContent = room;
    document.querySelectorAll('.room-btn').forEach(b => {
      b.classList.toggle('active', b.textContent === room);
    });
    toast('已切换到 ' + room);
  } else {
    toast('切换失败');
  }
}

async function testSpeak() {
  toast('正在播报...');
  const r = await api('/api/dashboard/dev_speak', {
    method: 'POST',
    body: JSON.stringify({ text: '开发助理测试播报，当前位置' + currentRoom })
  });
  if (r.ok && r.data.ok) {
    toast(r.data.message);
  } else {
    toast('播报失败: ' + (r.data?.message || r.error || ''));
  }
}

// ========== 工具 ==========
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toTimeString().slice(0, 5);
  return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + d.toTimeString().slice(0, 5);
}

// ========== 对话 ==========
function loadChat() {
  renderChat();
}

function renderChat() {
  const container = document.getElementById('chat-container');
  if (chatHistory.length === 0) {
    container.innerHTML = '<div class="empty">跟管家说点什么吧<br><span style="font-size:12px;">支持任务查询、设备控制、闲聊等</span></div>';
    return;
  }
  container.innerHTML = chatHistory.map(m => `
    <div class="chat-msg ${m.role}">
      <div class="bubble">${esc(m.text)}</div>
      <div class="time">${m.time}</div>
    </div>
  `).join('');
  container.scrollTop = container.scrollHeight;
}

let pwaChatLastId = 0;
let pwaChatPolling = false;

function setChatStatus(msg) {
  const el = document.getElementById('chat-status');
  if (el) el.textContent = msg;
  console.log('[pwa-chat]', msg);
}

async function sendChat() {
  const input = document.getElementById('chat-text');
  const btn = document.getElementById('chat-send-btn');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  btn.disabled = true;
  setChatStatus('发送中...');

  try {
    const r = await fetch('/api/pwa_chat/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ text })
    });
    const data = await r.json();
    if (data.ok) {
      const forwarded = data.data?.forwarded_to_pm;
      setChatStatus(forwarded ? '已发送到管家对话' : '已保存（转发管家失败：' + (data.data?.forward_error || '未知') + '）');
      // 立即拉取新消息
      pollPwaChat();
    } else {
      setChatStatus('发送失败：' + (data.error || '未知'));
    }
  } catch (e) {
    setChatStatus('网络错误：' + e.message);
  }
  btn.disabled = false;
}

async function pollPwaChat() {
  if (pwaChatPolling) return;
  pwaChatPolling = true;
  // R2-59: add 8s timeout to prevent hung fetch from permanently stopping polling
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch('/api/pwa_chat/messages?since=' + pwaChatLastId, {
      headers: { },
      signal: ctrl.signal
    });
    const data = await r.json();
    if (data.ok && data.data.items) {
      const items = data.data.items;
      if (items.length > 0) {
        for (const m of items) {
          const timeStr = new Date(m.created_at * 1000).toTimeString().slice(0, 5);
          chatHistory.push({ role: m.role === 'pm' ? 'butler' : 'user', text: m.text, time: timeStr });
        }
        pwaChatLastId = data.data.latest_id;
        renderChat();
      }
    }
  } catch (e) {
    console.error('[pwa-chat] poll error:', e);
  } finally {
    clearTimeout(timer);
    pwaChatPolling = false;
  }
}

// ========== 决策 ==========
async function loadDecisions() {
  const el = document.getElementById('decision-list');
  el.innerHTML = '<div class="loading">加载中...</div>';

  const r = await api('/api/decision/list');
  if (!r.ok) {
    el.innerHTML = '<div class="empty">加载失败</div>';
    return;
  }
  const decisions = r.data.decisions || [];
  const pending = r.data.pending_count || 0;
  const badge = document.getElementById('dec-badge');
  if (pending > 0) {
    badge.textContent = pending > 9 ? '9+' : pending;
    badge.style.display = 'flex';
  } else {
    badge.style.display = 'none';
  }

  if (decisions.length === 0) {
    el.innerHTML = '<div class="empty">暂无决策请示</div>';
    return;
  }

  el.innerHTML = decisions.map(d => {
    const isPending = d.status === '待投票';
    const optionsHtml = d.options.map((opt, i) => {
      const selected = d.choice === i;
      return `<button class="dec-option ${selected ? 'selected' : ''}"
        ${isPending ? `onclick="voteDecision('${d.id}', ${i})"` : 'disabled'}>
        ${String.fromCharCode(65 + i)}. ${esc(opt)}
        ${selected ? ' ✓' : ''}
      </button>`;
    }).join('');
    return `
      <div class="decision-card">
        <div class="dec-title">${esc(d.title)} ${d.priority === 'urgent' ? '<span style="color:var(--red);font-size:12px;">[紧急]</span>' : ''}</div>
        ${d.description ? `<div class="dec-desc">${esc(d.description)}</div>` : ''}
        <div class="dec-options">${optionsHtml}</div>
        <div class="dec-meta">
          ${d.id} | ${d.status} | ${fmtTime(d.created_at)}
          ${d.voted_at ? ` | 投票于 ${fmtTime(d.voted_at)}` : ''}
        </div>
      </div>
    `;
  }).join('');
}

async function voteDecision(id, choice) {
  const r = await api(`/api/decision/${id}/vote`, {
    method: 'POST',
    body: JSON.stringify({ choice })
  });
  if (r.ok) {
    toast('投票成功');
    loadDecisions();
  } else {
    toast('投票失败：' + (r.error || ''));
  }
}

// ========== 路线图 ==========
async function loadRoadmap() {
  const el = document.getElementById('roadmap-container');
  el.innerHTML = '<div class="loading">加载中...</div>';
  try {
    const r = await fetch('/dashboard/roadmap.json');
    const data = await r.json();
    el.innerHTML = (data.projects || []).map(p => `
      <div class="roadmap-project">
        <div class="proj-title"><span class="dot" style="background:${p.color}"></span>${p.name}</div>
        <div class="roadmap-timeline">
          ${(p.milestones || []).map(m => `
            <div class="roadmap-item ${m.status}">
              <div class="ms-version">${m.version} · ${m.date || ''}</div>
              <div class="ms-name">${esc(m.name)}</div>
            </div>
          `).join('')}
        </div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty">路线图加载失败</div>';
  }
}

// ========== 通知中心 ==========
async function loadNotifications() {
  const el = document.getElementById('notification-list');
  el.innerHTML = '<div class="loading">加载中...</div>';
  const r = await api('/api/notification/list?limit=50');
  if (!r.ok) {
    el.innerHTML = '<div class="empty">加载失败</div>';
    return;
  }
  const items = r.data.items || [];
  const unread = r.data.unread_count || 0;
  document.getElementById('notif-unread-text').textContent = unread > 0 ? `${unread} 条未读` : '全部已读';
  updateNotifBadge(unread);

  if (items.length === 0) {
    el.innerHTML = '<div class="empty">暂无通知</div>';
    return;
  }

  const typeNames = { task: '任务', decision: '决策', system: '系统', dev_speak: '播报' };
  el.innerHTML = items.map(n => `
    <div class="notif-item ${n.is_read ? '' : 'unread'} ${n.level}" onclick="readNotif(${n.id})">
      <div class="notif-title">${esc(n.title)}</div>
      ${n.body ? `<div class="notif-body">${esc(n.body)}</div>` : ''}
      <div class="notif-meta">
        <span class="notif-type">${typeNames[n.type] || n.type}</span>
        <span>${fmtTime(n.created_at)}</span>
      </div>
    </div>
  `).join('');
}

async function readNotif(id) {
  await api(`/api/notification/${id}/read`, { method: 'POST' });
  loadNotifications();
}

async function readAllNotif() {
  await api('/api/notification/read_all', { method: 'POST' });
  toast('已全部标记为已读');
  loadNotifications();
}

function updateNotifBadge(count) {
  const badge = document.getElementById('notif-badge');
  if (count > 0) {
    badge.textContent = count > 9 ? '9+' : count;
    badge.style.display = 'flex';
  } else {
    badge.style.display = 'none';
  }
}

// ========== 初始化 ==========
async function init() {
  console.log('[dashboard] init start v0.3');
  const verEl = document.getElementById('app-version');
  if (verEl) verEl.textContent = 'v0.3 (JS已加载)';
  try {
  const r = await api('/api/dashboard/location');
  if (r.ok) {
    currentRoom = r.data.room;
    document.getElementById('current-room').textContent = currentRoom;
  }
  loadBoard();
  // 启动 PWA 对话轮询（每3秒拉取新消息）
  pollPwaChat();
  setInterval(pollPwaChat, 3000);
  // 每 30 秒刷新看板
  setInterval(() => {
    if (document.getElementById('page-board').classList.contains('active')) loadBoard();
  }, 30000);
  // 每 15 秒检查待投票决策 + 未读通知（更新角标）
  setInterval(async () => {
    const r = await api('/api/decision/list?limit=1');
    if (r.ok) {
      const pending = r.data.pending_count || 0;
      const badge = document.getElementById('dec-badge');
      if (pending > 0) {
        badge.textContent = pending > 9 ? '9+' : pending;
        badge.style.display = 'flex';
      } else {
        badge.style.display = 'none';
      }
    }
    const r2 = await api('/api/notification/list?limit=1');
    if (r2.ok) updateNotifBadge(r2.data.unread_count || 0);
  }, 15000);
  } catch (e) {
    console.error('[dashboard] init error:', e);
    const el = document.getElementById('js-status');
    if (el) { el.style.display = 'block'; el.textContent = 'init错误: ' + e.message; }
  }
}

init();
