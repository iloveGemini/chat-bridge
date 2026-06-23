import { store } from '../store.js';
import { api } from '../api.js';
import { escHtml, showToast } from '../utils.js';

class SettingsFormView {
  constructor() { this.content = null; }

  async init(type, title) {
    this.type = type;
    this.content = document.getElementById('settings-view-content');
    const titleEl = document.getElementById('settings-view-title');
    if (titleEl) titleEl.textContent = title || '设置';
    if (this.content) this.content.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-secondary);">读取配置中...</div>';
    try {
      this.cfg = await api.fetchConfig();
      store.setState({ serverConfig: this.cfg });
    } catch (e) { this.cfg = {}; }
    if (type === 'api') this.renderApi();
    else if (type === 'memory') this.renderMemory();
    else if (type === 'tts') this.renderTts();
  }

  field(label, id, value, ph = '', type = 'text') {
    return `<div class="form-group"><label>${escHtml(label)}</label>
      <input type="${type}" id="${id}" value="${escHtml(value == null ? '' : value)}" placeholder="${escHtml(ph)}"></div>`;
  }
  check(label, id, checked) {
    return `<div class="form-check"><label for="${id}">${escHtml(label)}</label>
      <label class="switch"><input type="checkbox" id="${id}" ${checked ? 'checked' : ''}><span class="slider"></span></label></div>`;
  }
  val(id) { const el = document.getElementById(id); return el ? el.value.trim() : ''; }
  chk(id) { const el = document.getElementById(id); return el ? el.checked : false; }
  num(id, d) { const el = document.getElementById(id); const n = el ? parseFloat(el.value) : NaN; return isNaN(n) ? d : n; }

  renderApi() {
    const a = this.cfg.api || {}, s = this.cfg.summary_api || {}, e = this.cfg.embedding || {};
    this.content.innerHTML = `
      <div class="settings-sec-title">1. 主聊天 API</div>
      ${this.field('Base URL', 'f-api-url', a.base_url, 'https://.../v1')}
      ${this.field('API Key', 'f-api-key', a.api_key, '', 'password')}
      ${this.field('Model', 'f-api-model', a.model, '模型 ID')}
      <button class="btn-secondary" id="f-api-test">测试并拉取模型</button>
      <select id="f-api-models" style="display:none;margin-top:8px;width:100%;"></select>

      <div class="settings-sec-title">2. 独立总结 API</div>
      ${this.field('Base URL', 'f-sum-url', s.base_url)}
      ${this.field('API Key', 'f-sum-key', s.api_key, '', 'password')}
      ${this.field('Model', 'f-sum-model', s.model)}
      <button class="btn-secondary" id="f-sum-test">测试并拉取模型</button>
      <select id="f-sum-models" style="display:none;margin-top:8px;width:100%;"></select>

      <div class="settings-sec-title">3. 向量检索 Embedding</div>
      ${this.check('启用长时记忆向量化', 'f-emb-en', e.enabled)}
      <div style="height:10px;"></div>
      ${this.field('Base URL', 'f-emb-url', e.base_url)}
      ${this.field('API Key', 'f-emb-key', e.api_key, '', 'password')}
      ${this.field('Model', 'f-emb-model', e.model, 'BAAI/bge-m3')}

      <button class="btn-primary" id="f-save">保存配置</button>`;

    this.bindModelTest('f-api-test', 'f-api-url', 'f-api-key', 'f-api-model', 'f-api-models');
    this.bindModelTest('f-sum-test', 'f-sum-url', 'f-sum-key', 'f-sum-model', 'f-sum-models');
    document.getElementById('f-save').addEventListener('click', () => this.saveApi());
  }

  bindModelTest(btnId, urlId, keyId, modelId, selId) {
    const btn = document.getElementById(btnId);
    btn.addEventListener('click', async () => {
      const url = this.val(urlId); if (!url) { showToast('请先填 Base URL'); return; }
      btn.disabled = true; btn.textContent = '连接中...';
      try {
        const d = await api.testModels(url, this.val(keyId));
        if (d.ok && d.models && d.models.length) {
          showToast(`拉取到 ${d.models.length} 个模型`);
          const sel = document.getElementById(selId);
          sel.innerHTML = '<option value="">— 选择模型 —</option>' + d.models.map(m => `<option value="${escHtml(m)}">${escHtml(m)}</option>`).join('');
          sel.style.display = 'block';
          sel.onchange = () => { if (sel.value) document.getElementById(modelId).value = sel.value; };
        } else showToast('拉取失败: ' + (d.error || '无标准模型列表'));
      } catch (e) { showToast('无法连接'); }
      finally { btn.disabled = false; btn.textContent = '测试并拉取模型'; }
    });
  }

  async saveApi() {
    const payload = {
      api: { base_url: this.val('f-api-url'), api_key: this.val('f-api-key'), model: this.val('f-api-model') },
      summary_api: { base_url: this.val('f-sum-url'), api_key: this.val('f-sum-key'), model: this.val('f-sum-model') },
      embedding: { ...(this.cfg.embedding || {}), enabled: this.chk('f-emb-en'), base_url: this.val('f-emb-url'), api_key: this.val('f-emb-key'), model: this.val('f-emb-model') },
    };
    await this.doSave(payload);
  }

  renderMemory() {
    const m = this.cfg.memory || {};
    this.content.innerHTML = `
      <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;">控制记忆总结与召回的节奏。</p>
      ${this.field('附带最近轮数 (recent_rounds)', 'f-mem-recent', m.recent_rounds ?? 10, '', 'number')}
      ${this.field('每隔几条总结 (summarize_every)', 'f-mem-every', m.summarize_every ?? 20, '', 'number')}
      ${this.field('粗筛召回数 (recall_n)', 'f-mem-recall', m.recall_n ?? 30, '', 'number')}
      ${this.field('精筛注入数 (top_k)', 'f-mem-topk', m.top_k ?? 10, '', 'number')}
      <button class="btn-primary" id="f-save">保存配置</button>`;
    document.getElementById('f-save').addEventListener('click', () => this.doSave({
      memory: {
        ...(this.cfg.memory || {}),
        recent_rounds: this.num('f-mem-recent', 10), summarize_every: this.num('f-mem-every', 20),
        recall_n: this.num('f-mem-recall', 30), top_k: this.num('f-mem-topk', 10),
      }
    }));
  }

  renderTts() {
    const t = this.cfg.tts || {};
    this.content.innerHTML = `
      ${this.check('启用语音合成', 'f-tts-en', t.enabled)}
      <div style="height:10px;"></div>
      ${this.check('只读台词（跳过括号旁白）', 'f-tts-skip', t.skip_narration)}
      <div style="height:10px;"></div>
      ${this.check('收到新消息自动朗读', 'f-tts-auto', t.autoplay)}
      <div class="settings-sec-title">服务参数</div>
      ${this.field('Base URL', 'f-tts-url', t.base_url)}
      ${this.field('API Key', 'f-tts-key', t.api_key, '', 'password')}
      ${this.field('Group ID', 'f-tts-gid', t.group_id)}
      ${this.field('Model', 'f-tts-model', t.model)}
      ${this.field('默认音色 voice_id', 'f-tts-voice', t.voice_id)}
      ${this.field('语速 speed', 'f-tts-speed', t.speed ?? 1, '', 'number')}
      ${this.field('音调 pitch', 'f-tts-pitch', t.pitch ?? 0, '', 'number')}
      <button class="btn-primary" id="f-save">保存配置</button>`;
    document.getElementById('f-save').addEventListener('click', () => this.doSave({
      tts: {
        ...(this.cfg.tts || {}),
        enabled: this.chk('f-tts-en'), skip_narration: this.chk('f-tts-skip'), autoplay: this.chk('f-tts-auto'),
        base_url: this.val('f-tts-url'), api_key: this.val('f-tts-key'), group_id: this.val('f-tts-gid'),
        model: this.val('f-tts-model'), voice_id: this.val('f-tts-voice'),
        speed: this.num('f-tts-speed', 1), pitch: this.num('f-tts-pitch', 0),
      }
    }));
  }

  async doSave(payload) {
    const btn = document.getElementById('f-save');
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
    try {
      const r = await api.saveConfig(payload);
      if (r.ok) { showToast('已保存并生效'); Object.assign(this.cfg, payload); store.setState({ serverConfig: this.cfg }); }
      else showToast('保存失败');
    } catch (e) { showToast('保存失败'); }
    if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
  }
}

export const settingsFormView = new SettingsFormView();
