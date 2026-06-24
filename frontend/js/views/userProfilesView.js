import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, showToast, ICONS } from '../utils.js';

class UserProfilesView {
  constructor() {
    this.users = [];
  }

  async open(autoOpenKey = null) {
    const container = document.getElementById('user-profiles-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载中...</div>';
    router.pushView('user-profiles-view');
    try {
      const data = await api.fetchPrompts();
      this.users = data.users || [];
      this.defaultUser = data.default_user || 'default';
    } catch (e) { this.users = []; this.defaultUser = 'default'; }

    this.render();
    if (autoOpenKey) this.openEditor(autoOpenKey);
  }

  render() {
    const container = document.getElementById('user-profiles-content');
    if (!container) return;
    let html = `
      <div class="list-item" id="up-new" style="background:var(--surface); cursor:pointer;">
        <div class="avatar" style="background:var(--active-color);">${ICONS.plus}</div>
        <div class="info"><div class="name" style="font-weight:bold;color:var(--active-color);">新建用户角色</div></div>
      </div>
      <div class="contact-group-title" style="margin-top:12px;">已保存的角色 (${this.users.length})</div>
    `;

    this.users.forEach((u, index) => {
      const name = u.name || u.key;
      const avatar = u.avatar || getFallbackAvatar(name);
      
      // 按全局默认用户角色高亮：实心黄星；其余空心星
      const isDefault = u.key === this.defaultUser;
      const starIcon = isDefault 
        ? `<svg viewBox="0 0 24 24" width="22" height="22" fill="#ffcc00"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>`
        : `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="var(--text-secondary)" stroke-width="2"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>`;

      html += `
        <div class="list-item profile-row" data-key="${escHtml(u.key)}" style="justify-content:space-between; background:var(--surface); padding:12px 16px;">
          <div style="display:flex; align-items:center; gap:14px; flex:1; overflow:hidden;">
            <img class="avatar" style="object-fit:cover; width:45px; height:45px; border-radius:50%; margin:0;" src="${avatar}">
            <div class="name" style="font-size:16px; font-weight:500; color:var(--text); margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(name)}</div>
          </div>
          
          <div style="display:flex; align-items:center; gap:16px;">
            <div class="up-default-btn" data-key="${escHtml(u.key)}" style="display:flex; cursor:pointer; padding:4px;">
              ${starIcon}
            </div>
            <div class="up-edit-btn" data-key="${escHtml(u.key)}" style="color:var(--active-color); font-size:15px; font-weight:500; cursor:pointer; padding:4px;">编辑</div>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
    container.querySelector('#up-new').onclick = () => this.openEditor(null);

    // 事件绑定
    container.querySelectorAll('.up-default-btn').forEach(btn => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const key = btn.dataset.key;
        const r = await api.setDefaultUser(key);
        if (r && r.ok) {
          this.defaultUser = r.default_user || key;
          showToast('已设为默认角色');
          this.render();
          import('./meView.js').then(mv => mv.meView.refresh());
        } else { showToast('设置失败'); }
      };
    });

    container.querySelectorAll('.up-edit-btn').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        this.openEditor(btn.dataset.key);
      };
    });
  }

  openEditor(key) {
    import('../modals.js').then(m => m.openPromptEditor('user', key, () => {
      this.open();
      import('./meView.js').then(mv => mv.meView.refresh());
    }));
  }
}

export const userProfilesView = new UserProfilesView();