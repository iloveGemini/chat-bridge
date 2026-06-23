// 聊天设置 → 世界书管理（本会话）：预载随角色/用户绑定的世界书，
// 并可勾选额外整本世界书手动带入当前会话。次级页面，不弹窗。
import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, showToast, ICONS } from '../utils.js';

class ChatWorldbooksView {
  constructor() {
    this.sessionId = null;
    this.data = null;
  }

  async open(sessionId) {
    this.sessionId = sessionId;
    const c = document.getElementById('chat-worldbooks-content');
    if (c) c.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载世界书...</div>';
    router.pushView('chat-worldbooks-view');
    await this.reload();
  }

  async reload() {
    try {
      this.data = await api.worldbookSession(this.sessionId);
    } catch (e) { this.data = { auto: [], others: [], manual_ids: [] }; }
    this.render();
  }

  render() {
    const c = document.getElementById('chat-worldbooks-content');
    if (!c) return;
    const d = this.data || {};
    const auto = d.auto || [];
    const others = d.others || [];
    const manual = new Set(d.manual_ids || []);

    const reasonLabel = (r) => r === 'character' ? '随角色' : (r === 'user' ? '随用户' : '自动');

    let html = `<div style="padding:14px 16px 4px;font-size:12px;color:var(--text-secondary);line-height:1.6;">
      下面「自动带入」的世界书已随当前角色或用户设定生效，无需操作。需要临时带入其它世界书，在「可选」里勾选即可（仅对本会话生效）。</div>`;

    html += `<div class="contact-group-title" style="margin-top:8px;">自动带入 (${auto.length})</div>`;
    if (!auto.length) {
      html += `<div style="color:var(--text-secondary);font-size:13px;padding:14px 16px;">当前角色和用户身份都没有绑定世界书。</div>`;
    } else {
      auto.forEach(b => {
        html += `<div class="list-item" style="opacity:0.95;">
          <div class="avatar" style="background:var(--surface);color:var(--text-secondary);border:0.5px solid var(--border-color);">${ICONS.book}</div>
          <div class="info">
            <div class="name">${escHtml(b.name)}</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">${reasonLabel(b.auto_reason)} · ${b.entry_count} 条</div>
          </div>
          <span style="color:var(--active-color);font-size:12px;">已生效</span>
        </div>`;
      });
    }

    html += `<div class="contact-group-title" style="margin-top:14px;">可选 · 手动挂载 (${others.length})</div>`;
    if (!others.length) {
      html += `<div style="color:var(--text-secondary);font-size:13px;padding:14px 16px;">没有其它世界书可挂载。可在「我 → 全局世界书管理」里新建。</div>`;
    } else {
      others.forEach(b => {
        const checked = manual.has(b.id);
        html += `<div class="list-item wb-opt" data-id="${b.id}">
          <div class="avatar" style="background:var(--surface);color:var(--text-secondary);border:0.5px solid var(--border-color);">${ICONS.book}</div>
          <div class="info">
            <div class="name">${escHtml(b.name)}</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">${b.entry_count} 条</div>
          </div>
          <label class="switch"><input type="checkbox" class="wb-chk" ${checked ? 'checked' : ''}><span class="slider"></span></label>
        </div>`;
      });
    }
    html += `<div style="height:30px;"></div>`;
    c.innerHTML = html;

    c.querySelectorAll('.wb-opt .wb-chk').forEach(chk => {
      chk.onchange = () => this.save();
      // 点整行也能切换
    });
  }

  async save() {
    const c = document.getElementById('chat-worldbooks-content');
    if (!c) return;
    const ids = [];
    c.querySelectorAll('.wb-opt').forEach(row => {
      const chk = row.querySelector('.wb-chk');
      if (chk && chk.checked) ids.push(+row.dataset.id);
    });
    try {
      const r = await api.worldbookSessionSet(ids, this.sessionId);
      showToast(r.ok ? '已更新' : '保存失败');
    } catch (e) { showToast('保存失败'); }
  }
}

export const chatWorldbooksView = new ChatWorldbooksView();
