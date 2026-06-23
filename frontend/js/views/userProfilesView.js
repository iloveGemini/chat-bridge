import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, actionSheet, showToast, ICONS } from '../utils.js';

class UserProfilesView {
  constructor() {
    this.users = [];
  }

  async open(autoOpenKey = null) {
    const container = document.getElementById('user-profiles-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载分身信息中...</div>';
    router.pushView('user-profiles-view');

    try {
      const data = await api.fetchPrompts();
      this.users = data.users || [];
    } catch (e) { this.users = []; }

    this.render();

    // 如果是通过点击某个头像进来的，直接弹出对应的编辑面板
    if (autoOpenKey) {
      this.openEditor(autoOpenKey);
    }
  }

  render() {
    const container = document.getElementById('user-profiles-content');
    if (!container) return;

    let html = `
      <div class="list-item" id="up-new" style="background:var(--surface);">
        <div class="avatar" style="background:var(--active-color);">${ICONS.plus}</div>
        <div class="info"><div class="name" style="font-weight:bold;color:var(--active-color);">创建新分身</div></div>
      </div>
      <div class="contact-group-title" style="margin-top:12px;">已有分身 (${this.users.length})</div>
    `;

    this.users.forEach(u => {
      const name = u.name || u.key;
      const avatar = u.avatar || getFallbackAvatar(name);
      html += `
        <div class="list-item profile-row" data-key="${escHtml(u.key)}">
          <img class="avatar" style="object-fit:cover;" src="${avatar}">
          <div class="info"><div class="name">${escHtml(name)}</div></div>
          <span style="color:var(--text-secondary); font-size:12px;">编辑 〉</span>
        </div>
      `;
    });

    container.innerHTML = html;

    container.querySelector('#up-new').onclick = () => this.openEditor(null);
    container.querySelectorAll('.profile-row').forEach(el => {
      el.onclick = () => this.openEditor(el.dataset.key);
    });
  }

  openEditor(key) {
    // 复用原有的 prompt 编辑器，类型传 'user'
    import('../modals.js').then(m => m.openPromptEditor('user', key, () => {
      // 保存完毕后，刷新当前分身列表和首页的“我”面板
      this.open();
      import('./meView.js').then(mv => mv.meView.render());
    }));
  }
}

export const userProfilesView = new UserProfilesView();