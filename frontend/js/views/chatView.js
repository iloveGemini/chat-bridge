import { store } from '../store.js';
import { api } from '../api.js';
import { escHtml, renderMarkdown, formatTime, showToast, ICONS } from '../utils.js';

const MAX_DISPLAY = 60;

class ChatView {
  constructor() {
    this.sessionId = null;
    this.messages = [];
    this.pollTimer = null;
    this.pending = false;
    this.generating = false;
    this.pendingImage = null;
    this.maxDisplay = MAX_DISPLAY;
    this._bound = false;
    this._lastSig = '';
  }

  els() {
    return {
      scroll: document.getElementById('chat-scroll'),
      input: document.querySelector('#chat-room .chat-input'),
      send: document.querySelector('#chat-room .send-btn'),
      title: document.getElementById('chat-room-title'),
      imgBtn: document.getElementById('btn-image'),
      file: document.getElementById('file-upload'),
      preview: document.getElementById('preview-box'),
      previewImg: document.getElementById('preview-img'),
      rmImg: document.getElementById('btn-rm-img'),
      jump: document.getElementById('chat-jump-bottom')
    };
  }

  bindOnce() {
    if (this._bound) return;
    this._bound = true;
    const e = this.els();
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

    e.send.addEventListener('click', () => {
      if (this.generating) this.interruptGeneration();
      else this.onSend();
    });

    e.input.addEventListener('input', () => {
      e.input.style.height = 'auto';
      e.input.style.height = Math.min(e.input.scrollHeight, 96) + 'px';
    });

    e.input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !isMobile) { ev.preventDefault(); this.onSend(); }
    });

    e.imgBtn.addEventListener('click', () => e.file.click());
    e.file.addEventListener('change', (ev) => this.onPickImage(ev));
    e.rmImg.addEventListener('click', () => this.clearImage());
    e.jump.addEventListener('click', () => this.scrollToBottom(true));

    // 使用事件代理，接管聊天窗口内所有动作按钮的点击
    e.scroll.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.action-btn');
      if (!btn) return;
      const act = btn.dataset.act;
      const msgEl = btn.closest('.msg');
      if (!msgEl) return;
      const idx = parseInt(msgEl.dataset.msgIndex);
      this.handleMsgAction(act, idx, btn);
    });
  }

  async initRoom(sessionId, name) {
    this.bindOnce();
    this.sessionId = sessionId;
    this.maxDisplay = MAX_DISPLAY;
    this.messages = [];
    this._lastSig = '';
    store.setState({ activeSessionId: sessionId });

    const e = this.els();
    if (e.title) e.title.textContent = name;
    if (e.scroll) e.scroll.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">加载剧本中...</div>';
    
    this.clearImage();
    this._initial = true;
    await this.syncOnce();
  }

  onLeave() { this.stopPolling(); }

  openRoomSettings() {
    if (!this.sessionId) return;
    import('./chatSettingsView.js').then(m => m.chatSettingsView.open(this.sessionId));
  }

  async syncOnce() {
    if (!this.sessionId) return;
    try {
      const msgs = await api.fetchMessages(this.sessionId);
      const status = await api.fetchTypingStatus(this.sessionId);
      this.messages = Array.isArray(msgs) ? msgs : [];
      this.pending = Boolean(status.pending);
      
      this.setGenerating(this.pending);
      this.render();

      const last = this.messages[this.messages.length - 1];
      if (this.pending || (last && last.role === 'user')) this.startPolling();
      else this.stopPolling();
    } catch (e) {}
  }

  startPolling() { if (!this.pollTimer) this.pollTimer = setInterval(() => this.syncOnce(), 1500); }
  stopPolling() { if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; } }

  setGenerating(on) {
    this.generating = on;
    const e = this.els();
    if (!e.send) return;

    if (on) {
      e.send.className = 'send-btn generating-stop';
      e.send.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`;
      e.send.title = '停止生成';
    } else {
      e.send.className = 'send-btn';
      e.send.textContent = '发送';
      e.send.title = '发送';
    }
  }

  async interruptGeneration() {
    this.setGenerating(false);
    this.stopPolling();
    try { await api.interrupt(this.sessionId); } catch (e) {}
  }

  async onSend() {
    const e = this.els();
    const text = e.input.value.trim();
    if (!text && !this.pendingImage) return;

    const payload = { text };
    if (this.pendingImage) payload.image = this.pendingImage;

    this.messages.push({ role: 'user', text, image: this.pendingImage || undefined, ts: new Date().toISOString() });
    
    e.input.value = '';
    e.input.style.height = 'auto';
    this.clearImage();

    this.pending = true;
    this.setGenerating(true);
    this._lastSig = '';
    this.render();
    this.scrollToBottom(true);

    try {
      const res = await api.submitMessage(payload, this.sessionId);
      if (res.ok) this.startPolling();
      else { showToast('发送失败'); this.setGenerating(false); }
    } catch (err) { showToast('网络错误'); this.setGenerating(false); }
  }

  onPickImage(ev) {
    const file = ev.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e2) => {
      this.pendingImage = e2.target.result;
      const e = this.els();
      e.previewImg.src = this.pendingImage;
      e.preview.classList.add('show');
    };
    reader.readAsDataURL(file);
    ev.target.value = '';
  }

  clearImage() {
    this.pendingImage = null;
    const e = this.els();
    if (e.preview) e.preview.classList.remove('show');
    if (e.previewImg) e.previewImg.src = '';
  }

  render(preserveScroll = false) {
    const e = this.els();
    if (!e.scroll) return;

    const total = this.messages.length;
    const start = Math.max(0, total - this.maxDisplay);
    const visible = this.messages.slice(start);
    const bubbleMode = store.getState().config.bubbleMode;

    const sig = JSON.stringify(visible.map(m => [m.role, m.text, m.image])) + '|' + this.pending + '|' + bubbleMode + '|' + start;
    if (sig === this._lastSig && !preserveScroll) return;
    this._lastSig = sig;

    const oldTop = e.scroll.scrollTop;
    const oldH = e.scroll.scrollHeight;

    let html = '';
    if (start > 0) html += `<div class="load-more-bar">向上追溯更早剧本</div>`;

    visible.forEach((m, i) => {
      const actualIdx = start + i;
      const isUser = m.role === 'user';
      const imgHtml = m.image ? `<img src="${m.image}" class="msg-img">` : '';
      const body = isUser ? escHtml(m.text || '') : renderMarkdown(m.text || '');

      // ===== 注入内嵌操作栏 =====
      // 用户：编辑、复制、更多。 AI：播放、复制、重刷、更多。
      let actionsHtml = '';
      if (isUser) {
        actionsHtml = `
          <div class="action-btn" title="编辑" data-act="edit">${ICONS.edit}</div>
          <div class="action-btn" title="复制" data-act="copy">${ICONS.copy}</div>
          <div class="action-btn" title="更多展开" data-act="more">${ICONS.more}</div>
        `;
      } else {
        actionsHtml = `
          <div class="action-btn" title="朗读台词" data-act="tts">${ICONS.play}</div>
          <div class="action-btn" title="编辑" data-act="edit">${ICONS.edit}</div>
          <div class="action-btn" title="复制" data-act="copy">${ICONS.copy}</div>
          <div class="action-btn" title="重新生成" data-act="reroll">${ICONS.reroll}</div>
          <div class="action-btn" title="更多展开" data-act="more">${ICONS.more}</div>
        `;
      }

      html += `
        <div class="msg msg-${isUser ? 'user' : 'ai'}" data-msg-index="${actualIdx}">
          <div class="msg-bubble">${imgHtml}${body}</div>
          <div class="msg-actions">${actionsHtml}</div>
          <div class="msg-time">${formatTime(m.ts)}</div>
        </div>
      `;
    });

    if (this.pending) {
      const last = this.messages[total - 1];
      if (!last || last.role === 'user') {
        html += `
          <div class="msg msg-ai">
            <div class="msg-bubble" style="opacity:0.6;font-size:13px;">对方正在组织语言...</div>
          </div>
        `;
      }
    }

    e.scroll.innerHTML = html;

    if (this._initial) { e.scroll.scrollTop = e.scroll.scrollHeight; this._initial = false; }
    else if (preserveScroll) { e.scroll.scrollTop = oldTop + (e.scroll.scrollHeight - oldH); }
    else this.scrollToBottom();
  }

  scrollToBottom(force = false) {
    requestAnimationFrame(() => {
      const e = this.els();
      if (!e.scroll) return;
      const dist = e.scroll.scrollHeight - (e.scroll.scrollTop + e.scroll.clientHeight);
      if (force || dist < 200) e.scroll.scrollTop = e.scroll.scrollHeight;
    });
  }

  scrollToMessageIndex(msgIdx) {
    setTimeout(() => {
      const targetEl = this.els().scroll.querySelector(`.msg[data-msg-index="${msgIdx}"]`);
      if (!targetEl) { showToast('该台词超出当前可视范围，请点击顶部加载更多'); return; }
      targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      targetEl.classList.add('msg-highlight-anim');
      setTimeout(() => targetEl.classList.remove('msg-highlight-anim'), 1600);
    }, 250);
  }

  // ========== 处理气泡底部的所有动作按钮 ==========
  handleMsgAction(act, idx, btnEl) {
    const m = this.messages[idx];
    if (!m) return;

    if (act === 'copy') {
      navigator.clipboard.writeText(m.text || '').then(() => showToast('已复制到剪贴板'));
    } else if (act === 'tts') {
      api.tts({ text: m.text }, this.sessionId).then(d => { if (d.ok) new Audio(d.audio).play(); });
    } else if (act === 'reroll') {
      this.rerollMessage(idx);
    } else if (act === 'edit') {
      const newText = prompt('编辑消息：', m.text);
      if (newText !== null && newText.trim()) {
        api.editMessage(idx, newText.trim(), this.sessionId).then(() => {
          this.messages[idx].text = newText.trim();
          this.render(true);
        });
      }
    } else if (act === 'more') {
      this.openMoreMenu(idx, btnEl);
    }
  }

  // ========== 点击“更多(…)”弹出的折叠菜单 ==========
  openMoreMenu(idx, anchorEl) {
    const backdrop = document.createElement('div');
    backdrop.className = 'context-backdrop';
    backdrop.style.backgroundColor = 'transparent'; // 不再压暗全屏，更轻量

    const menuBox = document.createElement('div');
    menuBox.className = 'popover-box';
    menuBox.style.zIndex = '8002';
    menuBox.style.minWidth = '140px';

    const rect = anchorEl.getBoundingClientRect();
    // 智能定位：尽量在按钮上方弹出，如果顶部空间不够就在下方弹
    const popHeight = 180; 
    if (rect.top > popHeight) {
      menuBox.style.top = (rect.top - popHeight - 8) + 'px';
    } else {
      menuBox.style.top = (rect.bottom + 8) + 'px';
    }
    // 防止超出屏幕右侧边缘
    menuBox.style.left = Math.min(rect.left - 20, window.innerWidth - 150) + 'px';

    // 折叠区：创建存档点、分支、回溯、删除
    menuBox.innerHTML = `
      <div class="popover-item" data-menu-act="savePoint">${ICONS.savePoint} 创建存档点</div>
      <div class="popover-item" data-menu-act="branch">${ICONS.branch} 创建分支</div>
      <div class="popover-item" data-menu-act="rewind">${ICONS.rewind} 时光回溯</div>
      <div style="height:1px;background:var(--border-color);margin:4px 0;"></div>
      <div class="popover-item destructive" data-menu-act="del">${ICONS.trash} 彻底删除</div>
    `;

    const dismiss = () => { backdrop.remove(); menuBox.remove(); };
    backdrop.onclick = dismiss;

    menuBox.onclick = async (e) => {
      const actItem = e.target.closest('[data-menu-act]');
      if (!actItem) return;
      const act = actItem.dataset.menuAct;
      dismiss();

      if (act === 'del') {
        if (!confirm('确定删除这条剧本记录吗？')) return;
        await api.deleteMessage(idx, this.sessionId);
        this.messages.splice(idx, 1);
        this.render(true);
        showToast('已删除');
      } else if (act === 'savePoint') {
        showToast('📍 存档模块对接中...');
      } else if (act === 'branch') {
        showToast('🌿 平行时空分支对接中...');
      } else if (act === 'rewind') {
        if (!confirm('确定将时空回溯到该条消息吗？\n(它之后的剧本将被裁剪)')) return;
        showToast('⏳ 时光回溯对接中...');
      }
    };

    document.body.appendChild(backdrop);
    document.body.appendChild(menuBox);
  }

  async rerollMessage(idx) {
    this.setGenerating(true);
    await api.reroll(idx, this.sessionId);
    this.messages.splice(idx, 1);
    this.pending = true;
    this.render(true); 
    this.startPolling();
  }

  openRoomSettings() {
    if (!this.sessionId) return;
    import('./chatSettingsView.js').then(m => m.chatSettingsView.open(this.sessionId));
  }
}

export const chatView = new ChatView();