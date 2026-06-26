// Agent 提示词预设：为每个 agent（动态从注册表读取）挑选/编辑系统提示词预设。
// 「默认」预设 = 随程序发布的 .md，删不掉；用户可新增/编辑/删除自定义预设并启用其一。
import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, showToast, ICONS } from '../utils.js';

const DEFAULT_KEY = '__default__';

class AgentPromptsView {
  constructor() {
    this.agents = [];
    this.cur = null;        // 当前详情的 agent 对象
    this.curKey = DEFAULT_KEY; // 详情里正在查看的预设 key
  }

  async open() {
    const c = document.getElementById('agent-prompts-content');
    if (c) c.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载 Agent...</div>';
    router.pushView('agent-prompts-view');
    await this.reload();
  }

  async reload() {
    try {
      const r = await api.agentsPrompts();
      this.agents = (r && r.agents) || [];
    } catch (e) { this.agents = []; }
    this.renderList();
  }

  activeLabel(a) {
    const p = (a.presets || []).find(x => x.key === a.active);
    return p ? p.label : '默认（随程序发布）';
  }

  renderList() {
    const c = document.getElementById('agent-prompts-content');
    if (!c) return;
    let html = `
      <div style="padding:14px 16px 4px;font-size:12px;color:var(--text-secondary);line-height:1.6;">
        为每个 Agent 选择启用哪份系统提示词。「默认」是随程序发布的版本；你也可以新建自定义预设并启用。列表从 Agent 注册表动态读取，以后新增 Agent 会自动出现。</div>
      <div class="contact-group-title" style="margin-top:12px;">可配置 Agent (${this.agents.length})</div>`;

    if (!this.agents.length) {
      html += `<div style="color:var(--text-secondary);font-size:13px;padding:20px;">没有可配置提示词的 Agent。</div>`;
    }
    this.agents.forEach(a => {
      const customCount = (a.presets || []).filter(p => !p.builtin).length;
      html += `
        <div class="list-item ap-row" data-agent="${escHtml(a.agent)}" style="background:var(--surface);">
          <div class="avatar" style="background:var(--surface);color:var(--text-secondary);border:0.5px solid var(--border-color);">${ICONS.branch || ''}</div>
          <div class="info">
            <div class="name">${escHtml(a.label)} <span style="font-size:11px;color:var(--text-faint);">(${escHtml(a.agent)})</span></div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">启用：${escHtml(this.activeLabel(a))}${customCount ? ' · 自定义 ' + customCount : ''}</div>
          </div>
          <span style="color:var(--active-color);font-size:14px;">配置 〉</span>
        </div>`;
    });
    c.innerHTML = html;
    c.querySelectorAll('.ap-row').forEach(el => el.onclick = () => this.openDetail(el.dataset.agent));
  }

  openDetail(agentId) {
    this.cur = this.agents.find(a => a.agent === agentId);
    if (!this.cur) return;
    this.curKey = this.cur.active || DEFAULT_KEY;
    router.pushView('agent-prompt-detail-view');
    this.renderDetail();
    this.loadContent();
  }

  renderDetail() {
    const c = document.getElementById('agent-prompt-detail-content');
    if (!c || !this.cur) return;
    const a = this.cur;
    const opts = (a.presets || []).map(p =>
      `<option value="${escHtml(p.key)}" ${p.key === this.curKey ? 'selected' : ''}>${escHtml(p.label)}${p.key === a.active ? '（启用中）' : ''}</option>`
    ).join('');
    const isBuiltin = this.curKey === DEFAULT_KEY;

    c.innerHTML = `
      <div class="ios-group" style="margin-top:16px;">
        <div class="ios-item"><span class="label">Agent</span><span class="val">${escHtml(a.label)} · ${escHtml(a.agent)}</span></div>
        <div class="ios-item"><span class="label">当前启用</span><span class="val" id="ap-active">${escHtml(this.activeLabel(a))}</span></div>
      </div>

      <div class="sheet-section-label" style="margin-top:14px;">选择预设</div>
      <select class="sheet-input" id="ap-sel" style="margin-bottom:8px;">${opts}</select>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
        <button class="p-btn" id="ap-use">设为启用</button>
        <button class="p-btn" id="ap-new">新建预设…</button>
        <button class="p-del" id="ap-del" ${isBuiltin ? 'style="display:none;"' : ''}>删除此预设</button>
      </div>

      <div class="sheet-section-label">提示词正文${isBuiltin ? '（默认只读，可「新建预设」基于它改）' : ''}</div>
      <textarea class="sheet-textarea" id="ap-content" placeholder="系统提示词正文" style="width:100%;min-height:300px;box-sizing:border-box;" ${isBuiltin ? 'readonly' : ''}>加载中...</textarea>
      <div style="display:flex;justify-content:flex-end;margin:8px 0 24px;">
        <button class="p-btn" id="ap-save" ${isBuiltin ? 'style="display:none;"' : ''}>保存修改</button>
      </div>
    `;

    c.querySelector('#ap-sel').onchange = (e) => { this.curKey = e.target.value; this.renderDetail(); this.loadContent(); };
    c.querySelector('#ap-use').onclick = () => this.selectActive();
    c.querySelector('#ap-new').onclick = () => this.newPreset();
    const delBtn = c.querySelector('#ap-del');
    if (delBtn) delBtn.onclick = () => this.deletePreset();
    const saveBtn = c.querySelector('#ap-save');
    if (saveBtn) saveBtn.onclick = () => this.saveCurrent();
  }

  async loadContent() {
    const ta = document.getElementById('ap-content');
    if (!ta) return;
    ta.value = '加载中...';
    try {
      const r = await api.agentPromptGet(this.cur.agent, this.curKey);
      ta.value = (r && r.ok) ? (r.content || '') : '';
    } catch (e) { ta.value = ''; }
  }

  async selectActive() {
    try {
      const r = await api.agentPromptSelect(this.cur.agent, this.curKey);
      if (r && r.ok) {
        this.cur.active = this.curKey === DEFAULT_KEY ? DEFAULT_KEY : this.curKey;
        showToast('已设为启用');
        this.renderDetail(); this.loadContent();
        this.reload(); // 同步列表
      } else showToast('启用失败');
    } catch (e) { showToast('启用失败'); }
  }

  async newPreset() {
    const name = (prompt('新预设名称：') || '').trim();
    if (!name) return;
    if (name === DEFAULT_KEY) { showToast('名称非法'); return; }
    const base = document.getElementById('ap-content');
    const content = base ? base.value : '';
    try {
      const r = await api.agentPromptSave(this.cur.agent, name, content);
      if (r && r.ok) {
        showToast('已创建');
        // 把新预设并入本地并切换过去
        if (!(this.cur.presets || []).some(p => p.key === name))
          this.cur.presets.push({ key: name, label: name, builtin: false });
        this.curKey = name;
        this.renderDetail(); this.loadContent();
      } else showToast((r && r.error) || '创建失败');
    } catch (e) { showToast('创建失败'); }
  }

  async saveCurrent() {
    const ta = document.getElementById('ap-content');
    if (!ta) return;
    try {
      const r = await api.agentPromptSave(this.cur.agent, this.curKey, ta.value);
      showToast(r && r.ok ? '已保存' : ((r && r.error) || '保存失败'));
    } catch (e) { showToast('保存失败'); }
  }

  async deletePreset() {
    if (this.curKey === DEFAULT_KEY) return;
    if (!confirm(`删除预设「${this.curKey}」？`)) return;
    try {
      const r = await api.agentPromptDelete(this.cur.agent, this.curKey);
      if (r && r.ok) {
        showToast('已删除');
        this.cur.presets = (this.cur.presets || []).filter(p => p.key !== this.curKey);
        if (this.cur.active === this.curKey) this.cur.active = DEFAULT_KEY;
        this.curKey = DEFAULT_KEY;
        this.renderDetail(); this.loadContent();
        this.reload();
      } else showToast('删除失败');
    } catch (e) { showToast('删除失败'); }
  }
}

export const agentPromptsView = new AgentPromptsView();
