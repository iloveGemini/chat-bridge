import { router } from '../router.js';
import { api } from '../api.js';
import { showToast, escHtml, ICONS } from '../utils.js';

class MemoryManagerView {
  constructor() {
    this.sessionId = null;
    this.isPolling = false;
    this.memoryData = null; // 缓存拉取到的记忆数据

    // 核心改进：引入读写分离状态
    this.isEditingFacts = false;
    this.isEditingEvents = false;
  }

  async open(sid) {
    this.sessionId = sid;
    const container = document.getElementById('memory-manager-content') || document.getElementById('chat-settings-content'); 
    container.innerHTML = `<div style="text-align:center; padding:50px; color:var(--text-secondary);">提取记忆中...</div>`;
    router.pushView('memory-manager-view');
    
    // 每次打开重置为只读态
    this.isEditingFacts = false;
    this.isEditingEvents = false;
    await this.refresh();
  }

  async refresh() {
    const container = document.getElementById('memory-manager-content') || document.getElementById('chat-settings-content');
    if (!container) return;

    try {
      const d = await api.memoryOverview(this.sessionId);
      if (!d || !d.counts) throw new Error('数据结构异常');
      this.memoryData = d;
      this.render(container, d);
    } catch (e) {
      container.innerHTML = `<div style="text-align:center; padding:50px; color:#ff3b30;">无法读取该角色的记忆</div>`;
    }
  }

  render(container, d) {
    const m = d.meta || {};
    const unsumm = Math.max(0, (m.total_messages || 0) - (m.boundary || 0));
    const running = m.state === 'running';

    let statusText = `已总结 ${m.boundary || 0} / ${m.total_messages || 0} 条记录`;
    if (running) statusText += ' · 提纯中...';
    else if (m.last_status === 'failed') statusText += ` · 失败: ${m.last_error || '未知'}`;

    let html = `
      <div class="ios-sec-title" style="margin-top:15px; display:flex; justify-content:space-between; align-items:center;">
        <span>记忆引擎状态</span>
        <span id="mem-manual-refresh" style="font-size:12px; font-weight:normal; color:var(--active-color); cursor:pointer;">刷新</span>
      </div>
      
      <div class="ios-group" style="padding:16px; display:flex; justify-content:space-between; align-items:center; background:var(--surface);">
        <div style="display:flex; gap:16px;">
          <div style="text-align:center;">
            <div style="font-size:20px; font-weight:700; color:var(--text);">${d.counts.events || 0}</div>
            <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">事件</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:20px; font-weight:700; color:var(--text);">${d.counts.facts || 0}</div>
            <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">事实</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:20px; font-weight:700; color:${unsumm > 0 ? '#ff9500' : 'var(--text)'};">${unsumm}</div>
            <div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">待提纯</div>
          </div>
        </div>
        <button id="mem-sum-btn" class="btn-primary" style="margin:0; width:auto; padding:0 16px; height:34px; font-size:13px; border-radius:17px; background:${running ? 'var(--bg)' : 'var(--active-color)'}; color:${running ? 'var(--text-secondary)' : '#fff'};" ${running ? 'disabled' : ''}>
          ${running ? '提纯中...' : (unsumm > 0 ? '补总结 (' + unsumm + ')' : '全量重扫')}
        </button>
      </div>
      <div style="padding: 0 16px; font-size:11px; color:var(--text-secondary); text-align:right;">${statusText}</div>

      <div class="ios-sec-title" style="margin-top:20px;">全局宏观状态</div>
      <div class="ios-group">
        <div class="ios-item" style="flex-direction:column; align-items:stretch; border-bottom:0.5px solid var(--border-color);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span class="label" style="font-size:14px; font-weight:600; display:flex; align-items:center; gap:4px;">
              <span style="color:var(--text-secondary); display:flex;">${ICONS.branch}</span> 当前关系弧
            </span>
            <span class="mem-save-btn" data-key="${escHtml(d.arc_key)}" style="font-size:13px; color:var(--active-color); cursor:pointer;">保存修改</span>
          </div>
          <textarea class="mem-ta" data-key="${escHtml(d.arc_key)}" style="width:100%; height:60px; background:var(--input-bg); border:none; border-radius:8px; padding:8px 10px; font-size:13px; color:var(--text); resize:none;">${escHtml(d.arc || '')}</textarea>
        </div>
        <div class="ios-item" style="flex-direction:column; align-items:stretch;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span class="label" style="font-size:14px; font-weight:600; display:flex; align-items:center; gap:4px;">
              <span style="color:var(--text-secondary); display:flex;">${ICONS.edit}</span> 近期简报
            </span>
            <span class="mem-save-btn" data-key="${escHtml(d.session_key)}" style="font-size:13px; color:var(--active-color); cursor:pointer;">保存修改</span>
          </div>
          <textarea class="mem-ta" data-key="${escHtml(d.session_key)}" style="width:100%; height:80px; background:var(--input-bg); border:none; border-radius:8px; padding:8px 10px; font-size:13px; color:var(--text); resize:none; line-height:1.4;">${escHtml(d.session_summary || '')}</textarea>
        </div>
      </div>

      <div class="ios-sec-title" style="margin-top:20px; display:flex; justify-content:space-between; align-items:center;">
        <span style="display:flex; align-items:center; gap:4px;">
          <span style="display:flex; color:var(--text-secondary);">${ICONS.database}</span> 硬事实图谱 (${d.facts.length})
        </span>
        <span id="toggle-facts-btn" style="color:var(--active-color); cursor:pointer; font-weight:normal; font-size:13px;">${this.isEditingFacts ? '完成编辑' : '编辑'}</span>
      </div>
      <div class="ios-group" id="facts-container"></div>

      <div class="ios-sec-title" style="margin-top:20px; display:flex; justify-content:space-between; align-items:center;">
        <span style="display:flex; align-items:center; gap:4px;">
          <span style="display:flex; color:var(--text-secondary);">${ICONS.milestone}</span> 里程碑事件 (${d.events.length})
        </span>
        <span id="toggle-events-btn" style="color:var(--active-color); cursor:pointer; font-weight:normal; font-size:13px;">${this.isEditingEvents ? '完成编辑' : '编辑'}</span>
      </div>
      <div class="ios-group" id="events-container" style="margin-bottom: 40px; background:transparent;"></div>
    `;

    container.innerHTML = html;

    // 分离渲染列表，方便独立切换状态
    this.renderFactsList();
    this.renderEventsList();
    this.bindEvents(container);
  }

  renderFactsList() {
    const container = document.getElementById('facts-container');
    if (!container) return;
    const facts = this.memoryData.facts || [];

    if (facts.length === 0) {
      container.innerHTML = '<div style="color:var(--text-faint); font-size:12px; text-align:center; padding:16px 0;">暂无记录</div>';
      return;
    }

    if (this.isEditingFacts) {
      // 📝 编辑态：渲染表单和删除按钮
      container.innerHTML = facts.map(f => `
        <div class="mem-fact-row" data-id="${f.id}" style="padding:12px 16px; border-bottom:0.5px solid var(--border-color); background:var(--surface);">
          <div style="display:flex; gap:6px; align-items:center; margin-bottom:10px;">
            <input class="fact-inp" data-f="subject" value="${escHtml(f.subject)}" style="flex:0.8; width:100%; padding:6px 8px; font-size:13px; font-weight:600; color:var(--active-color); background:var(--bg); border:none; border-radius:6px;">
            <input class="fact-inp" data-f="predicate" value="${escHtml(f.predicate)}" style="flex:1; width:100%; padding:6px 8px; font-size:13px; color:var(--text-secondary); background:var(--bg); border:none; border-radius:6px;">
            <input class="fact-inp" data-f="object" value="${escHtml(f.object)}" style="flex:1.5; width:100%; padding:6px 8px; font-size:13px; color:var(--text); background:var(--bg); border:none; border-radius:6px;">
          </div>
          <div style="display:flex; justify-content:flex-end; align-items:center; gap:16px;">
            ${f.is_state ? `<span style="font-size:10px; color:var(--text-secondary); font-weight:500; margin-right:auto; padding:2px 4px; border:0.5px solid var(--border-color); border-radius:4px; background:var(--bg);">核心设定</span>` : '<div></div>'}
            <span class="mem-fact-save" style="font-size:12px; color:var(--active-color); cursor:pointer;">保存单条</span>
            <span class="mem-fact-del" style="font-size:12px; color:#ff3b30; cursor:pointer;">删除</span>
          </div>
        </div>
      `).join('');
      this.bindListActions();
    } else {
      // 📖 只读态：极致清爽的排版
      container.innerHTML = facts.map((f, idx) => `
        <div style="padding:12px 16px; border-bottom:${idx === facts.length - 1 ? 'none' : '0.5px solid var(--border-color)'}; display:flex; align-items:flex-start; justify-content:space-between; gap:10px;">
          <div style="font-size:14px; line-height:1.5;">
            <span style="color:var(--active-color); font-weight:500;">${escHtml(f.subject)}</span>
            <span style="color:var(--text-secondary); margin:0 4px;">${escHtml(f.predicate)}</span>
            <span style="color:var(--text);">${escHtml(f.object)}</span>
          </div>
          ${f.is_state ? `<span style="flex-shrink:0; font-size:10px; color:var(--text-secondary); background:var(--bg); padding:2px 6px; border-radius:4px; border:0.5px solid var(--border-color);">核心设定</span>` : ''}
        </div>
      `).join('');
    }
  }

  renderEventsList() {
    const container = document.getElementById('events-container');
    if (!container) return;
    const events = (this.memoryData.events || []).slice().reverse();

    if (events.length === 0) {
      container.innerHTML = '<div style="color:var(--text-faint); font-size:12px; text-align:center; padding:10px 0;">暂无记录</div>';
      return;
    }

    if (this.isEditingEvents) {
      // 📝 编辑态
      container.innerHTML = events.map(e => `
        <div class="mem-ev-row" data-id="${e.id}" style="margin-bottom:12px; padding:12px; background:var(--surface); border:1px solid var(--border-color); border-radius:12px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <div style="font-size:11px; color:var(--text-secondary); font-weight:500;">
              [${escHtml(e.type) || '—'} / ${escHtml(e.weight) || '—'}] 重要度 ${e.importance || 3}
            </div>
          </div>
          <textarea class="mem-ev-sum" style="width:100%; min-height:45px; background:var(--bg); border:none; border-radius:6px; padding:8px 10px; font-size:13px; color:var(--text); resize:none; box-sizing:border-box;">${escHtml(e.summary)}</textarea>
          <div style="display:flex; justify-content:flex-end; gap:16px; margin-top:8px;">
            <span class="mem-ev-save" style="font-size:12px; color:var(--active-color); cursor:pointer;">保存单条</span>
            <span class="mem-ev-del" style="font-size:12px; color:#ff3b30; cursor:pointer;">删除</span>
          </div>
        </div>
      `).join('');
      this.bindListActions();
    } else {
      // 📖 只读态
      container.innerHTML = events.map((e) => `
        <div style="margin-bottom:12px; padding:12px 14px; background:var(--surface); border:0.5px solid var(--border-color); border-radius:12px; box-shadow:0 1px 2px rgba(0,0,0,0.02);">
          <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px;">
            <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:var(--bg); color:var(--text-secondary);">${escHtml(e.time_label) || '未知时空'}</span>
            <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:var(--bg); color:var(--text-secondary);">${escHtml(e.type) || '日常'}</span>
            <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:var(--bg); color:${e.weight === '核心' ? 'var(--status-off)' : 'var(--text)'};">${escHtml(e.weight) || '普通'}</span>
          </div>
          <div style="font-size:14px; color:var(--text); line-height:1.5;">${escHtml(e.summary)}</div>
        </div>
      `).join('');
    }
  }

  bindEvents(container) {
    const sid = this.sessionId;

    // 状态切换按钮逻辑
    container.querySelector('#toggle-facts-btn').onclick = (e) => {
      this.isEditingFacts = !this.isEditingFacts;
      e.target.textContent = this.isEditingFacts ? '完成编辑' : '编辑';
      this.renderFactsList();
    };

    container.querySelector('#toggle-events-btn').onclick = (e) => {
      this.isEditingEvents = !this.isEditingEvents;
      e.target.textContent = this.isEditingEvents ? '完成编辑' : '编辑';
      this.renderEventsList();
    };

    container.querySelector('#mem-manual-refresh').onclick = () => this.refresh();

    // 提纯与轮询
    const sumBtn = container.querySelector('#mem-sum-btn');
    if (sumBtn) {
      sumBtn.onclick = async () => {
        if (sumBtn.disabled || this.isPolling) return;
        this.isPolling = true;
        await api.memorySummarize(sid); 
        showToast('记忆提纯线程已启动...');
        
        for (let i = 0; i < 25; i++) {
          await new Promise(r => setTimeout(r, 3000));
          if (router.currentView !== 'memory-manager-view') break;
          try {
            const d = await api.memoryOverview(sid);
            this.memoryData = d;
            this.render(container, d);
            if (!d.meta || d.meta.state !== 'running') { 
              showToast(d.meta && d.meta.last_status === 'failed' ? '总结失败' : '提纯完成'); 
              break; 
            }
          } catch (e) { break; }
        }
        this.isPolling = false;
      };
    }

    // 保存宏观 Arc & Session
    container.querySelectorAll('.mem-save-btn').forEach(btn => {
      btn.onclick = async () => {
        const key = btn.dataset.key; 
        const ta = container.querySelector(`.mem-ta[data-key="${key}"]`);
        const r = await api.memoryEdit({ table: 'summaries', key, text: ta.value }, sid); 
        showToast(r.ok ? '保存成功' : '保存失败');
      };
    });
  }

  bindListActions() {
    const sid = this.sessionId;
    const container = document.getElementById('memory-manager-content') || document.getElementById('chat-settings-content');
    
    // 微观事实 (Facts) 编辑与删除
    container.querySelectorAll('.mem-fact-row').forEach(row => {
      const id = parseInt(row.dataset.id);
      const saveBtn = row.querySelector('.mem-fact-save');
      if (saveBtn) {
        saveBtn.onclick = async () => {
          const g = f => row.querySelector(`[data-f="${f}"]`).value.trim();
          const r = await api.memoryEdit({ table: 'facts', id, subject: g('subject'), predicate: g('predicate'), object: g('object') }, sid); 
          showToast(r.ok ? '事实已保存' : '保存失败');
          if (r.ok) this.refresh();
        };
      }
      const delBtn = row.querySelector('.mem-fact-del');
      if (delBtn) {
        delBtn.onclick = async () => { 
          if(!confirm('确定遗忘该事实吗？')) return;
          await api.memoryForget({ table: 'facts', id }, sid); 
          showToast('已擦除'); 
          this.refresh(); 
        };
      }
    });

    // 里程碑事件 (Events) 编辑与删除
    container.querySelectorAll('.mem-ev-row').forEach(row => {
      const id = parseInt(row.dataset.id);
      const saveBtn = row.querySelector('.mem-ev-save');
      if (saveBtn) {
        saveBtn.onclick = async () => { 
          const summary = row.querySelector('.mem-ev-sum').value.trim();
          const r = await api.memoryEdit({ table: 'events', id, summary }, sid); 
          showToast(r.ok ? '事件已保存' : '保存失败'); 
          if (r.ok) this.refresh();
        };
      }
      const delBtn = row.querySelector('.mem-ev-del');
      if (delBtn) {
        delBtn.onclick = async () => { 
          if(!confirm('确定抹除该记录吗？')) return;
          await api.memoryForget({ table: 'events', id }, sid); 
          showToast('已擦除'); 
          this.refresh(); 
        };
      }
    });
  }
}

export const memoryManagerView = new MemoryManagerView();