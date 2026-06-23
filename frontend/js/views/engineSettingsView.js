import { api } from '../api.js';
import { router } from '../router.js';
import { store } from '../store.js';
import { escHtml, showToast } from '../utils.js';

class EngineSettingsView {
  constructor() {
    this.cfg = {};
  }

  async open() {
    const container = document.getElementById('engine-settings-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">读取配置中...</div>';
    router.pushView('engine-settings-view');

    try {
      this.cfg = await api.fetchConfig();
      store.setState({ serverConfig: this.cfg });
    } catch (e) { this.cfg = {}; }

    this.render();
  }

  render() {
    const container = document.getElementById('engine-settings-content');
    if (!container) return;

    const a = this.cfg.api || {};
    const sum = this.cfg.summary_api || {};
    const e = this.cfg.embedding || {};
    const rr = this.cfg.rerank || {};
    const mem = this.cfg.memory || {};
    const tts = this.cfg.tts || {};

    // 智能解析当前的音色 ID，判断是预设还是自定义
    const ttsVoiceId = tts.voice_id || 'female-tianmei';
    const PRESETS = ['female-tianmei', 'male-qn', 'female-yujie'];
    const isCustomVoice = !PRESETS.includes(ttsVoiceId);

    container.innerHTML = `
      <div class="ios-sec-title">核心聊天模型</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item">
          <span class="label">Base URL</span>
          <input type="text" class="ios-input" id="es-api-url" value="${escHtml(a.base_url || '')}" placeholder="https://api.../v1">
        </div>
        <div class="ios-item">
          <span class="label">API Key</span>
          <input type="password" class="ios-input" id="es-api-key" value="${escHtml(a.api_key || '')}" placeholder="sk-...">
        </div>
        <div class="ios-item btn-test-models" data-target="api" style="justify-content:center; color:var(--active-color); font-weight:500;">
          连通测试并拉取模型 〉
        </div>
        <div class="ios-item">
          <span class="label">当前模型</span>
          <input type="text" class="ios-input" id="es-api-model" value="${escHtml(a.model || '')}" placeholder="输入或拉取选择">
        </div>
        <div class="ios-item sel-model-row" id="row-sel-api" style="display:none; background:var(--bg);">
          <span class="label">选择拉取的模型</span>
          <select class="ios-select" id="sel-api" style="width:50%;"></select>
        </div>
      </div>

      <div class="ios-sec-title">记忆总结模型 (Summary)</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item">
          <span class="label">Base URL</span>
          <input type="text" class="ios-input" id="es-sum-url" value="${escHtml(sum.base_url || '')}" placeholder="留空则复用上方聊天模型">
        </div>
        <div class="ios-item">
          <span class="label">API Key</span>
          <input type="password" class="ios-input" id="es-sum-key" value="${escHtml(sum.api_key || '')}" placeholder="sk-...">
        </div>
        <div class="ios-item btn-test-models" data-target="sum" style="justify-content:center; color:var(--active-color); font-weight:500;">
          连通测试并拉取模型 〉
        </div>
        <div class="ios-item">
          <span class="label">当前模型</span>
          <input type="text" class="ios-input" id="es-sum-model" value="${escHtml(sum.model || '')}" placeholder="输入或拉取选择">
        </div>
        <div class="ios-item sel-model-row" id="row-sel-sum" style="display:none; background:var(--bg);">
          <span class="label">选择拉取的模型</span>
          <select class="ios-select" id="sel-sum" style="width:50%;"></select>
        </div>
        <div class="ios-item" style="border-top: 1px solid var(--border-color);">
          <span class="label">保留会话轮数</span>
          <input type="number" class="ios-input" id="es-mem-recent" value="${mem.recent_rounds ?? 10}" placeholder="10">
        </div>
        <div class="ios-item">
          <span class="label">自动总结间隔</span>
          <input type="number" class="ios-input" id="es-mem-every" value="${mem.summarize_every ?? 20}" placeholder="20">
        </div>
      </div>

      <div class="ios-sec-title">向量与召回 (Embedding & Rerank)</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item" style="background:var(--bg);">
          <span class="label">启用向量检索库</span>
          <label class="switch"><input type="checkbox" id="es-emb-en" ${e.enabled ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item">
          <span class="label">Embedding URL</span>
          <input type="text" class="ios-input" id="es-emb-url" value="${escHtml(e.base_url || '')}" placeholder="https://api...">
        </div>
        <div class="ios-item">
          <span class="label">Embedding Key</span>
          <input type="password" class="ios-input" id="es-emb-key" value="${escHtml(e.api_key || '')}" placeholder="sk-...">
        </div>
        <div class="ios-item btn-test-models" data-target="emb" style="justify-content:center; color:var(--active-color); font-weight:500;">
          连通测试并拉取模型 〉
        </div>
        <div class="ios-item">
          <span class="label">Embedding 模型</span>
          <input type="text" class="ios-input" id="es-emb-model" value="${escHtml(e.model || 'BAAI/bge-m3')}" placeholder="BAAI/bge-m3">
        </div>
        <div class="ios-item sel-model-row" id="row-sel-emb" style="display:none; background:var(--bg);">
          <span class="label">选择拉取的模型</span>
          <select class="ios-select" id="sel-emb" style="width:50%;"></select>
        </div>
        
        <div class="ios-item" style="background:var(--bg); border-top: 1px solid var(--border-color);">
          <span class="label">启用 Rerank 混合重排</span>
          <label class="switch"><input type="checkbox" id="es-rr-en" ${rr.enabled ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item btn-test-models" data-target="rr" style="justify-content:center; color:var(--active-color); font-weight:500;">
          连通测试并拉取 Rerank 模型 〉
        </div>
        <div class="ios-item">
          <span class="label">Rerank 模型</span>
          <input type="text" class="ios-input" id="es-rr-model" value="${escHtml(rr.model || '')}" placeholder="BAAI/bge-reranker-v2-m3">
        </div>
        <div class="ios-item sel-model-row" id="row-sel-rr" style="display:none; background:var(--bg);">
          <span class="label">选择拉取的模型</span>
          <select class="ios-select" id="sel-rr" style="width:50%;"></select>
        </div>
        
        <div class="ios-item" style="border-top: 1px solid var(--border-color);">
          <span class="label">粗筛数量</span>
          <input type="number" class="ios-input" id="es-mem-recall" value="${mem.recall_n ?? 30}">
        </div>
        <div class="ios-item">
          <span class="label">精筛注入数</span>
          <input type="number" class="ios-input" id="es-mem-topk" value="${mem.top_k ?? 5}">
        </div>
      </div>

      <div class="ios-sec-title">语音合成引擎 (TTS)</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item" style="background:var(--bg);">
          <span class="label">开启 TTS 语音能力</span>
          <label class="switch"><input type="checkbox" id="es-tts-en" ${tts.enabled ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item">
          <span class="label">TTS Base URL</span>
          <input type="text" class="ios-input" id="es-tts-url" value="${escHtml(tts.base_url || '')}">
        </div>
        <div class="ios-item">
          <span class="label">TTS API Key</span>
          <input type="password" class="ios-input" id="es-tts-key" value="${escHtml(tts.api_key || '')}">
        </div>
        <div class="ios-item">
          <span class="label">Group ID (可选)</span>
          <input type="text" class="ios-input" id="es-tts-gid" value="${escHtml(tts.group_id || '')}">
        </div>
        <div class="ios-item btn-test-models" data-target="tts" style="justify-content:center; color:var(--active-color); font-weight:500;">
          连通测试并拉取模型 〉
        </div>
        <div class="ios-item">
          <span class="label">语音大模型</span>
          <input type="text" class="ios-input" id="es-tts-model" value="${escHtml(tts.model || '')}">
        </div>
        <div class="ios-item sel-model-row" id="row-sel-tts" style="display:none; background:var(--bg);">
          <span class="label">选择拉取的模型</span>
          <select class="ios-select" id="sel-tts" style="width:50%;"></select>
        </div>
        
        <div class="ios-item" style="border-top: 1px solid var(--border-color);">
          <span class="label">默认全局音色</span>
          <select class="ios-select" id="es-tts-voice-sel" style="width:50%;">
            <option value="female-tianmei" ${ttsVoiceId === 'female-tianmei' ? 'selected' : ''}>甜美女声 (tianmei)</option>
            <option value="male-qn" ${ttsVoiceId === 'male-qn' ? 'selected' : ''}>青年代入 (qn)</option>
            <option value="female-yujie" ${ttsVoiceId === 'female-yujie' ? 'selected' : ''}>成熟御姐 (yujie)</option>
            <option value="custom" ${isCustomVoice ? 'selected' : ''}>手动输入 ID...</option>
          </select>
        </div>
        <div class="ios-item" id="es-tts-voice-custom-row" style="display:${isCustomVoice ? 'flex' : 'none'}; background:var(--bg);">
          <span class="label">自定义音色ID</span>
          <input type="text" class="ios-input" id="es-tts-voice" value="${escHtml(ttsVoiceId)}">
        </div>
        
        <div class="ios-item" id="es-tts-test-play" style="justify-content:center; color:#34c759; font-weight:500;">
          保存配置并试听当前音色
        </div>
      </div>

      <div style="padding: 25px 15px 50px;">
        <button class="btn-primary" id="es-save-btn-bottom" style="width:100%; height:48px; font-size:16px;">保存全部引擎配置</button>
      </div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const chk = (id) => { const el = document.getElementById(id); return el ? el.checked : false; };
    const num = (id, def) => { const el = document.getElementById(id); const n = el ? parseFloat(el.value) : NaN; return isNaN(n) ? def : n; };

    // 1. 代理所有拉取模型的点击事件
    document.querySelectorAll('.btn-test-models').forEach(btn => {
      btn.onclick = async () => {
        const target = btn.dataset.target; // 'api', 'sum', 'emb', 'rr', 'tts'
        
        // Rerank 默认复用 Embedding 的 URL 和 Key 进行测试
        const urlId = target === 'rr' ? 'es-emb-url' : `es-${target}-url`;
        const keyId = target === 'rr' ? 'es-emb-key' : `es-${target}-key`;
        const modelId = `es-${target}-model`;
        
        const url = val(urlId);
        if (!url) { showToast('请先填写对应的 Base URL'); return; }
        
        btn.textContent = '连接中...';
        btn.style.opacity = '0.5';
        
        try {
          const d = await api.testModels(url, val(keyId));
          if (d.ok && d.models && d.models.length) {
            showToast(`拉取到 ${d.models.length} 个模型`);
            const selRow = document.getElementById(`row-sel-${target}`);
            const sel = document.getElementById(`sel-${target}`);
            sel.innerHTML = '<option value="">— 点击选择 —</option>' + d.models.map(m => `<option value="${escHtml(m)}">${escHtml(m)}</option>`).join('');
            selRow.style.display = 'flex';
            sel.onchange = () => { if (sel.value) document.getElementById(modelId).value = sel.value; };
          } else {
            showToast('拉取失败: ' + (d.error || '未返回标准模型列表'));
          }
        } catch (e) { showToast('连接失败，请检查网络和URL'); }
        
        btn.textContent = '连通测试并拉取模型 〉';
        btn.style.opacity = '1';
      };
    });

    // 2. 音色下拉框逻辑
    const voiceSel = document.getElementById('es-tts-voice-sel');
    const voiceCustomRow = document.getElementById('es-tts-voice-custom-row');
    const voiceInput = document.getElementById('es-tts-voice');

    voiceSel.onchange = () => {
      if (voiceSel.value === 'custom') {
        voiceCustomRow.style.display = 'flex';
        voiceInput.focus();
      } else {
        voiceCustomRow.style.display = 'none';
        voiceInput.value = voiceSel.value;
      }
    };

    // 3. 核心保存逻辑（提取为公共函数，支持静默保存）
    const doSave = async (popView = true) => {
      const payload = {
        api: { base_url: val('es-api-url'), api_key: val('es-api-key'), model: val('es-api-model') },
        summary_api: { base_url: val('es-sum-url'), api_key: val('es-sum-key'), model: val('es-sum-model') },
        embedding: { enabled: chk('es-emb-en'), base_url: val('es-emb-url'), api_key: val('es-emb-key'), model: val('es-emb-model') },
        rerank: { enabled: chk('es-rr-en'), model: val('es-rr-model') },
        memory: {
          recent_rounds: num('es-mem-recent', 10), summarize_every: num('es-mem-every', 20),
          recall_n: num('es-mem-recall', 30), top_k: num('es-mem-topk', 5)
        },
        tts: {
          ...this.cfg.tts, // 保留其他参数
          enabled: chk('es-tts-en'), base_url: val('es-tts-url'), api_key: val('es-tts-key'), 
          group_id: val('es-tts-gid'), model: val('es-tts-model'), voice_id: val('es-tts-voice')
        }
      };
      
      try {
        const r = await api.saveConfig(payload);
        if (r.ok) { 
          if (popView) { showToast('引擎配置已保存并生效'); router.popView(); }
          return true;
        } else {
          showToast('保存失败'); return false;
        }
      } catch (e) { showToast('保存失败'); return false; }
    };

    // 绑定右上角(若有)和底部的巨大保存按钮
    const topBtn = document.getElementById('es-save-btn');
    if (topBtn) topBtn.onclick = () => doSave(true);
    document.getElementById('es-save-btn-bottom').onclick = () => {
      document.getElementById('es-save-btn-bottom').textContent = '保存中...';
      doSave(true);
    };

    // 4. 一键试听逻辑
    document.getElementById('es-tts-test-play').onclick = async () => {
      showToast('正在应用配置...');
      const saved = await doSave(false); // 静默保存，不退出页面
      if (!saved) return;
      
      showToast('正在合成测试音频...');
      try {
        const d = await api.tts({ text: "你好呀，这是我现在的声音，听起来感觉怎么样？" });
        if (d && d.ok) { 
          new Audio(d.audio).play(); 
          showToast('试听成功！');
        } else {
          showToast('试听失败: ' + (d.error || '可能是密钥或模型名称错误'));
        }
      } catch (e) { showToast('试听网络错误'); }
    };
  }
}

export const engineSettingsView = new EngineSettingsView();