import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, showToast, ICONS } from '../utils.js';
import { contactProfileView } from './contactProfileView.js';

class ContactsView {
  constructor() {
    this.container = document.getElementById('contacts-list');
    this.characters = [];
    this.searchKeyword = '';
  }

  async refresh() {
    this.container = document.getElementById('contacts-list');
    try {
      const data = await api.fetchPrompts();
      this.characters = data.characters || [];
      this.render();
    } catch (e) {
      console.error(e);
      if (this.container) this.container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">通讯录加载失败</div>';
    }
  }

  render() {
    if (!this.container) return;

    // 实时搜索过滤
    const kw = this.searchKeyword.trim().toLowerCase();
    const filtered = this.characters.filter(c => {
      const n = (c.name || c.key).toLowerCase();
      return n.includes(kw) || c.key.toLowerCase().includes(kw);
    });

    let html = `
      <div class="search-bar-container">
        <div class="search-input-box">
          <span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span>
          <input type="text" id="contacts-search-input" placeholder="搜索联系人..." value="${escHtml(this.searchKeyword)}">
          ${this.searchKeyword ? `<span id="contacts-search-clear" style="cursor:pointer;color:var(--text-secondary);">✕</span>` : ''}
        </div>
      </div>

      <div class="list-item" id="contact-new" style="background:var(--surface);">
        <div class="avatar" style="background:var(--active-color);">${ICONS.plus}</div>
        <div class="info"><div class="name" style="font-weight:bold;color:var(--active-color);">新建角色</div></div>
      </div>
      
      <div class="list-item" id="contact-groups-btn" style="background:var(--surface);">
        <div class="avatar" style="background:#345392;">${ICONS.group}</div>
        <div class="info"><div class="name" style="font-weight:bold;">群聊</div></div>
        <span style="color:var(--text-secondary);font-family:monospace;">〉</span>
      </div>

      <div class="contact-group-title" style="margin-top:12px;">联系人 (${filtered.length})</div>
    `;

    filtered.forEach(c => {
      const name = c.name || c.key;
      const avatar = c.avatar || getFallbackAvatar(name);
      html += `<div class="list-item contact-item" data-key="${escHtml(c.key)}">
        <img class="avatar" style="object-fit:cover;" src="${avatar}">
        <div class="info"><div class="name">${escHtml(name)}</div></div>
      </div>`;
    });

    if (filtered.length === 0) {
      html += `<div style="text-align:center;padding:30px;color:var(--text-secondary);font-size:14px;">没有搜到相关角色</div>`;
    }

    this.container.innerHTML = html;
    this.bindEvents();
  }

  bindEvents() {
    // 搜索过滤绑定
    const searchInput = this.container.querySelector('#contacts-search-input');
    const clearBtn = this.container.querySelector('#contacts-search-clear');

    if (searchInput) {
      searchInput.oninput = (e) => {
        this.searchKeyword = e.target.value;
        this.render();
        const nextInp = document.getElementById('contacts-search-input');
        if (nextInp) nextInp.focus();
      };
    }
    if (clearBtn) clearBtn.onclick = () => { this.searchKeyword = ''; this.render(); };

    // 新建角色表单
    this.container.querySelector('#contact-new').onclick = () => {
      import('../modals.js').then(m => m.openPromptEditor('character', null, () => this.refresh()));
    };

    // 点击联系人 -> 直接平滑推入Profile主页！(干掉反直觉的ActionSheet)
    this.container.querySelectorAll('.contact-item').forEach(el => {
      el.onclick = () => contactProfileView.open(el.dataset.key);
    });

    // 群聊入口
    this.container.querySelector('#contact-groups-btn').onclick = () => {
      showToast('群聊空间聚合页开发中');
    };
  }
}

export const contactsView = new ContactsView();