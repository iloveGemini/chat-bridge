import { router } from '../router.js';
import { store } from '../store.js';
import { applyTheme, currentTheme, THEME_LABELS, showToast } from '../utils.js';

class UiSettingsView {
  open() {
    router.pushView('ui-settings-view');
    this.render();
  }

  render() {
    const container = document.getElementById('ui-settings-content');
    if (!container) return;

    const theme = currentTheme();
    const isMultiBubble = Boolean(store.getState().config.bubbleMode);
    const isVconsoleEn = localStorage.getItem('vconsole_en') === '1';

    container.innerHTML = `
      <div class="ios-sec-title">视觉与排版</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item">
          <span class="label">界面主题风格</span>
          <select class="ios-select" id="us-theme">
            ${Object.entries(THEME_LABELS).map(([k, v]) => `<option value="${k}" ${k === theme ? 'selected' : ''}>${v}</option>`).join('')}
          </select>
        </div>
        <div class="ios-item">
          <span class="label">全局多段气泡排版</span>
          <label class="switch"><input type="checkbox" id="us-bubble-chk" ${isMultiBubble ? 'checked' : ''}><span class="slider"></span></label>
        </div>
      </div>

      <div class="ios-sec-title">开发者选项</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item">
          <span class="label">显示系统终端浮窗 (vConsole)</span>
          <label class="switch"><input type="checkbox" id="us-vconsole-chk" ${isVconsoleEn ? 'checked' : ''}><span class="slider"></span></label>
        </div>
      </div>
      <div style="padding:0 20px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
        开启后屏幕右下角将悬浮一个终端监控台，用于查看系统底层日志和报错追踪。
      </div>

      <div class="ios-sec-title">数据与清理</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item" id="us-clear-cache" style="justify-content:center; color:#ff3b30; font-weight:500;">
          清除前端本地缓存
        </div>
      </div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    // 主题切换
    document.getElementById('us-theme').onchange = (e) => {
      const mode = e.target.value;
      applyTheme(mode);
      store.setState({ config: { ...store.getState().config, theme: mode } });
    };

    // 气泡模式
    document.getElementById('us-bubble-chk').onchange = (e) => {
      const v = e.target.checked;
      localStorage.setItem('chat-bubble', v ? '1' : '0');
      store.setState({ config: { ...store.getState().config, bubbleMode: v } });
    };

    // VConsole 开关实时联动
    document.getElementById('us-vconsole-chk').onchange = (e) => {
      localStorage.setItem('vconsole_en', e.target.checked ? '1' : '0');
      if (window.vConsole) window.vConsole.syncVisibility();
    };

    // 清理缓存
    document.getElementById('us-clear-cache').onclick = () => {
      if (confirm('清除本地缓存？(不会删除聊天记录)')) {
        localStorage.clear();
        showToast('缓存已清理，即将刷新');
        setTimeout(() => window.location.reload(), 1500);
      }
    };
  }
}

export const uiSettingsView = new UiSettingsView();