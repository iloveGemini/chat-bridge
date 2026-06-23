import { router } from '../router.js';
import { api } from '../api.js';
import { showToast, ICONS } from '../utils.js';

class PluginManagerView {
  constructor() {
    this.sessionId = null;
  }

  async open(sid) {
    this.sessionId = sid;
    // 修复：指向正确的 DOM ID
    const container = document.getElementById('plugin-manager-content');
    container.innerHTML = `<div style="padding:15px; font-weight:bold; color:var(--text-secondary);">已安装工具</div><div id="plugin-list"></div>`;
    router.pushView('plugin-manager-view');
    this.render();
  }

  render() {
    const list = document.getElementById('plugin-list');
    // 逻辑：动态读取 enable 状态
    const tools = [
      { id: 'outreach', title: '主动联系', desc: '允许 AI 定时/主动联系你', enabled: true },
      { id: 'web', title: '联网检索', desc: '允许 AI 搜索实时互联网信息', enabled: false }
    ];

    list.innerHTML = tools.map(t => `
      <div class="ios-group" id="plugin-${t.id}">
        <div class="ios-item" style="border-bottom:${t.enabled ? '0.5px solid var(--border-color)' : 'none'}">
          <span class="label">${t.icon} <strong>${t.title}</strong></span>
          <label class="switch"><input type="checkbox" class="plugin-switch" ${t.enabled ? 'checked' : ''}></label>
        </div>
        <div class="plugin-config-panel ${t.enabled ? 'show' : ''}">
          <div style="padding: 0 16px 10px; font-size: 13px; color: var(--text-secondary);">${t.desc}</div>
          <div class="ios-item" style="background:var(--bg); border-top:0.5px solid var(--border-color);" data-action="config" data-id="${t.id}">
            <span class="label" style="font-size:14px; color:var(--active-color);">⚙️ 配置触发规则</span>
            <span class="val"></span>
          </div>
        </div>
      </div>
    `).join('');
    this.bindEvents(); // 确保绑定
  }
  
  bindEvents() {
    // 监听开关切换
    document.querySelectorAll('.plugin-switch').forEach(sw => {
      sw.onchange = (e) => {
        const panel = e.target.closest('.ios-group').querySelector('.plugin-config-panel');
        const item = e.target.closest('.ios-group').querySelector('.ios-item');
        if (e.target.checked) {
          panel.classList.add('show');
          item.style.borderBottom = '0.5px solid var(--border-color)';
        } else {
          panel.classList.remove('show');
          item.style.borderBottom = 'none';
        }
      };
    });

    // 绑定配置跳转：对接主动联系面板
    document.querySelectorAll('[data-action="config"]').forEach(btn => {
      btn.onclick = () => {
        // 直接调用我们之前写好的 openOutreachPanel
        import('../modals.js').then(m => m.openOutreachPanel(this.sessionId));
      };
    });
  }
}
export const pluginManagerView = new PluginManagerView();