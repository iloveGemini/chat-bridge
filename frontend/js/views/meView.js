import { api } from '../api.js';
import { store } from '../store.js';
import { ICONS, getFallbackAvatar, escHtml, showToast } from '../utils.js';

class MeView {
  constructor() {
    this.container = document.getElementById('me-list');
    this.users = [];
  }

  refresh() {
    this.container = document.getElementById('me-list');
    this.render();
  }

  async render() {
    if (!this.container) return;
    this.container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载身份信息...</div>';

    try {
      const data = await api.fetchPrompts();
      this.users = data.users || [];
    } catch (e) { this.users = []; }

    // 获取当前的主身份（默认第一个）
    const activeUser = this.users[0] || { 
      name: '探索者', key: 'default', content: '暂无设定', avatar: getFallbackAvatar('User') 
    };
    const mood = (activeUser.content || '未填写身份简介').slice(0, 30);

    this.container.innerHTML = `
      <div style="background:var(--surface); padding:25px 20px; display:flex; align-items:center; border-bottom:0.5px solid var(--border-color); cursor:pointer;" id="me-main-profile" data-key="${escHtml(activeUser.key)}">
        <img src="${activeUser.avatar || getFallbackAvatar(activeUser.name || activeUser.key)}" style="width:68px; height:68px; border-radius:18px; object-fit:cover; border:0.5px solid var(--border-color); box-shadow: 0 4px 12px rgba(0,0,0,0.08);">
        <div style="flex:1; margin-left:16px; overflow:hidden;">
          <div style="font-size:22px; font-weight:800; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(activeUser.name || activeUser.key)}</div>
          <div style="font-size:13px; color:var(--text-secondary); margin-top:6px; display:flex; align-items:center; gap:5px;">
            <span style="display:flex;opacity:0.7;">${ICONS.edit}</span>
            <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(mood)}</span>
          </div>
        </div>
        <div style="color:var(--text-secondary); opacity:0.5; margin-left:10px;">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
        </div>
      </div>

      <div style="padding: 15px 20px 5px; font-weight: bold; font-size: 13px; color: var(--text-secondary); text-transform:uppercase;">我的平行分身</div>
      <div class="profile-scroll-row" id="me-profiles-row">
        ${this.users.map(u => `
          <div class="profile-item-col" data-key="${escHtml(u.key)}">
            <img src="${u.avatar || getFallbackAvatar(u.name || u.key)}">
            <div class="name">${escHtml(u.name || u.key)}</div>
          </div>
        `).join('')}
        <div class="profile-item-col" id="me-add-profile">
          <div class="add-btn">${ICONS.plus}</div>
          <div class="name" style="color:var(--text-secondary);">新建分身</div>
        </div>
      </div>

      <div class="ios-group" style="margin-top:20px;">
        <div class="ios-item" id="me-lore">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.book}</span> 全局世界书管理</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="me-presets">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.branch}</span> 对话执行预设</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="me-memory">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.savePoint}</span> 全局记忆总览</span>
          <span class="val">进入</span>
        </div>
      </div>

      <div class="ios-group">
        <div class="ios-item" id="me-engine">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span> 大模型引擎网络</span>
          <span class="val">配置</span>
        </div>
        <div class="ios-item" id="me-ui">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.photo}</span> 界面与通用设置</span>
          <span class="val">配置</span>
        </div>
      </div>
      <div style="height:40px;"></div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    // 点击主名片，直接进入全屏编辑
    const mainProfile = this.container.querySelector('#me-main-profile');
    if (mainProfile) {
      mainProfile.onclick = () => {
        import('../modals.js').then(m => m.openPromptEditor('user', mainProfile.dataset.key, () => this.refresh()));
      };
    }

    // 分身小列表管理入口
    this.container.querySelector('#me-add-profile').onclick = () => {
      import('./userProfilesView.js').then(m => m.userProfilesView.open());
    };
    this.container.querySelectorAll('.profile-item-col[data-key]').forEach(el => {
      el.onclick = () => {
        const k = el.dataset.key;
        import('./userProfilesView.js').then(m => m.userProfilesView.open(k));
      };
    });

    // 资产系统入口
    this.container.querySelector('#me-presets').onclick = () => {
      import('../modals.js').then(m => m.openPresetList());
    };
    this.container.querySelector('#me-lore').onclick = () => {
      import('./worldbooksView.js').then(m => m.worldbooksView.open());
    };

    // 硬核设置页入口
    this.container.querySelector('#me-engine').onclick = () => {
      import('./engineSettingsView.js').then(m => m.engineSettingsView.open());
    };
    this.container.querySelector('#me-ui').onclick = () => {
      import('./uiSettingsView.js').then(m => m.uiSettingsView.open());
    };
  }
}

export const meView = new MeView();