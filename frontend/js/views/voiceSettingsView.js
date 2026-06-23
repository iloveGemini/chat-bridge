import { api } from '../api.js';
import { router } from '../router.js';
import { store } from '../store.js';
import { escHtml, showToast, ICONS } from '../utils.js';

class VoiceSettingsView {
  open() {
    router.pushView('voice-settings-view');
    this.render();
  }

  render() {
    const container = document.getElementById('voice-settings-content');
    if (!container) return;

    const cfg = store.getState().config;
    const isAuto = Boolean(cfg.ttsAuto);
    const isSkip = Boolean(cfg.ttsSkipNarration);

    container.innerHTML = `
      <div class="ios-group" style="margin-top:20px;">
        <div class="ios-item">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.voice}</span> 自动语音播报</span>
          <label class="switch"><input type="checkbox" id="vs-auto-chk" ${isAuto ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.book}</span> 仅朗读角色台词 (过滤旁白)</span>
          <label class="switch"><input type="checkbox" id="vs-skip-chk" ${isSkip ? 'checked' : ''}><span class="slider"></span></label>
        </div>
      </div>
      <div style="padding:0 25px; font-size:12px; color:var(--text-secondary); line-height:1.5;">
        开启「仅朗读台词」后，系统将自动跳过剧本中的 *动作神态* 以及成对括号内的旁白描述，仅对对话正文进行按需语音合成。
      </div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    document.getElementById('vs-auto-chk').onchange = (e) => {
      const v = e.target.checked;
      localStorage.setItem('tts_auto', v ? '1' : '0');
      store.setState({ config: { ...store.getState().config, ttsAuto: v } });
      showToast(v ? '已开启自动播报' : '已关闭自动播报');
    };

    document.getElementById('vs-skip-chk').onchange = (e) => {
      const v = e.target.checked;
      store.setState({ config: { ...store.getState().config, ttsSkipNarration: v } });
      api.post('/api/tts/option', { key: 'skip_narration', value: v }).then(() => {
        showToast(v ? '已过滤旁白' : '已完整播报');
      });
    };
  }
}

export const voiceSettingsView = new VoiceSettingsView();