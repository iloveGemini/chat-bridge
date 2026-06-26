// 弹层集合：房间设置 / 提示词编辑 / 预设编辑 / 记忆 / 世界书 / 主动联系 / 调试
import { api } from './api.js';
import { router } from './router.js';
import { escHtml, getFallbackAvatar, showToast, selectSheet, actionSheet, panel } from './utils.js';

const VOICE_PRESETS = [
  { id: '', label: '（用全局默认音色）' },
  { id: 'Chinese (Mandarin)_Gentleman', label: '绅士男声' },
  { id: 'Chinese (Mandarin)_Lady', label: '优雅女声' },
  { id: 'female-tianmei', label: '甜美女声' },
  { id: 'female-shaonv', label: '少女音' },
  { id: 'female-yujie', label: '御姐音' },
  { id: 'female-chengshu', label: '成熟女声' },
  { id: 'male-qn-qingse', label: '青涩青年' },
  { id: 'male-qn-jingying', label: '精英青年' },
  { id: 'male-qn-badao', label: '霸道青年' },
  { id: 'presenter_male', label: '男主持' },
  { id: 'presenter_female', label: '女主持' },
  { id: 'audiobook_male_1', label: '有声书男声' },
  { id: 'audiobook_female_1', label: '有声书女声' },
];

// ========== 房间设置面板 ==========
export async function openRoomSettings(cv) {
  const sid = cv.sessionId;
  let data = {}, presets = [];
  try { data = await api.fetchPrompts(sid); presets = (await api.fetchPresets()).presets || []; }
  catch (e) { showToast('读取设定失败'); return; }
  const tree = data.tree || {}, active = data.active || {}, chars = data.characters || [];

  const p = panel('会话设定');
  const binding = {
    world: active.world || 'default',
    user: active.user || 'default',
    preset: active.preset || 'default',
  };

  const row = (label, val, onClick) =>
    `<div class="settings-item" data-row style="border-radius:10px;margin-bottom:2px;"><div class="settings-label">${escHtml(label)}</div><div class="settings-value">${escHtml(val)}</div></div>`;

  function draw() {
    p.body.innerHTML = `
      <div class="p-sec">提示词绑定</div>
      <div id="r-user">${row('用户设定', binding.user)}</div>
      <div id="r-preset">${row('预设', binding.preset)}</div>
      <div class="p-sec">高级</div>
      <div id="r-memory">${row('记忆管理', '查看 / 总结 / 编辑')}</div>
      <div id="r-lore">${row('世界书', '设定 / 触发词')}</div>
      <div id="r-outreach">${row('主动联系', '角色定时找你')}</div>
      <div id="r-debug">${row('调试日志', '上次请求')}</div>
      <div class="p-sec">危险操作</div>
      <div id="r-clear">${row('清空当前对话', '')}</div>`;

    const pick = (cat) => {
      const names = tree[cat] || [];
      selectSheet(cat === 'world' ? '世界设定' : '用户设定',
        names.map(n => ({ name: n, label: n, selected: n === binding[cat] })), {
        onSelect: async (name) => { binding[cat] = name; draw(); await api.usePrompt({ [cat]: name }, sid); showToast('已应用'); },
        onEdit: (name) => openPromptEditor(cat, name, async () => { data = await api.fetchPrompts(sid); Object.assign(tree, data.tree); }),
        onNew: () => openPromptEditor(cat, null, async () => { data = await api.fetchPrompts(sid); Object.assign(tree, data.tree); }),
      });
    };
    p.body.querySelector('#r-user').onclick = () => pick('user');
    p.body.querySelector('#r-preset').onclick = () => {
      selectSheet('预设', presets.map(n => ({ name: n, label: n, selected: n === binding.preset })), {
        onSelect: async (name) => { binding.preset = name; draw(); await api.usePrompt({ preset: name }, sid); showToast('已应用'); },
        onKebab: (name) => actionSheet([
          { label: '编辑', action: 'edit' },
          ...(name !== 'default' ? [{ label: '删除', action: 'del', destructive: true }] : []),
        ], async (act) => {
          if (act === 'edit') openPresetEditor(name, tree, async () => { presets = (await api.fetchPresets()).presets || []; });
          else if (act === 'del') { await api.deletePreset(name); presets = (await api.fetchPresets()).presets || []; showToast('已删除'); }
        }),
        onNew: () => openPresetEditor(null, tree, async () => { presets = (await api.fetchPresets()).presets || []; }),
      });
    };
    p.body.querySelector('#r-memory').onclick = () => openMemoryPanel(sid);
    p.body.querySelector('#r-lore').onclick = () => import('./views/chatWorldbooksView.js').then(m => m.chatWorldbooksView.open(sid));
    p.body.querySelector('#r-outreach').onclick = () => openOutreachPanel(sid);
    p.body.querySelector('#r-debug').onclick = () => openDebugPanel(sid);
    p.body.querySelector('#r-clear').onclick = async () => {
      if (!confirm('清空当前对话所有消息？')) return;
      const r = await api.clear(sid);
      if (r.ok) { showToast('已清空'); cv.messages = []; cv._lastSig = ''; cv.render(); }
    };
  }
  draw();
}

// ========== 提示词编辑 (全屏化) ==========
export async function openPromptEditor(category, name, onDone) {
  const isChar = category === 'character';
  let avatarData = '', voice = {};
  let content = '', displayName = '';

  const titleEl = document.getElementById('editor-title');
  const contentEl = document.getElementById('editor-content');
  const saveBtn = document.getElementById('editor-save-btn');

  titleEl.textContent = name ? `编辑${isChar ? '角色' : '用户'}资料` : `新建${isChar ? '角色' : '用户'}`;
  saveBtn.textContent = '保存';
  saveBtn.onclick = null; // 清掉上一次编辑器残留的保存处理器，避免加载期间误触

  // 先把编辑器页推上来（带加载态），再异步取数据——
  // 这样即便服务器一时繁忙，点「编辑」也会立刻出现页面而不是看起来卡死。
  contentEl.innerHTML = '<div style="padding:60px 0;text-align:center;color:var(--text-secondary);">加载中...</div>';
  router.pushView('generic-editor-view');

  if (name) {
    try {
      const d = await api.getPrompt(category, name);
      if (d.ok) {
        content = d.data.content || '';
        displayName = d.data.name || name;
        avatarData = d.data.avatar || '';
        voice = d.data.voice || {};
      }
    } catch (e) {}
  }

  // 若加载期间用户已经返回（当前栈顶不再是编辑器），就不再覆盖渲染
  if (router.history[router.history.length - 1] !== 'generic-editor-view') return;

  contentEl.innerHTML = `
    <div class="ios-sec-title">基本信息</div>
    <div class="ios-group" style="margin-top:0;">
      <div class="ios-item" id="pe-avatar-row">
        <span class="label">头像</span>
        <div style="display:flex; align-items:center; gap:10px;">
          <img id="pe-avatar-preview" src="${avatarData || getFallbackAvatar(displayName || name || (isChar ? 'C' : 'U'))}" style="width:40px; height:40px; border-radius:50%; object-fit:cover; border:0.5px solid var(--border-color);">
          <span style="color:var(--text-secondary); font-size:13px;">点击更换</span>
        </div>
        <input type="file" id="pe-avatar-file" accept="image/*" style="display:none;">
      </div>
      <div class="ios-item">
        <span class="label">名称</span>
        <input type="text" class="ios-input" id="pe-name" placeholder="请输入设定名称" value="${escHtml(displayName)}">
      </div>
    </div>

    <div class="ios-sec-title">${isChar ? '角色核心设定' : '用户核心设定'}</div>
    <div class="ios-group" style="margin-top:0; padding:12px 16px;">
      <textarea class="sheet-textarea" id="pe-content" placeholder="请输入详细的背景设定、性格特征或对话习惯..." style="width:100%; min-height:220px; border:none; background:transparent; padding:0; color:var(--text); font-size:15px; line-height:1.5; outline:none; resize:none;">${escHtml(content)}</textarea>
    </div>

    ${(name && name !== 'default') ? `
    <div class="ios-group" style="margin-top:30px; background:transparent;">
      <div id="pe-del-btn" style="text-align:center; padding:14px; background:var(--surface); border-radius:12px; color:#ff3b30; font-weight:bold; font-size:16px; cursor:pointer;">
        删除此${isChar ? '角色' : '用户'}资料
      </div>
    </div>
    ` : ''}
  `;

  // 绑定头像上传 (角色与用户资料完全打通)
  const avatarRow = contentEl.querySelector('#pe-avatar-row');
  const avatarFile = contentEl.querySelector('#pe-avatar-file');
  if (avatarRow && avatarFile) {
    avatarRow.onclick = () => avatarFile.click();
    avatarFile.onchange = (ev) => {
      const f = ev.target.files[0]; if (!f) return;
      const r = new FileReader(); 
      r.onload = (e) => { 
        avatarData = e.target.result; 
        contentEl.querySelector('#pe-avatar-preview').src = avatarData; 
      }; 
      r.readAsDataURL(f);
    };
  }

  // 保存操作
  saveBtn.onclick = async () => {
    const newName = contentEl.querySelector('#pe-name').value.trim();
    if (!newName) { showToast('名称不能为空'); return; }

    const payload = { 
      category, 
      name: name || newName, 
      content: contentEl.querySelector('#pe-content').value, 
      display_name: newName,
      avatar: avatarData 
    };

    if (name) payload.old_name = name;
    // 角色语音在「角色资料 → 语音音色」单独配置，编辑器统一为「头像+名称+核心设定」。
    // 保存时不覆盖已有 voice：仅当编辑现有角色时回填原 voice，避免被清空。
    if (isChar && voice && Object.keys(voice).length) payload.voice = voice;

    const r = await api.savePrompt(payload);
    if (r.ok) {
      showToast('已保存');
      router.popView();
      if (onDone) onDone();
    } else { showToast(r.error || '保存失败'); }
  };

  // 删除操作
  const delBtn = contentEl.querySelector('#pe-del-btn');
  if (delBtn) {
    delBtn.onclick = async () => {
      if (!confirm(`确定要删除 "${displayName || name}" 吗？`)) return;
      await api.deletePrompt(category, name);
      showToast('已删除');
      router.popView();
      if (onDone) onDone();
    };
  }
}

// ========== 预设编辑 (全屏化) ==========
export async function openPresetEditor(name, tree, onDone) {
  const titleEl = document.getElementById('editor-title');
  const contentEl = document.getElementById('editor-content');
  const saveBtn = document.getElementById('editor-save-btn');

  titleEl.textContent = name ? '编辑预设' : '新建预设';
  saveBtn.textContent = '保存';
  saveBtn.onclick = null;

  // 先推页带加载态，再异步取数据，避免点编辑看起来卡死
  contentEl.innerHTML = '<div style="padding:60px 0;text-align:center;color:var(--text-secondary);">加载中...</div>';
  router.pushView('generic-editor-view');

  const SLOT_KEYS = ['main', 'world', 'style', 'post', 'reasoning'];
  let refs = {}; SLOT_KEYS.forEach(k => refs[k] = 'default');
  let agentType = 'rp';
  let ofEnabled = new Set();   // 该预设启用的 output_format 条目
  let ofCatalog = [];          // 全局条目目录（内置 + 自定义）
  if (name) { try { const d = await api.getPreset(name); if (d.ok && d.data) { SLOT_KEYS.forEach(k => { if (d.data[k]) refs[k] = d.data[k]; }); agentType = d.data.agent_type || 'rp'; (d.data.output_format || []).forEach(k => ofEnabled.add(k)); } } catch (e) {} }
  try { ofCatalog = (await api.outputFormats()).formats || []; } catch (e) {}
  const opt = (slot, sel) => (tree[slot] || []).map(n => `<option value="${escHtml(n)}" ${n === sel ? 'selected' : ''}>${escHtml(n)}</option>`).join('');

  if (router.history[router.history.length - 1] !== 'generic-editor-view') return;

  const SLOTS = [['main', '主提示词 (main)'], ['world', '世界设定 (world·env)'], ['style', '文风 (style)'], ['post', '后续指令 (post)'], ['reasoning', '思维链脚手架 (reasoning)']];
  const slotBlock = (slot, label) => `
    <div class="sheet-section-label">${label}</div>
    <select class="sheet-input ps-slot-sel" id="ps-${slot}" data-slot="${slot}" style="margin-bottom:6px;">${opt(slot, refs[slot])}</select>
    <textarea class="sheet-textarea ps-slot-content" id="ps-${slot}-content" placeholder="该组成部分的正文内容" style="width:100%;min-height:120px;margin-bottom:6px;box-sizing:border-box;">加载中...</textarea>
    <div style="display:flex;justify-content:flex-end;margin-bottom:16px;"><button class="p-btn ps-slot-save" data-slot="${slot}">保存此段内容</button></div>`;

  const ofRows = ofCatalog.map(f => `
    <div class="ios-item" style="background:var(--bg);">
      <span class="label" style="font-size:14px;">${escHtml(f.label || f.key)}${f.custom ? ' <span style="font-size:10px;color:var(--text-faint);">自定义</span>' : ''}
        <div style="font-size:11px;color:var(--text-secondary);font-weight:normal;margin-top:2px;">${escHtml(f.desc || '')}</div></span>
      <label class="switch"><input type="checkbox" class="ps-of-chk" data-key="${escHtml(f.key)}" ${ofEnabled.has(f.key) ? 'checked' : ''}><span class="slider"></span></label>
    </div>`).join('');

  contentEl.innerHTML = `
    <div class="form-group" style="margin-bottom:12px;">
      <input type="text" class="sheet-input" id="ps-name" placeholder="预设名称" value="${escHtml(name || '')}" ${name ? 'disabled' : ''}>
    </div>
    <div class="sheet-section-label">适用方案 (agent_type)</div>
    <select class="sheet-input" id="ps-agent-type" style="margin-bottom:12px;">
      <option value="rp" ${agentType === 'rp' ? 'selected' : ''}>RP 角色扮演</option>
      <option value="coding" ${agentType === 'coding' ? 'selected' : ''}>Coding 编码代理</option>
    </select>
    <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;margin-bottom:10px;">
      选择每个组成部分用哪一份提示词；下方文本框可直接编辑该份的正文，「保存此段内容」会写回该提示词本身。
    </div>
    ${SLOTS.map(([s, l]) => slotBlock(s, l)).join('')}

    <div class="sheet-section-label">输出格式 (output_format)</div>
    <div style="font-size:12px;color:var(--text-secondary);line-height:1.5;margin-bottom:6px;">勾选这套预设默认启用的格式条目；会话可整套覆盖。</div>
    <div class="ios-group" style="margin-top:0;">${ofRows || '<div style="padding:12px 16px;color:var(--text-secondary);font-size:13px;">暂无条目</div>'}</div>

    ${(name && name !== 'default') ? `<div style="text-align:center;margin-top:20px;"><button class="edit-btn-cancel" id="ps-del" style="color:#ff3b30;background:transparent;width:100%;">删除此预设</button></div>` : ''}
  `;

  // 记录每个槽位当前加载到的显示名，保存内容时回填，避免清空 prompt 的 name 字段
  const dispNames = {};

  const loadContent = async (slot) => {
    const sel = contentEl.querySelector('#ps-' + slot);
    const ta = contentEl.querySelector('#ps-' + slot + '-content');
    if (!sel || !ta) return;
    const pname = sel.value;
    ta.value = '加载中...';
    try {
      const d = await api.getPrompt(slot, pname);
      if (d.ok && d.data) { ta.value = d.data.content || ''; dispNames[slot] = d.data.name || pname; }
      else { ta.value = ''; dispNames[slot] = pname; }
    } catch (e) { ta.value = ''; dispNames[slot] = pname; }
  };

  // 初始加载三段内容 + 切换下拉时重载
  SLOTS.forEach(([slot]) => {
    loadContent(slot);
    contentEl.querySelector('#ps-' + slot).onchange = () => loadContent(slot);
  });

  // 每段「保存此段内容」：写回对应提示词文件
  contentEl.querySelectorAll('.ps-slot-save').forEach(btn => {
    btn.onclick = async () => {
      const slot = btn.dataset.slot;
      const pname = contentEl.querySelector('#ps-' + slot).value;
      const content = contentEl.querySelector('#ps-' + slot + '-content').value;
      btn.disabled = true; btn.textContent = '保存中...';
      try {
        const r = await api.savePrompt({ category: slot, name: pname, content, display_name: dispNames[slot] || pname });
        showToast(r.ok ? '内容已保存' : (r.error || '保存失败'));
      } catch (e) { showToast('保存失败'); }
      btn.disabled = false; btn.textContent = '保存此段内容';
    };
  });

  saveBtn.onclick = async () => {
    const finalName = name || contentEl.querySelector('#ps-name').value.trim();
    if (!finalName) { showToast('请填写预设名称'); return; }
    const payload = { name: finalName };
    SLOT_KEYS.forEach(k => { payload[k] = contentEl.querySelector('#ps-' + k).value; });
    payload.agent_type = contentEl.querySelector('#ps-agent-type').value;
    payload.output_format = Array.from(contentEl.querySelectorAll('.ps-of-chk')).filter(c => c.checked).map(c => c.dataset.key);
    const r = await api.savePreset(payload);
    if (r.ok) { showToast('已保存'); router.popView(); if(onDone) onDone(); } else showToast(r.error || '保存失败');
  };

  const delBtn = contentEl.querySelector('#ps-del');
  if (delBtn) delBtn.onclick = async () => { if (!confirm('删除该预设？')) return; await api.deletePreset(name); router.popView(); if(onDone) onDone(); };
}

// ========== 记忆管理 ==========
export function openMemoryPanel(sid) {
  const p = panel('记忆管理', true);
  const draw = (d) => {
    if (!d || !d.counts) { p.body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);">读取失败</div>'; return; }
    const m = d.meta || {};
    const unsumm = (m.total_messages || 0) - (m.boundary || 0);
    let st = `已总结 ${m.boundary || 0}/${m.total_messages || 0} 条 · 事实${d.counts.facts}·事件${d.counts.events}·切片${d.counts.chunks}`;
    if (m.state === 'running') st += ' · 总结中…';
    else if (m.last_status === 'failed') st += ' · ⚠ ' + (m.last_error || '失败');
    const running = m.state === 'running';
    p.status.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);flex:1;min-width:140px;">${escHtml(st)}</div>
      <button class="p-btn mem-sum" ${running ? 'disabled style="opacity:.5;"' : ''}>${running ? '总结中…' : (unsumm > 0 ? '补总结 (' + unsumm + ')' : '重新总结')}</button>
      <button class="p-btn-g mem-refresh">刷新</button>`;
    let h = '';
    h += `<div class="p-sec">关系弧</div><textarea class="p-ta mem-ta" data-key="${escHtml(d.arc_key)}">${escHtml(d.arc)}</textarea><div style="text-align:right;margin-top:4px;"><button class="p-btn mem-save" data-key="${escHtml(d.arc_key)}">保存</button></div>`;
    h += `<div class="p-sec">近况</div><textarea class="p-ta mem-ta" data-key="${escHtml(d.session_key)}">${escHtml(d.session_summary)}</textarea><div style="text-align:right;margin-top:4px;"><button class="p-btn mem-save" data-key="${escHtml(d.session_key)}">保存</button></div>`;
    h += `<div class="p-sec">硬事实 (${d.facts.length})</div>`;
    d.facts.forEach(f => {
      h += `<div class="mem-fact" data-id="${f.id}" style="display:flex;gap:5px;align-items:center;margin:5px 0;">
        <input class="p-inp" data-f="subject" value="${escHtml(f.subject)}" style="max-width:80px;">
        <input class="p-inp" data-f="predicate" value="${escHtml(f.predicate)}" style="max-width:90px;">
        <input class="p-inp" data-f="object" value="${escHtml(f.object)}">
        <button class="p-btn mem-fact-save" style="padding:5px 8px;">✓</button><button class="p-del mem-fact-del">✕</button></div>`;
    });
    h += `<div class="p-sec">事件 (${d.events.length})</div>`;
    d.events.forEach(e => {
      h += `<div class="mem-ev" data-id="${e.id}" style="margin:8px 0;padding:8px;border:1px solid var(--border-color);border-radius:8px;">
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">[${escHtml(e.type) || '—'} / ${escHtml(e.weight) || '—'}] 重要度 ${e.importance || 3}</div>
        <textarea class="p-ta mem-ev-sum" style="min-height:40px;">${escHtml(e.summary)}</textarea>
        <div style="text-align:right;margin-top:4px;"><button class="p-btn mem-ev-save">保存</button> <button class="p-del mem-ev-del">删除</button></div></div>`;
    });
    if (!d.facts.length && !d.events.length) h += '<div style="color:var(--text-secondary);font-size:13px;padding:14px 0;">该角色还没有记忆，点上面「补总结」。</div>';
    p.body.innerHTML = h;
    wire();
  };
  const refresh = async () => { try { draw(await api.memoryOverview(sid)); } catch (e) { showToast('读取失败'); } };
  function wire() {
    p.status.querySelector('.mem-refresh').onclick = refresh;
    p.status.querySelector('.mem-sum').onclick = async (ev) => {
      if (ev.target.disabled) return;
      await api.memorySummarize(sid); showToast('开始总结…');
      for (let i = 0; i < 25; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const d = await api.memoryOverview(sid); draw(d);
        if (!d.meta || d.meta.state !== 'running') { showToast(d.meta && d.meta.last_status === 'failed' ? '总结失败' : '总结完成'); return; }
      }
    };
    p.body.querySelectorAll('.mem-save').forEach(b => b.onclick = async () => {
      const key = b.dataset.key; const ta = p.body.querySelector(`.mem-ta[data-key="${key}"]`);
      const r = await api.memoryEdit({ table: 'summaries', key, text: ta.value }, sid); showToast(r.ok ? '已保存' : '失败');
    });
    p.body.querySelectorAll('.mem-fact').forEach(row => {
      const id = +row.dataset.id;
      row.querySelector('.mem-fact-save').onclick = async () => {
        const g = f => row.querySelector(`[data-f="${f}"]`).value;
        const r = await api.memoryEdit({ table: 'facts', id, subject: g('subject'), predicate: g('predicate'), object: g('object') }, sid); showToast(r.ok ? '已保存' : '失败');
      };
      row.querySelector('.mem-fact-del').onclick = async () => { await api.memoryForget({ table: 'facts', id }, sid); showToast('已删除'); refresh(); };
    });
    p.body.querySelectorAll('.mem-ev').forEach(row => {
      const id = +row.dataset.id;
      row.querySelector('.mem-ev-save').onclick = async () => { const r = await api.memoryEdit({ table: 'events', id, summary: row.querySelector('.mem-ev-sum').value }, sid); showToast(r.ok ? '已保存' : '失败'); };
      row.querySelector('.mem-ev-del').onclick = async () => { await api.memoryForget({ table: 'events', id }, sid); showToast('已删除'); refresh(); };
    });
  }
  refresh();
}

// ========== 世界书 ==========
export function openLorePanel(sid) {
  const p = panel('世界书', true);
  p.status.innerHTML = `<div style="font-size:12px;color:var(--text-secondary);flex:1;min-width:140px;">常驻条目永远注入；其余靠触发词命中</div>
    <button class="p-btn lore-add">+ 新建</button><button class="p-btn-g lore-refresh">刷新</button>`;
  const parseKeys = s => (s || '').split(/[，,]/).map(x => x.trim()).filter(Boolean);
  const card = (e) => {
    const on = !!e.always_on;
    return `<div class="lore-item" data-id="${e.id}" style="margin:8px 0;padding:10px;border:1px solid var(--border-color);border-radius:10px;">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <input class="p-inp lore-title" value="${escHtml(e.title)}" placeholder="标题" style="font-weight:600;">
        <label style="font-size:11px;color:var(--text-secondary);white-space:nowrap;"><input type="checkbox" class="lore-on" ${on ? 'checked' : ''}>常驻</label>
        <input class="p-inp lore-pri" type="number" value="${e.priority || 0}" style="max-width:54px;flex:0 0 54px;">
      </div>
      <input class="p-inp lore-keys" value="${escHtml((e.keys || []).join('，'))}" placeholder="触发词，逗号分隔" style="width:100%;margin-bottom:6px;${on ? 'opacity:.5;' : ''}">
      <textarea class="p-ta lore-content" placeholder="设定正文">${escHtml(e.content)}</textarea>
      <div style="text-align:right;margin-top:5px;"><button class="p-btn lore-save">保存</button> <button class="p-del lore-del">删除</button></div></div>`;
  };
  const draw = (list) => {
    const ons = list.filter(e => e.always_on), keyed = list.filter(e => !e.always_on);
    let h = '';
    if (ons.length) h += `<div class="p-sec">常驻 · Tier 0 (${ons.length})</div>` + ons.map(card).join('');
    if (keyed.length) h += `<div class="p-sec">触发 · Tier 1 (${keyed.length})</div>` + keyed.map(card).join('');
    if (!list.length) h += '<div style="color:var(--text-secondary);font-size:13px;padding:14px 0;">还没有设定条目，点「+ 新建」。</div>';
    p.body.innerHTML = h; wire();
  };
  const refresh = async () => { try { draw((await api.loreList(sid)).lore || []); } catch (e) { showToast('读取失败'); } };
  function wire() {
    p.status.querySelector('.lore-refresh').onclick = refresh;
    p.status.querySelector('.lore-add').onclick = async () => { const r = await api.loreAdd({ title: '新设定', content: '', keys: [] }, sid); if (r.ok) refresh(); else showToast('新建失败'); };
    p.body.querySelectorAll('.lore-item').forEach(row => {
      const id = +row.dataset.id;
      const onBox = row.querySelector('.lore-on');
      onBox.onchange = () => { row.querySelector('.lore-keys').style.opacity = onBox.checked ? '.5' : '1'; };
      row.querySelector('.lore-save').onclick = async () => {
        const title = row.querySelector('.lore-title').value.trim(), content = row.querySelector('.lore-content').value.trim();
        if (!title || !content) { showToast('标题和正文都要填'); return; }
        const r = await api.loreUpdate({ id, title, content, keys: parseKeys(row.querySelector('.lore-keys').value), priority: +row.querySelector('.lore-pri').value || 0, always_on: onBox.checked }, sid);
        showToast(r.ok ? '已保存' : '失败');
      };
      row.querySelector('.lore-del').onclick = async () => { await api.loreDelete(id, sid); showToast('已删除'); refresh(); };
    });
  }
  refresh();
}

// ========== 输出格式（会话级覆盖预设）==========
export function openOutputFormatPanel(sid) {
  const p = panel('输出格式 · 当前会话');
  const draw = (d) => {
    const ov = d.override || { set: false, enabled: [] };
    const formats = d.formats || [];
    const enabledSet = new Set(ov.set ? ov.enabled : (d.effective || []));
    let h = `<div style="font-size:12px;color:var(--text-secondary);line-height:1.6;margin-bottom:10px;">
      默认跟随预设「${escHtml(d.preset || 'default')}」。打开「覆盖预设」后，这一整套以本会话为准。</div>
      <div class="ios-group" style="margin-top:0;">
        <div class="ios-item" style="background:var(--bg);">
          <span class="label">覆盖预设的输出格式</span>
          <label class="switch"><input type="checkbox" id="of-master" ${ov.set ? 'checked' : ''}><span class="slider"></span></label>
        </div>
      </div>
      <div class="ios-group" id="of-list" style="margin-top:10px;${ov.set ? '' : 'opacity:.5;pointer-events:none;'}">`;
    if (!formats.length) h += '<div style="padding:12px 16px;color:var(--text-secondary);font-size:13px;">暂无条目</div>';
    formats.forEach(f => {
      h += `<div class="ios-item" style="background:var(--bg);">
        <span class="label" style="font-size:14px;">${escHtml(f.label || f.key)}
          <div style="font-size:11px;color:var(--text-secondary);font-weight:normal;margin-top:2px;">${escHtml(f.desc || '')}</div></span>
        <label class="switch"><input type="checkbox" class="of-chk" data-key="${escHtml(f.key)}" ${enabledSet.has(f.key) ? 'checked' : ''}><span class="slider"></span></label>
      </div>`;
    });
    h += `</div>`;
    p.body.innerHTML = h;

    const collect = () => Array.from(p.body.querySelectorAll('.of-chk')).filter(c => c.checked).map(c => c.dataset.key);
    const save = async () => {
      const setOn = p.body.querySelector('#of-master').checked;
      const list = p.body.querySelector('#of-list');
      list.style.opacity = setOn ? '1' : '.5';
      list.style.pointerEvents = setOn ? 'auto' : 'none';
      try { await api.outputFormatSessionSet(setOn, collect(), sid); } catch (e) { showToast('保存失败'); }
    };
    p.body.querySelector('#of-master').onchange = save;
    p.body.querySelectorAll('.of-chk').forEach(c => c.onchange = save);
  };
  api.outputFormatSession(sid).then(draw).catch(() => { p.body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);">读取失败</div>'; });
}

// ========== 主动联系 ==========
export function openOutreachPanel(sid) {
  const KIND = { once: '一次', daily: '每日', interval: '周期', idle: '久未聊' };
  const MODE = { wake: '唤醒生成', push: '固定文案' };
  const p = panel('主动联系 · 当前会话');
  const fmtNext = ts => ts ? new Date(ts * 1000).toLocaleString() : '—';
  const refresh = async () => {
    let jobs = [];
    try { jobs = (await api.outreachList(sid)).jobs || []; } catch (e) {}
    let h = `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">角色可在聊天里自己排，也可在这手动加一条测试。</div>
      <div style="border:1px solid var(--border-color);border-radius:10px;padding:10px;margin-bottom:12px;display:flex;flex-direction:column;gap:6px;">
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <select class="p-inp" id="ot-kind"><option value="once">一次</option><option value="daily">每日</option><option value="interval">周期</option><option value="idle">久未聊</option></select>
          <select class="p-inp" id="ot-mode"><option value="wake">唤醒生成</option><option value="push">固定文案</option></select>
          <input class="p-inp" id="ot-when" style="flex:1;min-width:90px;" placeholder="时机：+5m / 08:30 / 180(分)">
        </div>
        <input class="p-inp" id="ot-text" placeholder="wake填事由/心情，push填固定文案">
        <div style="text-align:right;"><button class="p-btn" id="ot-add">+ 添加任务</button></div>
      </div>`;
    if (!jobs.length) h += '<div style="color:var(--text-secondary);font-size:13px;padding:10px 0;">还没有任务。</div>';
    jobs.forEach(j => {
      h += `<div class="ot-item" data-id="${j.id}" style="border:1px solid var(--border-color);border-radius:10px;padding:9px 11px;margin:7px 0;${j.enabled ? '' : 'opacity:.5;'}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <div style="font-size:13px;font-weight:600;">${KIND[j.kind] || j.kind} · ${MODE[j.mode] || j.mode}</div>
          <div><label style="font-size:11px;color:var(--text-secondary);"><input type="checkbox" class="ot-on" ${j.enabled ? 'checked' : ''}>启用</label> <button class="p-del ot-del">✕</button></div>
        </div>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:3px;">下次：${j.kind === 'idle' ? ('空闲 ' + Math.round((+j.when_spec) / 60) + ' 分触发') : fmtNext(j.next_run)}</div>
        ${(j.intention || j.content) ? `<div style="font-size:12px;margin-top:3px;">「${escHtml(j.intention || j.content)}」</div>` : ''}</div>`;
    });
    p.body.innerHTML = h;
    p.body.querySelector('#ot-add').onclick = async () => {
      const kind = p.body.querySelector('#ot-kind').value, mode = p.body.querySelector('#ot-mode').value;
      const when = p.body.querySelector('#ot-when').value.trim(), t = p.body.querySelector('#ot-text').value.trim();
      if (!when) { showToast('填一下触发时机'); return; }
      const r = await api.outreachAdd({ kind, mode, when, intention: mode === 'wake' ? t : '', content: mode === 'push' ? t : '' }, sid);
      showToast(r.ok ? '已添加' : '失败'); refresh();
    };
    p.body.querySelectorAll('.ot-item').forEach(row => {
      const id = +row.dataset.id;
      row.querySelector('.ot-del').onclick = async () => { await api.outreachDelete(id, sid); showToast('已删除'); refresh(); };
      row.querySelector('.ot-on').onchange = async (e) => { await api.outreachToggle(id, e.target.checked, sid); refresh(); };
    });
  };
  refresh();
}

// ========== 调试日志 ==========
export async function openDebugPanel(sid) {
  const p = panel('调试日志');
  let data = {};
  try { data = await api.debugLastPrompt(sid); } catch (e) {}
  if (data.error) { p.body.innerHTML = `<div style="padding:20px;color:var(--text-secondary);">${escHtml(data.error)}</div>`; return; }
  const lines = ['⏱ ' + (data.ts || ''), '🤖 ' + (data.model || ''), '─'.repeat(30)];
  (data.messages || []).forEach(m => {
    const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content).substring(0, 800);
    lines.push('\n【' + (m.role || '').toUpperCase() + '】', content);
  });
  const pre = document.createElement('pre');
  pre.style.cssText = 'white-space:pre-wrap;word-break:break-all;font-size:12px;line-height:1.6;font-family:monospace;color:var(--text);';
  pre.textContent = lines.join('\n');
  p.body.appendChild(pre);
}
