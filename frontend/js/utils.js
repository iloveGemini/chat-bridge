// 通用工具：转义 / Markdown / 时间 / Toast / 主题 / 头像 / 弹层构造器

export function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = (s == null ? '' : String(s));
  return d.innerHTML;
}

// 轻量 Markdown 渲染（从旧 app/core.js 迁移并精简）
export function renderMarkdown(text) {
  let html = escHtml(text);
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => '\x00PRE\x00' + code.trim() + '\x00/PRE\x00');
  html = html.replace(/`([^`]+)`/g, '\x00CODE\x00$1\x00/CODE\x00');
  html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/(?<!href="|">)(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/^([-*]){3,}\s*$/gm, '<hr>');
  html = html.replace(/\x00PRE\x00([\s\S]*?)\x00\/PRE\x00/g, '<pre><code>$1</code></pre>');
  html = html.replace(/\x00CODE\x00([\s\S]*?)\x00\/CODE\x00/g, '<code>$1</code>');

  const codeBlockRe = /(<pre><code>[\s\S]*?<\/code><\/pre>)/g;
  const segments = html.split(codeBlockRe);
  let out = '';
  for (let s = 0; s < segments.length; s++) {
    if (segments[s].startsWith('<pre><code>')) { out += segments[s]; continue; }
    const lines = segments[s].split('\n');
    let buf = '', inUl = false, inOl = false, inBq = false;
    for (const line of lines) {
      const ulMatch = line.match(/^[\-\*]\s+(.+)/);
      const olMatch = line.match(/^\d+\.\s+(.+)/);
      const bqMatch = line.match(/^&gt;\s?(.*)/);
      const isBlank = line === '';
      if (!ulMatch && !isBlank && inUl) { buf += '</ul>'; inUl = false; }
      if (!olMatch && !isBlank && inOl) { buf += '</ol>'; inOl = false; }
      if (!bqMatch && !isBlank && inBq) { buf += '</blockquote>'; inBq = false; }
      if (ulMatch) { if (!inUl) { buf += '<ul>'; inUl = true; } buf += '<li>' + ulMatch[1] + '</li>'; }
      else if (olMatch) { if (!inOl) { buf += '<ol>'; inOl = true; } buf += '<li>' + olMatch[1] + '</li>'; }
      else if (bqMatch) { if (!inBq) { buf += '<blockquote>'; inBq = true; } buf += bqMatch[1] + '<br>'; }
      else if (isBlank) { if (!inUl && !inOl && !inBq) buf += '<br>'; }
      else { buf += line + '<br>'; }
    }
    if (inUl) buf += '</ul>';
    if (inOl) buf += '</ol>';
    if (inBq) buf += '</blockquote>';
    out += buf;
  }
  out = out.replace(/<br>(<\/?(?:ul|ol|li|blockquote|pre|hr))/g, '$1');
  return out;
}

export function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() !== now.toDateString()) {
      return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' + time;
    }
    return time;
  } catch (e) { return ''; }
}

export function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) { console.log('[toast]', msg); return; }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove('show'), 2500);
}
window.showToast = showToast;

// ===== 主题 =====
export const THEME_LABELS = { light: '浅色', dark: '深色', midnight: '午夜来电', paper: '纸质墨色' };
export function applyTheme(mode) {
  if (!THEME_LABELS[mode]) mode = 'dark';
  if (mode === 'light') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', mode);
  localStorage.setItem('chat-theme', mode);
}
export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') || 'light';
}

// 动态默认头像（纯 SVG，无图片依赖）
export function getFallbackAvatar(name) {
  const str = String(name || '未命名').trim();
  const char = (str ? str.charAt(0) : '—').toUpperCase();
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
  const hue = Math.abs(hash) % 360;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100%" height="100%" fill="hsl(${hue}, 45%, 55%)"/><text x="50%" y="54%" font-size="46" font-family="system-ui, sans-serif" font-weight="600" fill="#fff" dominant-baseline="middle" text-anchor="middle">${char}</text></svg>`;
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

// ===== 通用动作面板（iOS 风格底部选项） =====
// items: [{label, action, destructive}]  返回 action 字符串经 onPick 回调
export function actionSheet(items, onPick) {
  const mask = document.createElement('div'); mask.className = 'action-sheet-mask';
  const sheet = document.createElement('div'); sheet.className = 'action-sheet';
  const groups = {};
  const main = document.createElement('div'); main.className = 'action-sheet-group';
  const del = document.createElement('div'); del.className = 'action-sheet-group';
  items.forEach(it => {
    const row = document.createElement('div');
    row.className = 'action-sheet-item' + (it.destructive ? ' destructive' : '');
    row.textContent = it.label;
    row.addEventListener('click', () => { dismiss(); onPick && onPick(it.action); });
    (it.destructive ? del : main).appendChild(row);
  });
  if (main.children.length) sheet.appendChild(main);
  if (del.children.length) sheet.appendChild(del);
  const cancel = document.createElement('div'); cancel.className = 'action-sheet-cancel';
  cancel.textContent = '取消'; cancel.addEventListener('click', dismiss);
  sheet.appendChild(cancel);
  mask.addEventListener('click', dismiss);
  document.body.appendChild(mask); document.body.appendChild(sheet);
  requestAnimationFrame(() => { mask.classList.add('show'); sheet.classList.add('show'); });
  function dismiss() { sheet.classList.remove('show'); mask.classList.remove('show'); setTimeout(() => { sheet.remove(); mask.remove(); }, 300); }
}

// ===== 通用选择弹层 =====
// title, options:[{name,label,selected,avatar}], opts:{onSelect,onEdit,onKebab,onNew,newLabel}
export function selectSheet(title, options, opts = {}) {
  const mask = document.createElement('div'); mask.className = 'sheet-mask';
  const sheet = document.createElement('div'); sheet.className = 'sheet';
  sheet.innerHTML = `<div class="sheet-handle"></div><div class="sheet-title">${escHtml(title)}</div><div class="sheet-list"></div>` +
    (opts.onNew ? `<button class="sheet-new-btn">${escHtml(opts.newLabel || '+ 新建')}</button>` : '');
  const list = sheet.querySelector('.sheet-list');
  options.forEach(it => {
    const row = document.createElement('div');
    row.className = 'sheet-option' + (it.selected ? ' selected' : '');
    let html = '<span class="sheet-option-name">' + (it.avatar ? `<img src="${it.avatar}">` : '') + escHtml(it.label) + '</span>';
    if (opts.onEdit) html += '<button class="sheet-edit-pencil" data-act="edit">✎</button>';
    if (opts.onKebab) html += '<button class="sheet-edit-pencil" data-act="kebab" style="font-size:16px;">⋮</button>';
    row.innerHTML = html;
    row.addEventListener('click', (e) => {
      const actBtn = e.target.closest('[data-act]');
      const act = actBtn ? actBtn.dataset.act : 'select';
      if (act === 'edit') { e.stopPropagation(); dismiss(); opts.onEdit(it.name); }
      else if (act === 'kebab') { e.stopPropagation(); opts.onKebab(it.name); }
      else { dismiss(); opts.onSelect && opts.onSelect(it.name); }
    });
    list.appendChild(row);
  });
  if (opts.onNew) sheet.querySelector('.sheet-new-btn').addEventListener('click', () => { dismiss(); opts.onNew(); });
  mask.addEventListener('click', dismiss);
  document.body.appendChild(mask); document.body.appendChild(sheet);
  requestAnimationFrame(() => { mask.classList.add('show'); sheet.classList.add('show'); });
  function dismiss() { sheet.classList.remove('show'); mask.classList.remove('show'); setTimeout(() => { sheet.remove(); mask.remove(); }, 300); }
  return { dismiss };
}

// ===== 常规 SVG 图标库 (去 Emoji 化，极简线性风格) =====
// ===== 常规 SVG 图标库 (注入标准18x18尺寸，防止0px塌陷) =====
export const ICONS = {
  search: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>`,
  group: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>`,
  plus: `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>`,
  voice: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>`,
  book: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>`,
  photo: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>`,
  camera: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>`,
  chat: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`,
  userPlus: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="8.5" cy="7" r="4"></circle><line x1="20" y1="8" x2="20" y2="14"></line><line x1="23" y1="11" x2="17" y2="11"></line></svg>`,

  /* ======== 补回被你漏掉尺寸的 SVG ======== */
  copy: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`,
  edit: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>`,
  reroll: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>`,
  play: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>`,
  more: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1.5"></circle><circle cx="19" cy="12" r="1.5"></circle><circle cx="5" cy="12" r="1.5"></circle></svg>`,
  trash: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`,

  /* 注入标准18x18宽高，根治隐形 */
  savePoint: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`,
  branch: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>`,
  rewind: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><polyline points="3 3 3 8 8 8"></polyline></svg>`,
  // 在 ICONS 中补上这些
  pin: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="17" x2="12" y2="3"/><path d="M5 12l7-7 7 7"/><line x1="2" y1="21" x2="22" y2="21"/></svg>`,
  bubble: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>`,
  // 在 ICONS 对象中追加以下三个图标
  plugin: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>`,
  database: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>`,
  milestone: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>`
};

// ===== 大面板全屏化 (取代原有的固定弹窗) =====
export function panel(title, withStatus = false) {
  const titleEl = document.getElementById('panel-title');
  const contentEl = document.getElementById('panel-content');
  if (titleEl) titleEl.textContent = title;

  contentEl.innerHTML = (withStatus ? '<div class="panel-status"></div>' : '') + '<div class="panel-body" style="padding-top:10px;"></div>';

  // 核心魔法：直接向路由推入面板视图，原来的弹窗逻辑自动变为页面跳转
  import('./router.js').then(m => m.router.pushView('generic-panel-view'));

  const close = () => { import('./router.js').then(m => m.router.popView()); };

  return {
    box: contentEl, // 保持向后兼容
    body: contentEl.querySelector('.panel-body'),
    status: contentEl.querySelector('.panel-status'),
    close
  };
}