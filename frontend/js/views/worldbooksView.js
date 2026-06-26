// 全局世界书管理：列表（worldbooks-view）→ 单本详情/条目（worldbook-detail-view）
// 世界书是独立实体，可绑角色 / 随用户带走 / 不绑（聊天里手动挂载）。
import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, showToast, ICONS, selectSheet, actionSheet } from '../utils.js';

class WorldbooksView {
  constructor() {
    this.books = [];
    this.chars = [];
    this.users = [];
    this.bookId = null;
  }

  // ============ 列表页 ============
  async open() {
    const c = document.getElementById('worldbooks-content');
    if (c) c.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载世界书...</div>';
    router.pushView('worldbooks-view');
    await this.reload();
  }

  async reload() {
    try {
      const [d, p] = await Promise.all([api.worldbooksList(), api.fetchPrompts()]);
      this.books = d.worldbooks || [];
      this.chars = p.characters || [];
      this.users = p.users || [];
    } catch (e) { this.books = []; }
    this.renderList();
  }

  bindDesc(b) {
    if (b.bind_type === 'character') {
      const ch = this.chars.find(x => x.key === b.bind_target);
      return '绑定角色 · ' + (ch ? ch.name : (b.bind_target || '未指定'));
    }
    if (b.bind_type === 'user') {
      const u = this.users.find(x => x.key === b.bind_target);
      return '随用户带走 · ' + (u ? u.name : (b.bind_target || '未指定'));
    }
    return '未绑定 · 聊天里手动挂载';
  }

  renderList() {
    const c = document.getElementById('worldbooks-content');
    if (!c) return;
    let html = `
      <div class="list-item" id="wb-new" style="background:var(--surface);">
        <div class="avatar" style="background:var(--active-color);color:#fff;">${ICONS.plus}</div>
        <div class="info"><div class="name" style="font-weight:bold;color:var(--active-color);">新建世界书</div></div>
      </div>
      <div class="contact-group-title" style="margin-top:12px;">全部世界书 (${this.books.length})</div>`;

    if (!this.books.length) {
      html += `<div style="color:var(--text-secondary);font-size:13px;padding:20px;line-height:1.6;">
        还没有世界书。新建一本，写好条目，再绑定到角色或用户；不绑定的可在聊天设置里按需手动挂载。</div>`;
    }
    this.books.forEach(b => {
      html += `
        <div class="list-item wb-row" data-id="${b.id}">
          <div class="avatar" style="background:var(--surface);color:var(--text-secondary);border:0.5px solid var(--border-color);">${ICONS.book}</div>
          <div class="info">
            <div class="name">${escHtml(b.name)}</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">${escHtml(this.bindDesc(b))} · ${b.entry_count} 条</div>
          </div>
          <span style="color:var(--text-secondary);font-size:12px;">管理 〉</span>
        </div>`;
    });
    c.innerHTML = html;
    c.querySelector('#wb-new').onclick = () => this.createBook();
    c.querySelectorAll('.wb-row').forEach(el => el.onclick = () => this.openDetail(+el.dataset.id));
  }

  async createBook() {
    const r = await api.worldbookCreate({ name: '未命名世界书', bind_type: 'none', bind_target: '' });
    if (r.ok) { await this.reload(); this.openDetail(r.id); }
    else showToast('创建失败');
  }

  // ============ 详情页（单本：名称 + 绑定 + 条目）============
  async openDetail(bookId) {
    this.bookId = bookId;
    const c = document.getElementById('worldbook-detail-content');
    if (c) c.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载...</div>';
    router.pushView('worldbook-detail-view');
    await this.reloadDetail();
  }

  async reloadDetail() {
    try {
      const [d, listRes, p] = await Promise.all([
        api.worldbooksList(), api.loreList(this.bookId), api.fetchPrompts()
      ]);
      this.books = d.worldbooks || [];
      this.chars = p.characters || [];
      this.users = p.users || [];
      this.entries = listRes.lore || [];
    } catch (e) { this.entries = []; }
    this.book = this.books.find(b => b.id === this.bookId) || { id: this.bookId, name: '', bind_type: 'none', bind_target: '' };
    this.renderDetail();
  }

  renderDetail() {
    const c = document.getElementById('worldbook-detail-content');
    if (!c) return;
    const b = this.book;
    const parseKeys = s => (s || '').split(/[，,]/).map(x => x.trim()).filter(Boolean);

    const card = (e) => {
      const on = !!e.always_on;
      const pos = (e.position === 'before') ? 'before' : 'after';
      return `<div class="lore-item" data-id="${e.id}" style="margin:8px 0;padding:10px;border:1px solid var(--border-color);border-radius:10px;background:var(--surface);">
        <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
          <input class="p-inp lore-title" value="${escHtml(e.title)}" placeholder="标题" style="font-weight:600;">
          <label style="font-size:11px;color:var(--text-secondary);white-space:nowrap;"><input type="checkbox" class="lore-on" ${on ? 'checked' : ''}>常驻</label>
          <input class="p-inp lore-pri" type="number" value="${e.priority || 0}" style="max-width:54px;flex:0 0 54px;">
        </div>
        <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
          <span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;">注入位置</span>
          <select class="p-inp lore-pos" style="flex:1;">
            <option value="after" ${pos === 'after' ? 'selected' : ''}>尾部 · 贴对话末尾（召回，默认）</option>
            <option value="before" ${pos === 'before' ? 'selected' : ''}>系统头 · 常驻强注意力区</option>
          </select>
        </div>
        <input class="p-inp lore-keys" value="${escHtml((e.keys || []).join('，'))}" placeholder="触发词，逗号分隔" style="width:100%;margin-bottom:6px;${on ? 'opacity:.5;' : ''}">
        <textarea class="p-ta lore-content" placeholder="设定正文">${escHtml(e.content)}</textarea>
        <div style="text-align:right;margin-top:5px;"><button class="p-btn lore-save">保存</button> <button class="p-del lore-del">删除</button></div></div>`;
    };

    const ons = (this.entries || []).filter(e => e.always_on);
    const keyed = (this.entries || []).filter(e => !e.always_on);
    let entriesHtml = '';
    if (ons.length) entriesHtml += `<div class="p-sec">常驻 · 永远注入 (${ons.length})</div>` + ons.map(card).join('');
    if (keyed.length) entriesHtml += `<div class="p-sec">触发 · 命中关键词才注入 (${keyed.length})</div>` + keyed.map(card).join('');
    if (!this.entries.length) entriesHtml += '<div style="color:var(--text-secondary);font-size:13px;padding:14px 4px;">还没有条目，点「新建条目」。</div>';

    c.innerHTML = `
      <div class="ios-group" style="margin-top:16px;">
        <div class="ios-item" id="wbd-name">
          <span class="label">名称</span>
          <span class="val">${escHtml(b.name)}</span>
        </div>
        <div class="ios-item" id="wbd-bind">
          <span class="label">绑定方式</span>
          <span class="val">${escHtml(this.bindDesc(b))}</span>
        </div>
      </div>

      <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 4px;margin-top:10px;">
        <div style="font-size:12px;color:var(--text-secondary);flex:1;">常驻条目永远注入；其余靠触发词命中</div>
        <button class="p-btn" id="wbd-add">新建条目</button>
      </div>
      <div id="wbd-entries">${entriesHtml}</div>

      <div class="ios-group" style="margin-top:30px;">
        <div class="ios-item" id="wbd-del" style="justify-content:center;color:#ff3b30;font-weight:500;">删除这本世界书</div>
      </div>
      <div style="height:30px;"></div>
    `;

    // 改名（行内编辑，不弹窗）
    c.querySelector('#wbd-name').onclick = () => this.editName();
    c.querySelector('#wbd-bind').onclick = () => this.editBinding();
    c.querySelector('#wbd-add').onclick = async () => {
      const r = await api.loreAdd({ book_id: this.bookId, title: '新设定', content: '（写正文）', keys: [] });
      if (r.ok) this.reloadDetail(); else showToast('新建失败');
    };
    c.querySelector('#wbd-del').onclick = async () => {
      if (!confirm(`确定删除「${b.name}」及其全部条目吗？`)) return;
      const r = await api.worldbookDelete(this.bookId);
      if (r.ok) { showToast('已删除'); router.popView(); this.reload(); }
      else showToast('删除失败');
    };

    c.querySelectorAll('.lore-item').forEach(row => {
      const id = +row.dataset.id;
      const onBox = row.querySelector('.lore-on');
      onBox.onchange = () => { row.querySelector('.lore-keys').style.opacity = onBox.checked ? '.5' : '1'; };
      row.querySelector('.lore-save').onclick = async () => {
        const title = row.querySelector('.lore-title').value.trim();
        const content = row.querySelector('.lore-content').value.trim();
        if (!title || !content) { showToast('标题和正文都要填'); return; }
        const r = await api.loreUpdate({
          id, title, content,
          keys: parseKeys(row.querySelector('.lore-keys').value),
          priority: +row.querySelector('.lore-pri').value || 0,
          always_on: onBox.checked,
          position: row.querySelector('.lore-pos').value
        });
        showToast(r.ok ? '已保存' : '失败');
      };
      row.querySelector('.lore-del').onclick = async () => {
        await api.loreDelete(id); showToast('已删除'); this.reloadDetail();
      };
    });
  }

  // 行内改名：把名称行变成输入框
  editName() {
    const row = document.getElementById('wbd-name');
    if (!row || row.dataset.editing) return;
    row.dataset.editing = '1';
    row.innerHTML = `<input class="p-inp" id="wbd-name-inp" value="${escHtml(this.book.name)}" style="font-size:14px;">
      <button class="p-btn" id="wbd-name-ok" style="margin-left:8px;">保存</button>`;
    const inp = row.querySelector('#wbd-name-inp');
    inp.focus();
    row.querySelector('#wbd-name-ok').onclick = async () => {
      const name = inp.value.trim() || '未命名世界书';
      const r = await api.worldbookUpdate({ id: this.bookId, name });
      if (r.ok) { this.book.name = name; showToast('已保存'); }
      this.reloadDetail();
    };
  }

  editBinding() {
    selectSheet('绑定方式', [
      { name: 'none', label: '不绑定（聊天里手动挂载）', selected: this.book.bind_type === 'none' },
      { name: 'character', label: '绑定角色（该角色所有会话自动带入）', selected: this.book.bind_type === 'character' },
      { name: 'user', label: '随用户带走（该用户身份自动带入）', selected: this.book.bind_type === 'user' },
    ], {
      onSelect: async (type) => {
        if (type === 'none') {
          await api.worldbookUpdate({ id: this.bookId, bind_type: 'none', bind_target: '' });
          showToast('已设为未绑定'); this.reloadDetail();
        } else if (type === 'character') {
          this.pickTarget('character', this.chars);
        } else if (type === 'user') {
          this.pickTarget('user', this.users);
        }
      }
    });
  }

  pickTarget(type, list) {
    const title = type === 'character' ? '绑定到哪个角色' : '随哪个用户带走';
    selectSheet(title, (list || []).map(x => ({
      name: x.key, label: x.name || x.key, selected: this.book.bind_target === x.key
    })), {
      onSelect: async (key) => {
        const r = await api.worldbookUpdate({ id: this.bookId, bind_type: type, bind_target: key });
        if (r.ok) showToast('已绑定');
        this.reloadDetail();
      }
    });
  }
}

export const worldbooksView = new WorldbooksView();
