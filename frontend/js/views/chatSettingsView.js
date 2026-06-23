import { api } from '../api.js';
import { router } from '../router.js';
import { store } from '../store.js';
import { escHtml, getFallbackAvatar, selectSheet, actionSheet, showToast, ICONS } from '../utils.js';
import { chatsView } from './chatsView.js';

class ChatSettingsView {
  constructor() {
    this.sessionId = null;
    this.sessionData = null;
    this.promptsData = {};
  }

  async open(sessionId) {
    this.sessionId = sessionId;
    const container = document.getElementById('chat-settings-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载设置中...</div>';
    router.pushView('chat-settings-view');

    try {
      this.sessionData = (store.getState().sessions || []).find(s => s.id === sessionId) || {};
      this.promptsData = await api.fetchPrompts(sessionId);
    } catch (e) {
      container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--status-off);">数据加载失败</div>';
      return;
    }

    this.render();
  }

  render() {
    const container = document.getElementById('chat-settings-content');
    if (!container) return;

    const s = this.sessionData;
    const active = this.promptsData.active || {};
    const tree = this.promptsData.tree || {};
    const charName = s.character_name || s.character || 'AI';
    const avatar = s.avatar || getFallbackAvatar(charName);
    const isPinned = Boolean(s.pinned);
    const isMultiBubble = Boolean(store.getState().config.bubbleMode);

    container.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; background:var(--surface); padding:16px 20px; border-bottom:0.5px solid var(--border-color);">
        <img src="${avatar}" style="width:52px;height:52px;border-radius:12px;object-fit:cover;">
        <div id="cs-add-member-btn" class="member-plus-btn" title="添加成员">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </div>
      </div>

      <div class="ios-group" style="margin-top:20px;">
        <div class="ios-item" id="cs-pick-user">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.userPlus}</span> 用户设定身份</span>
          <span class="val">${escHtml(active.user || '默认')}</span>
        </div>
        <div class="ios-item" id="cs-pick-preset">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.branch}</span> 对话执行预设</span>
          <span class="val">${escHtml(active.preset || '默认')}</span>
        </div>
        <div class="ios-item" id="cs-worldbooks">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.book}</span> 世界书管理</span>
          <span class="val">进入</span>
        </div>
      </div>

      <div class="ios-group">
        <div class="ios-item">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.pin}</span>置顶</span>
          <label class="switch"><input type="checkbox" id="cs-pin-chk" ${isPinned ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.bubble}</span>多段气泡</span>
          <label class="switch"><input type="checkbox" id="cs-bubble-chk" ${isMultiBubble ? 'checked' : ''}><span class="slider"></span></label>
        </div>
        <div class="ios-item" id="cs-voice-rule">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.voice}</span> 语音规则设定</span>
          <span class="val">进入</span>
        </div>
        <div class="ios-item" id="cs-tools-rule">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.plugin}</span> Tool 管理</span>
          <span class="val">主动联系等</span>
        </div>
      </div>

      <div class="ios-group">
        <div class="ios-item" id="cs-search-history">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span> 查找聊天记录</span>
          <span class="val">日历/搜索</span>
        </div>
        <div class="ios-item" id="cs-long-memory">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.database}</span> 记忆提取与简报</span>
          <span class="val">进入</span>
        </div>
        <div class="ios-item" id="cs-milestones">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.milestone}</span> 故事里程碑事件</span>
          <span class="val">查看</span>
        </div>
      </div>

      <div class="ios-group" style="margin-top:35px;">
        <div class="ios-item" id="cs-clear-btn" style="justify-content:center; color:#ff3b30; font-weight:500;">清空聊天记录</div>
      </div>
      <div class="ios-group">
        <div class="ios-item" id="cs-delete-btn" style="justify-content:center; color:#ff3b30; font-weight:500;">删除该会话</div>
      </div>
    `;

    this.bindEvents();
  }

  bindEvents() {
    const sid = this.sessionId;
    const tree = this.promptsData.tree || {};
    let presets = [];
    api.fetchPresets().then(d => presets = d.presets || []);

    // 1. 单聊拉人变群聊
    document.getElementById('cs-add-member-btn').onclick = () => {
      router.popView();
      // 这里原本调用的是 chatsView 的模态框，现在改为打开新全屏页
      import('./chatMultiSelectView.js').then(m => m.chatMultiSelectView.open());
    };

    // 2. 方案选择
    document.getElementById('cs-pick-user').onclick = () => {
      const list = (tree.user || []).map(n => ({ name: n, label: n, selected: n === this.promptsData.active?.user }));
      selectSheet('选择用户身份设定', list, {
        onSelect: async (val) => { await api.usePrompt({ user: val }, sid); this.open(sid); }
      });
    };
    document.getElementById('cs-pick-preset').onclick = () => {
      const list = presets.map(n => ({ name: n, label: n, selected: n === this.promptsData.active?.preset }));
      selectSheet('选择对话执行预设', list, {
        onSelect: async (val) => { await api.usePrompt({ preset: val }, sid); this.open(sid); }
      });
    };

    // 世界书管理（本会话）：预载随角色/用户绑定的，可勾选额外整本带入
    const wbItem = document.getElementById('cs-worldbooks');
    if (wbItem) {
      wbItem.onclick = () => {
        import('./chatWorldbooksView.js').then(m => m.chatWorldbooksView.open(sid));
      };
    }

    // 3. 置顶开关
    const pinChk = document.getElementById('cs-pin-chk');
    if (pinChk) {
      pinChk.onchange = async () => {
        const r = await api.pinSession(sid);
        if (r.ok) {
          this.sessionData.pinned = r.pinned;
          chatsView.refresh();
        }
      };
    }

    // 4. 多气泡开关
    const bubbleChk = document.getElementById('cs-bubble-chk');
    if (bubbleChk) {
      bubbleChk.onchange = (e) => {
        const v = e.target.checked;
        localStorage.setItem('chat-bubble', v ? '1' : '0');
        store.setState({ config: { ...store.getState().config, bubbleMode: v } });
      };
    }

    // 5. 绑定语音独立页面 (安全绑定，避免因 HTML 缺失导致崩溃)
    const voiceRule = document.getElementById('cs-voice-rule');
    if (voiceRule) {
      voiceRule.onclick = () => {
        import('./voiceSettingsView.js').then(m => m.voiceSettingsView.open());
      };
    }

    // 6. 允许的 Tool
    const toolsRule = document.getElementById('cs-tools-rule');
    if (toolsRule) {
      toolsRule.onclick = () => {
        import('./pluginManagerView.js').then(m => m.pluginManagerView.open(sid));
      };
    }

    // 7. 绑定日历聊天记录检索页
    const searchHistory = document.getElementById('cs-search-history');
    if (searchHistory) {
      searchHistory.onclick = () => {
        import('./chatHistorySearchView.js').then(m => m.chatHistorySearchView.open(sid));
      };
    }
    
    const longMem = document.getElementById('cs-long-memory');
    if (longMem) {
      longMem.onclick = () => {
        import('./memoryManagerView.js').then(m => m.memoryManagerView.open(sid));
      };
    }
    
    const milestones = document.getElementById('cs-milestones');
    if (milestones) {
      milestones.onclick = () => showToast('故事里程碑检索中...');
    }

    // 8. 危险操作区
    const clearBtn = document.getElementById('cs-clear-btn');
    if (clearBtn) {
      clearBtn.onclick = async () => {
        if (!confirm('确定清空当前房间的所有对话剧本吗？')) return;
        await api.clear(sid);
        showToast('记录已清空');
        router.popView();
        import('./chatView.js').then(c => { c.chatView.messages = []; c.chatView.render(); });
      };
    }

    const deleteBtn = document.getElementById('cs-delete-btn');
    if (deleteBtn) {
      deleteBtn.onclick = async () => {
        if (!confirm('确定彻底删除该对话窗口吗？')) return;
        const r = await api.deleteSession(sid);
        if (r.ok) {
          showToast('会话已删除');
          router.popView();
          router.popView(); // 退回首页
          chatsView.refresh();
        } else showToast('删除失败');
      };
    }
  }
}

export const chatSettingsView = new ChatSettingsView();