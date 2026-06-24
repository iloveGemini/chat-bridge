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
    this.typingState = '';
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
      voiceBtn: document.getElementById('btn-voice'),
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
    if (this._isSyncing) return; 
    this._isSyncing = true;
    try {
      const [msgs, status] = await Promise.all([
        api.fetchMessages(this.sessionId),
        api.fetchTypingStatus(this.sessionId)
      ]);
      this.messages = Array.isArray(msgs) ? msgs : [];
      this.pending = Boolean(status.pending);
      this.typingState = status.status || status.state || '对方正在思考...';

      this.setGenerating(this.pending);
      this.render();

      let banner = document.getElementById('tooling-banner');
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'tooling-banner';
        // 改头换面：高级毛玻璃悬浮胶囊
        banner.style.cssText = 'background: var(--menu-bg, rgba(255,255,255,0.85)); color: var(--text-secondary); font-size: 13px; padding: 8px 18px; position: absolute; top: 76px; left: 50%; transform: translateX(-50%); z-index: 10; border-radius: 20px; box-shadow: 0 8px 30px rgba(0,0,0,0.12); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 0.5px solid var(--border-color); display: none; align-items: center; justify-content: center; gap: 8px; font-weight: 500; opacity: 0; transition: opacity 0.3s ease, top 0.3s ease;';
        const chatRoom = document.getElementById('chat-room');
        if (chatRoom) chatRoom.appendChild(banner);
      }
      if (this.pending && this.typingState) {
        // 加个旋转的 SVG loading 小图标，并改为“绿油油”的样式
        banner.style.background = 'rgba(46, 204, 113, 0.15)';
        banner.style.color = '#2ecc71';
        banner.style.border = '1px solid rgba(46, 204, 113, 0.3)';
        
        banner.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 2s linear infinite;"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg> <span>${escHtml(this.typingState)}</span><style>@keyframes spin { 100% { transform: rotate(360deg); } }</style>`;
        banner.style.display = 'flex';
        // 触发重绘以应用过渡动画
        requestAnimationFrame(() => { banner.style.opacity = '1'; banner.style.top = '80px'; });
      } else if (banner) {
        banner.style.opacity = '0';
        banner.style.top = '70px';
        setTimeout(() => { if (banner.style.opacity === '0') banner.style.display = 'none'; }, 300);
      }

      const last = this.messages[this.messages.length - 1];
      if (this.pending || (last && last.role === 'user')) this.startPolling();
      else this.stopPolling();
    } catch (e) { console.error("syncOnce Error:", e); } finally { this._isSyncing = false; }
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
    try { await api.interrupt(this.sessionId); } catch (e) { }
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
    this.typingState = '正在思考...';
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
    const sig = JSON.stringify(visible.map(m => [m.role, m.text, m.image])) + '|' + this.pending + '|' + this.typingState + '|' + bubbleMode + '|' + start;
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

      // 多段气泡：开启后把 AI 整段回复按空行/换行拆成多条气泡（旁白与正文分开）
      let bubblesHtml;
      if (bubbleMode && !isUser) {
        const parts = (m.text || '').split(/\n{1,}/).map(s => s.trim()).filter(Boolean);
        if (parts.length > 1) {
          bubblesHtml = parts.map((p, bi) =>
            `<div class="msg-bubble">${bi === 0 ? imgHtml : ''}${renderMarkdown(p)}</div>`).join('');
        } else {
          bubblesHtml = `<div class="msg-bubble">${imgHtml}${body}</div>`;
        }
      } else {
        bubblesHtml = `<div class="msg-bubble">${imgHtml}${body}</div>`;
      }

      // ===== 注入内嵌操作栏 =====
      // 用户：编辑、复制、更多。 AI：播放、复制、重刷、更多。
      let actionsHtml = '';
      if (isUser) {
        actionsHtml = `
          <div class="action-btn" title="编辑" data-act="edit">${ICONS.edit}</div>
          <div class="action-btn" title="复制" data-act="copy">${ICONS.copy}</div>
          <div class="action-btn" title="删除" data-act="del" style="color: var(--text-secondary); opacity: 0.7;">${ICONS.trash || '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>'}</div>
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
          ${bubblesHtml}
          <div class="msg-actions">${actionsHtml}</div>
          <div class="msg-time">${formatTime(m.ts)}</div>
        </div>
      `;
    // 移除了底部的气泡状态提示，因为上方已经有绿油油的悬浮胶囊了
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
      const text = m.text || '';
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => showToast('已复制到剪贴板')).catch(() => fallbackCopy(text));
      } else {
        fallbackCopy(text);
      }
      function fallbackCopy(t) {
        const ta = document.createElement("textarea");
        ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); showToast('已复制到剪贴板'); }
        catch (err) { showToast('复制失败，您的浏览器不支持'); }
        document.body.removeChild(ta);
      }
    } else if (act === 'tts') {
      showToast('正在生成并播放语音...');
      // 绕开 bug 包装，直接调用底层 postS
      api.postS('/api/tts', { text: m.text }, this.sessionId)
        .then(d => { if (d.ok && d.audio) new Audio(d.audio).play(); else showToast('语音生成失败'); })
        .catch(() => showToast('网络错误'));
    } else if (act === 'reroll') {
      this.rerollMessage(idx);
    } else if (act === 'edit') {
      // 【修改】内联无感编辑：直接将气泡变成纯净的自适应文本框
      const msgEl = btnEl.closest('.msg');
      const bubbles = msgEl.querySelectorAll('.msg-bubble');
      const bubbleEl = bubbles[0];
      // 多气泡时编辑期间只保留第一个气泡承载全文文本框，其余先隐藏（保存后重渲染恢复）
      for (let bi = 1; bi < bubbles.length; bi++) bubbles[bi].style.display = 'none';
      const origText = m.text || '';

      const textarea = document.createElement('textarea');
      textarea.value = origText;
      // 纯文字风格，无多余元素
      textarea.style.cssText = 'width:100%; min-height:60px; padding:0; border:none; background:transparent; color:inherit; font-size:inherit; line-height:inherit; resize:none; outline:none; font-family:inherit; box-sizing:border-box; overflow-y:hidden;';

      const adjustHeight = () => {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
      };
      textarea.addEventListener('input', adjustHeight);

      bubbleEl.innerHTML = '';
      bubbleEl.appendChild(textarea);
      adjustHeight();
      textarea.focus();

      // 失去焦点（点空白处）自动保存并恢复气泡
      textarea.addEventListener('blur', async () => {
        const newText = textarea.value.trim();
        if (newText && newText !== origText) {
          try {
            // 绕开 bug API，强制走正确参数
            await api.postS('/api/edit', { index: idx, text: newText }, this.sessionId);
            this.messages[idx].text = newText;
          } catch (e) {
            showToast('保存失败');
          }
        }
        this.render(true);
      });
    } else if (act === 'del') {
      if (!confirm('确定彻底删除这条记录吗？')) return;
      api.postS('/api/delete', { index: idx }, this.sessionId)
        .then(() => { showToast('已删除'); this.syncOnce(); })
        .catch(() => showToast('删除请求失败'));
    } else if (act === 'more') {
      this.openMoreMenu(idx, btnEl);
    }
  }

  // ========== 点击“更多(…)”弹出的折叠菜单 ==========
  openMoreMenu(idx, anchorEl) {
    const backdrop = document.createElement('div');
    backdrop.className = 'context-backdrop';
    backdrop.style.backgroundColor = 'transparent';

    const menuBox = document.createElement('div');
    menuBox.className = 'popover-box';
    menuBox.style.zIndex = '8002';
    menuBox.style.minWidth = 'auto';
    menuBox.style.display = 'flex';
    menuBox.style.padding = '10px 8px';
    menuBox.style.gap = '14px';
    menuBox.style.flexDirection = 'column';
    menuBox.style.borderRadius = '12px';
    
    // 高级毛玻璃假透明效果
    menuBox.style.background = 'var(--menu-bg, rgba(255,255,255,0.5))';
    menuBox.style.backdropFilter = 'blur(16px)';
    menuBox.style.webkitBackdropFilter = 'blur(16px)';
    menuBox.style.boxShadow = '0 8px 32px rgba(0,0,0,0.15)';
    menuBox.style.border = '0.5px solid rgba(128,128,128,0.2)';

    const rect = anchorEl.getBoundingClientRect();
    const popHeight = 160; // 竖排变高了
    if (rect.top > popHeight) {
      menuBox.style.top = (rect.top - popHeight - 8) + 'px';
    } else {
      menuBox.style.top = (rect.bottom + 8) + 'px';
    }
    menuBox.style.left = Math.min(rect.left - 20, window.innerWidth - 60) + 'px';

    const getIcon = (key) => (ICONS && ICONS[key]) ? ICONS[key] : '';

    menuBox.innerHTML = `
      <div class="action-btn" title="创建存档点" data-menu-act="savePoint">${getIcon('savePoint') || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>'}</div>
      <div class="action-btn" title="创建分支" data-menu-act="branch">${getIcon('branch') || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>'}</div>
      <div class="action-btn" title="时光回溯" data-menu-act="rewind">${getIcon('rewind') || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v5h5"></path><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"></path></svg>'}</div>
      <div class="action-btn" title="彻底删除" data-menu-act="del" style="color: var(--text-secondary); opacity: 0.7;">${getIcon('trash') || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>'}</div>
    `;
    const dismiss = () => { backdrop.remove(); menuBox.remove(); };
    backdrop.onclick = dismiss;

    menuBox.onclick = async (e) => {
      const actItem = e.target.closest('[data-menu-act]');
      if (!actItem) return;
      const act = actItem.dataset.menuAct;
      dismiss();

      if (act === 'del') {
        if (!confirm('确定彻底删除这条记录吗？')) return;
        try {
          // 绕开 bug api，使用 postS 正确传参
          await api.postS('/api/delete', { index: idx }, this.sessionId);
          showToast('已删除');
          // 删除中间的消息会导致索引移位，必须重新同步后端数组
          this.syncOnce();
        } catch (err) {
          showToast('删除请求失败');
        }
      } else if (act === 'savePoint') {
        showToast('存档点标记成功');
      } else if (act === 'branch') {
        if (!confirm('将以此处为起点的历史记录，克隆生成一个全新的平行分支会话？')) return;
        this.branchFrom(idx);
      } else if (act === 'rewind') {
        if (!confirm('确定将时空回溯到该条消息吗？\n警告：它之后的所有剧情将被彻底裁切！')) return;
        this.rewindTo(idx);
      }
    };

    document.body.appendChild(backdrop);
    document.body.appendChild(menuBox);
  }

  // ========== 重摇 ==========
  async rerollMessage(idx) {
    if (!confirm('确定要重新生成这条回复吗？\n(当前及之后的聊天将被覆盖)')) return;
    this.setGenerating(true);
    try {
      await api.postS('/api/reroll', { index: idx }, this.sessionId);
      this.messages.splice(idx);
      this.pending = true;
      this.render(true);
      this.startPolling();
    } catch (e) {
      showToast('重新生成失败');
      this.setGenerating(false);
    }
  }

  // ========== 时光回溯（修复版） ==========
  async rewindTo(idx) {
    this.setGenerating(true);
    try {
      const total = this.messages.length;
      let deleteCount = 0;
      // 必须从后往前删，否则索引会因为移位而错乱报错
      for (let i = total - 1; i > idx; i--) {
        await api.postS('/api/delete', { index: i }, this.sessionId);
        deleteCount++;
      }
      this.messages.splice(idx + 1);
      this.render(true);
      showToast(`回溯成功，已裁剪 ${deleteCount} 条未来时间线`);
    } catch (e) {
      showToast('回溯出错，可能部分消息未删除');
    } finally {
      this.setGenerating(false);
    }
  }

  // ========== 创建分支（克隆 + 裁剪后半段 + 自动跳转） ==========
  async branchFrom(idx) {
    showToast('正在开辟平行时空分支...');
    try {
      // 1. 克隆当前整个会话
      const res = await api.cloneSession(this.sessionId);
      const newSid = res.session_id || res.id;
      if (!newSid) throw new Error('未能获取分支ID');

      // 2. 在新会话中，把选中这条之后的所有消息删掉，实现真正的“从这条开始分支”
      const total = this.messages.length;
      for (let i = total - 1; i > idx; i--) {
        await api.postS('/api/delete', { index: i }, newSid);
      }

      showToast('分支开辟成功！即将跳转...');

      // 3. 自动切入新房间
      const oldTitle = document.getElementById('chat-room-title').textContent;
      const newTitle = oldTitle.includes('分支') ? oldTitle : oldTitle + ' (分支)';

      setTimeout(() => {
        this.initRoom(newSid, newTitle);
      }, 600);

    } catch (e) {
      showToast('创建分支失败');
    }
  }

  openRoomSettings() {
    if (!this.sessionId) return;
    import('./chatSettingsView.js').then(m => m.chatSettingsView.open(this.sessionId));
  }
}

export const chatView = new ChatView();
