import { store } from './store.js';

class ApiService {
  withSid(url, sid) {
    const sessionId = sid || store.getState().activeSessionId || 'default';
    return url + (url.includes('?') ? '&' : '?') + 'session_id=' + encodeURIComponent(sessionId);
  }

  async request(endpoint, options = {}) {
    const { config } = store.getState();
    const url = (config.apiBaseUrl || '') + endpoint;
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    const token = localStorage.getItem('auth_token');
    if (token) headers['X-Auth-Token'] = token;

    const response = await fetch(url, { ...options, headers });

    if (response.status === 401 && endpoint.indexOf('/api/login') === -1) {
      if (!window._isPrompting) {
        window._isPrompting = true;
        const pwd = prompt('此系统已加锁，请输入访问口令：');
        if (pwd !== null) {
          const loginRes = await fetch((config.apiBaseUrl || '') + '/api/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd })
          });
          const loginData = await loginRes.json().catch(() => ({}));
          if (loginData.ok) {
            localStorage.setItem('auth_token', loginData.token || '');
            window.location.reload();
            return new Promise(() => {});
          } else { alert('口令错误，请重试！'); }
        }
        window._isPrompting = false;
      } else {
        return new Promise(() => {});
      }
      throw new Error('Unauthorized');
    }
    if (!response.ok) throw new Error(`API ${response.status} ${response.statusText}`);
    const text = await response.text();
    try { return JSON.parse(text); } catch (e) { return text; }
  }

  get(endpoint) { return this.request(endpoint); }
  post(endpoint, body) { return this.request(endpoint, { method: 'POST', body: JSON.stringify(body || {}) }); }
  getS(endpoint, sid) { return this.request(this.withSid(endpoint, sid)); }
  postS(endpoint, body, sid) { return this.request(this.withSid(endpoint, sid), { method: 'POST', body: JSON.stringify(body || {}) }); }

  // ---- 消息 / 聊天 ----
  fetchMessages(sid) { return this.getS('/api/messages', sid); }
  fetchTypingStatus(sid) { return this.getS('/api/typing_status', sid); }
  submitMessage(payload, sid) { return this.postS('/api/submit', payload, sid); }
  interrupt(sid) { return this.postS('/api/interrupt', {}, sid); }
  clear(sid) { return this.postS('/api/clear', {}, sid); }
  editMessage(index, text, sid) { return this.postS('/api/edit', { index, text }, sid); }
  deleteMessage(index, sid) { return this.postS('/api/delete', { index }, sid); }
  reroll(index, sid) { return this.postS('/api/reroll', { index }, sid); }
  status(sid) { return this.getS('/api/status', sid); }

  // ---- 会话 ----
  fetchSessions() { return this.get('/api/sessions/list'); }
  createSession(character) { return this.post('/api/sessions/create', { character }); }
  deleteSession(session_id) { return this.post('/api/sessions/delete', { session_id }); }
  renameSession(session_id, name) { return this.post('/api/sessions/rename', { session_id, name }); }
  cloneSession(session_id) { return this.post('/api/sessions/clone', { session_id }); }
  pinSession(session_id) { return this.post('/api/sessions/pin', { session_id }); }

  // ---- 提示词 / 角色 ----
  fetchPrompts(sid) { return this.getS('/api/prompts/list', sid); }
  getPrompt(category, name) { return this.get('/api/prompts/get?category=' + encodeURIComponent(category) + '&name=' + encodeURIComponent(name)); }
  savePrompt(payload) { return this.post('/api/prompts/save', payload); }
  deletePrompt(category, name) { return this.post('/api/prompts/delete', { category, name }); }
  usePrompt(binding, sid) { return this.postS('/api/prompts/use', binding, sid); }
  setDefaultUser(key) { return this.post('/api/prompts/set_default_user', { key }); }

  // ---- 预设 ----
  fetchPresets() { return this.get('/api/presets/list'); }
  getPreset(name) { return this.get('/api/presets/get?name=' + encodeURIComponent(name)); }
  savePreset(payload) { return this.post('/api/presets/save', payload); }
  deletePreset(name) { return this.post('/api/presets/delete', { name }); }

  // ---- 输出格式（Output_Format 条目开关集）----
  outputFormats() { return this.get('/api/output_formats'); }                         // 全局目录：内置 + 自定义
  outputFormatSave(payload) { return this.post('/api/output_formats/save', payload); } // 新增/改自定义条目
  outputFormatDelete(key) { return this.post('/api/output_formats/delete', { key }); }
  outputFormatSession(sid) { return this.getS('/api/output_format/session', sid); }    // 会话覆盖态 + 生效集 + 目录
  outputFormatSessionSet(set, enabled, sid) { return this.postS('/api/output_format/session/set', { set, enabled }, sid); }

  // ---- Agent 提示词预设（动态从 agent 注册表读取）----
  agentsPrompts() { return this.get('/api/agents/prompts'); }                                  // 列出所有可配置 agent + 其预设 + 当前启用
  agentPromptGet(agent, preset) { return this.get('/api/agent_prompt?agent=' + encodeURIComponent(agent) + '&preset=' + encodeURIComponent(preset || '')); }
  agentPromptSave(agent, name, content) { return this.post('/api/agent_prompt/save', { agent, name, content }); }
  agentPromptDelete(agent, name) { return this.post('/api/agent_prompt/delete', { agent, name }); }
  agentPromptSelect(agent, preset) { return this.post('/api/agent_prompt/select', { agent, preset }); }

  // ---- 记忆 ----
  memoryOverview(sid) { return this.getS('/api/memory/overview', sid); }
  memorySummarize(sid) { return this.postS('/api/memory/summarize', {}, sid); }
  memoryEdit(payload, sid) { return this.postS('/api/memory/edit', payload, sid); }
  memoryForget(payload, sid) { return this.postS('/api/memory/forget', payload, sid); }

  // ---- 世界书·条目（按 book_id 归属，不再绑会话）----
  loreList(bookId) { return this.get('/api/lore?book_id=' + encodeURIComponent(bookId)); }
  loreAdd(payload) { return this.post('/api/lore', payload); }       // payload 含 book_id
  loreUpdate(payload) { return this.post('/api/lore/update', payload); }
  loreDelete(id) { return this.post('/api/lore/delete', { id }); }

  // ---- 世界书·容器（独立实体：绑角色/绑用户/不绑）----
  worldbooksList() { return this.get('/api/worldbooks/list'); }
  worldbookCreate(payload) { return this.post('/api/worldbooks/create', payload); }
  worldbookUpdate(payload) { return this.post('/api/worldbooks/update', payload); }
  worldbookDelete(id) { return this.post('/api/worldbooks/delete', { id }); }
  // 某会话视角：自动并入(绑角色/用户) + 可选其它书 + 已手动挂载的 id
  worldbookSession(sid) { return this.getS('/api/worldbooks/session', sid); }
  worldbookSessionSet(ids, sid) { return this.postS('/api/worldbooks/session/set', { ids }, sid); }

  // ---- 主动联系 ----
  outreachList(sid) { return this.getS('/api/outreach', sid); }
  outreachAdd(payload, sid) { return this.postS('/api/outreach', payload, sid); }
  outreachDelete(id, sid) { return this.postS('/api/outreach/delete', { id }, sid); }
  outreachToggle(id, enabled, sid) { return this.postS('/api/outreach/toggle', { id, enabled }, sid); }

  // ---- 会话级工具授权（按会话窗口）----
  toolsGet(sid) { return this.getS('/api/tools', sid); }
  toolsSet(patch, sid) { return this.postS('/api/tools', patch, sid); }

  // ---- TTS ----
  tts(payload, sid) { return this.postS('/api/tts', payload, sid); }
  ttsOption(key, value) { return this.post('/api/tts/option', { key, value }); }

  // ---- Code Agent ----
  agentTasks() { return this.get('/api/agent/tasks'); }
  agentTask(id) { return this.get('/api/agent/task?id=' + encodeURIComponent(id)); }
  agentTurns(id, after) { return this.get('/api/agent/turns?id=' + encodeURIComponent(id) + '&after=' + (after || 0)); }
  agentCreate(payload) { return this.post('/api/agent/create', payload); }
  agentUpdate(payload) { return this.post('/api/agent/update', payload); }
  agentSend(task_id, text) { return this.post('/api/agent/send', { task_id, text }); }
  agentDelete(task_id) { return this.post('/api/agent/delete', { task_id }); }
  agentInterrupt(task_id) { return this.post('/api/agent/interrupt', { task_id }); }
  agentConfirm(task_id) { return this.post('/api/agent/confirm', { task_id }); }
  agentEnqueue(task_id, text) { return this.post('/api/agent/enqueue', { task_id, text }); }
  fetchLogs(after) { return this.get('/api/logs?after=' + (after || 0)); }
  agentLastPrompt(id) { return this.get('/api/agent/last_prompt?id=' + encodeURIComponent(id)); }
  debugLastPrompt(sid) { return this.getS('/api/debug/last_prompt', sid); }
  fsList(path) { return this.get('/api/fs/list?path=' + encodeURIComponent(path || '')); }
  agentContext(id) { return this.get('/api/agent/context?id=' + encodeURIComponent(id)); }
  agentFiles(id) { return this.get('/api/agent/files?id=' + encodeURIComponent(id)); }
  agentContextAdd(task_id, filepath, mode) { return this.post('/api/agent/context/add', { task_id, filepath, mode }); }
  agentContextRemove(task_id, filepath) { return this.post('/api/agent/context/remove', { task_id, filepath }); }

  // ---- 系统 ----
  fetchConfig() { return this.get('/api/config'); }
  saveConfig(configData) { return this.post('/api/config/save', configData); }
  testModels(base_url, api_key) { return this.post('/api/test_models', { base_url, api_key }); }
  toggleMode() { return this.post('/api/toggle_mode', {}); }
  notifyTest() { return this.post('/api/notify/test', {}); }
  debugLastPrompt(sid) { return this.getS('/api/debug/last_prompt', sid); }
}

export const api = new ApiService();
