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
    this.container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载中...</div>';
    let defaultUser = 'default';
    try {
      const data = await api.fetchPrompts();
      this.users = data.users || [];
      defaultUser = data.default_user || 'default';
    } catch (e) { this.users = []; }

    // 主名片 = 默认用户角色；那排头像里把它排除掉
    const activeUser = this.users.find(u => u.key === defaultUser) || this.users[0] || {
      name: '默认用户', key: 'default', content: '暂无设定', avatar: getFallbackAvatar('User')
    };
    const mood = (activeUser.content || '暂无设定').slice(0, 30);

    // 那排头像：固定最多显示 ROW_MAX 个（排除主身份），等间距；多余的去「我的角色」管理页
    const ROW_MAX = 4;
    const rowUsers = this.users.filter(u => u.key !== activeUser.key);
    const shown = rowUsers.slice(0, ROW_MAX);
    const overflow = rowUsers.length - shown.length;

    this.container.innerHTML = `
      <!-- 1. 顶部 User 大头像（已取消编辑事件与交互手势） -->
      <div style="background:var(--surface); padding:25px 20px; display:flex; align-items:center; border-bottom:0.5px solid var(--border-color);" id="me-main-profile" data-key="${escHtml(activeUser.key)}">
        <img src="${activeUser.avatar || getFallbackAvatar(activeUser.name || activeUser.key)}" style="width:68px; height:68px; border-radius:18px; object-fit:cover; border:0.5px solid var(--border-color); box-shadow: 0 4px 12px rgba(0,0,0,0.08);">
        <div style="flex:1; margin-left:16px; overflow:hidden;">
          <div style="font-size:22px; font-weight:800; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(activeUser.name || activeUser.key)}</div>
          <div style="font-size:13px; color:var(--text-secondary); margin-top:6px; display:flex; align-items:center; gap:5px;">
            <span style="display:flex;opacity:0.7;">${ICONS.edit}</span>
            <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(mood)}</span>
          </div>
        </div>
      </div>

      <!-- 2. 用户角色列表（高度还原设计图 d7ef2bb4-c607-4790-bea1-25f3b7138148.jfif 样式） -->
      <div class="ios-group" style="margin-top:20px;">
        <div style="display:flex; justify-content:space-between; align-items:center; padding:14px 16px; border-bottom:0.5px solid var(--border-color); cursor:pointer;" id="me-roles-header">
          <span style="font-size:15px; font-weight:bold; color:var(--text);">我的角色</span>
          <span style="color:var(--text-secondary); font-size:14px; font-family:monospace;">&#10095;</span>
        </div>
        <!-- 固定个数、等间距（space-between）；多余的进「我的角色」管理页 -->
        <div style="display:flex; align-items:flex-start; justify-content:space-between; padding:16px;" id="me-profiles-row">
          ${shown.map(u => `
            <div class="profile-item-col" data-key="${escHtml(u.key)}" style="flex:0 0 auto; width:52px; margin:0; padding:0;">
              <img src="${u.avatar || getFallbackAvatar(u.name || u.key)}" style="width:52px; height:52px; border-radius:50%; object-fit:cover; border:1px solid var(--border-color);">
              <div class="name" style="font-size:11px; margin-top:6px; text-align:center;">${escHtml(u.name || u.key)}</div>
            </div>
          `).join('')}
          ${overflow > 0 ? `
            <div class="profile-item-col" id="me-more-profile" style="flex:0 0 auto; width:52px; margin:0; padding:0;">
              <div style="width:52px; height:52px; border-radius:50%; background:var(--bg); border:1px solid var(--border-color); display:flex; align-items:center; justify-content:center; color:var(--text-secondary); font-size:14px;">+${overflow}</div>
              <div class="name" style="font-size:11px; margin-top:6px; color:var(--text-secondary); text-align:center;">更多</div>
            </div>` : ''}
          <div class="profile-item-col" id="me-add-profile" style="flex:0 0 auto; width:52px; margin:0; padding:0;">
            <div class="add-btn" style="width:52px; height:52px; border-radius:50%; background:var(--bg); border:1px dashed var(--text-secondary); display:flex; align-items:center; justify-content:center; color:var(--text-secondary); font-size:24px; font-weight:300;">+</div>
            <div class="name" style="font-size:11px; margin-top:6px; color:var(--text-secondary); text-align:center;">新建</div>
          </div>
        </div>
      </div>

      <!-- 3. 下方常规设置项 -->
      <div class="ios-group">
        <div class="ios-item" id="me-lore">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.book}</span> 知识库 (Lore)</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="me-presets">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.branch}</span>预设分支</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="me-agent-prompts">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.branch}</span>Agent 提示词</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="me-memory">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.savePoint}</span>记忆存档</span>
          <span class="val">正常</span>
        </div>
      </div>
      <div class="ios-group">
        <div class="ios-item" id="me-engine">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span>引擎驱动设置</span>
          <span class="val">已连接</span>
        </div>
        <div class="ios-item" id="me-ui">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.photo}</span>界面与表现</span>
          <span class="val">自定义</span>
        </div>
      </div>
      <div style="height:40px;"></div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    // 点击“我的角色”标题栏，进入角色管理列表
    const rolesHeader = this.container.querySelector('#me-roles-header');
    if (rolesHeader) {
      rolesHeader.onclick = () => import('./userProfilesView.js').then(m => m.userProfilesView.open());
    }

    // 点击头像直接弹窗编辑该角色
    this.container.querySelectorAll('#me-profiles-row .profile-item-col[data-key]').forEach(el => {
      el.onclick = () => import('../modals.js').then(m => m.openPromptEditor('user', el.dataset.key, () => this.refresh()));
    });

    // 点击“新建”
    const addBtn = this.container.querySelector('#me-add-profile');
    if (addBtn) {
      addBtn.onclick = () => import('../modals.js').then(m => m.openPromptEditor('user', null, () => this.refresh()));
    }

    // “更多”：超出固定显示数的分身进管理页
    const moreBtn = this.container.querySelector('#me-more-profile');
    if (moreBtn) {
      moreBtn.onclick = () => import('./userProfilesView.js').then(m => m.userProfilesView.open());
    }

    // 常规绑定
    this.container.querySelector('#me-presets').onclick = () => import('./presetsView.js').then(m => m.presetsView.open());
    this.container.querySelector('#me-agent-prompts').onclick = () => import('./agentPromptsView.js').then(m => m.agentPromptsView.open());
    this.container.querySelector('#me-lore').onclick = () => import('./worldbooksView.js').then(m => m.worldbooksView.open());
    this.container.querySelector('#me-engine').onclick = () => import('./engineSettingsView.js').then(m => m.engineSettingsView.open());
    this.container.querySelector('#me-ui').onclick = () => import('./uiSettingsView.js').then(m => m.uiSettingsView.open());
  }
}

export const meView = new MeView();