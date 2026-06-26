import { store } from "../store.js";
import { api } from "../api.js";
import {
  escHtml,
  renderMarkdown,
  formatTime,
  showToast,
  ICONS,
} from "../utils.js";

const MAX_DISPLAY = 60;

class ChatView {
  constructor() {
    this.sessionId = null;
    this.messages = [];
    this.pollTimer = null;
    this.pending = false;
    this.typingState = "";
    this.generating = false;
    this.pendingImage = null;
    this.maxDisplay = MAX_DISPLAY;
    this._bound = false;
    this._lastSig = "";

    // 模块化渲染：按消息 type 分派到对应渲染器（ARCHITECTURE §1 type→renderer 注册表）。
    // 每个渲染器签名 (m, ctx) => html 字符串；未命中 type 回落 default（普通气泡）。
    this.RENDERERS = {
      reasoning: this._renderReasoning,
      tool_call: this._renderToolCall,
      tool_result: this._renderToolResult,
      default: this._renderNormal,
    };
  }

  // 分派入口：选中对应 type 的渲染器并执行（this 透传给渲染器方法）
  renderBubbles(m, ctx) {
    const r = this.RENDERERS[m.type] || this.RENDERERS.default;
    return r.call(this, m, ctx);
  }

  _renderReasoning(m) {
    return `<details class="system-panel">
          <summary class="panel-summary">
            <div class="tool-title">Thought progress</div>
          </summary>
          <div class="panel-content">${renderMarkdown(m.text || "")}</div>
        </details>`;
  }

  _renderToolCall(m, ctx) {
    const isRunning = ctx.isRunning;
    // 需求确认卡片 (ask_user_clarification)
    if (m.tool_name === "ask_user_clarification") {
      const args = m.tool_args || {};
      const question = args.question || "请确认接下来的操作：";
      const options = args.options || [];
      const rec = args.recommended || "";
      const optsHtml = options
        .map(
          (opt) => `
            <div class="clarify-option ${opt === rec ? "recommended" : ""}" data-opt="${escHtml(opt)}">
              ${opt === rec ? "👉 " : ""}${escHtml(opt)}
            </div>
          `,
        )
        .join("");
      return `
            <div class="msg-bubble clarification-card" style="border: 1px solid var(--border-color); background: var(--bg-secondary);">
              <div class="card-title" style="font-weight: bold; margin-bottom: 8px;">📝 需要确认</div>
              <div class="card-question" style="font-size: 14px; margin-bottom: 12px;">${renderMarkdown(question)}</div>
              <div class="card-options" style="display: flex; flex-direction: column; gap: 8px;">${optsHtml}</div>
            </div>
          `;
    }
    // 常规工具调用面板
    const statusHtml = isRunning
      ? `<span class="tool-status">Executing... ▼</span>`
      : `<span class="tool-status done">Completed ▼</span>`;
    return `<details class="system-panel" ${isRunning ? "open" : ""}>
            <summary class="panel-summary">
              <div class="tool-title"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg> Call Tool: ${m.tool_name}</div>
              ${statusHtml}
            </summary>
            <div class="panel-content"><pre>${escHtml(JSON.stringify(m.tool_args, null, 2))}</pre></div>
          </details>`;
  }

  _renderToolResult(m) {
    return `<details class="system-panel">
          <summary class="panel-summary">
            <div class="tool-title"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Tool Result</div>
          </summary>
          <div class="panel-content"><pre>${escHtml(m.text || "")}</pre></div>
        </details>`;
  }

  _renderNormal(m, ctx) {
    const { isUser, imgHtml, body, bubbleMode } = ctx;
    // 多段气泡模式：仅当非用户、不含代码块时，按连续双换行拆段，避免破坏 Markdown
    if (bubbleMode && !isUser && !(m.text || "").includes("```")) {
      const parts = (m.text || "")
        .split(/\n{2,}/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (parts.length > 1) {
        return parts
          .map(
            (p, bi) =>
              `<div class="msg-bubble">${bi === 0 ? imgHtml : ""}${renderMarkdown(p)}</div>`,
          )
          .join("");
      }
      return `<div class="msg-bubble">${imgHtml}${body}</div>`;
    }
    return `<div class="msg-bubble">${imgHtml}${body}</div>`;
  }

  els() {
    return {
      scroll: document.getElementById("chat-scroll"),
      input: document.querySelector("#chat-room .chat-input"),
      send: document.querySelector("#chat-room .send-btn"),
      title: document.getElementById("chat-room-title"),
      imgBtn: document.getElementById("btn-image"),
      voiceBtn: document.getElementById("btn-voice"),
      file: document.getElementById("file-upload"),
      preview: document.getElementById("preview-box"),
      previewImg: document.getElementById("preview-img"),
      rmImg: document.getElementById("btn-rm-img"),
      jump: document.getElementById("chat-jump-bottom"),
    };
  }

  bindOnce() {
    if (this._bound) return;
    this._bound = true;
    const e = this.els();
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

    e.send.addEventListener("click", () => {
      if (this.generating) this.interruptGeneration();
      else this.onSend();
    });

    /*     e.input.addEventListener("input", () => {
      e.input.style.height = "auto";
      e.input.style.height = Math.min(e.input.scrollHeight, 120) + "px";
    }); */

    e.input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !isMobile) {
        ev.preventDefault();
        if (!this.generating) this.onSend();
      }
    });

    e.imgBtn.addEventListener("click", () => e.file.click());
    e.file.addEventListener("change", (ev) => this.onPickImage(ev));
    e.rmImg.addEventListener("click", () => this.clearImage());
    e.jump.addEventListener("click", () => this.scrollToBottom(true));

    // 全局事件代理，支持动作按钮和需求确认卡片点击
    e.scroll.addEventListener("click", (ev) => {
      // 处理动作按钮
      const btn = ev.target.closest(".action-btn");
      if (btn) {
        const act = btn.dataset.act;
        const msgEl = btn.closest(".msg");
        if (!msgEl) return;
        const idx = parseInt(msgEl.dataset.msgIndex);
        this.handleMsgAction(act, idx, btn);
        return;
      }

      // 处理需求确认卡片 (Clarification Card) 点击
      const clarifyBtn = ev.target.closest(".clarify-option");
      if (clarifyBtn) {
        const optValue = clarifyBtn.dataset.opt;
        if (optValue) {
          e.input.value = optValue;
          this.onSend(); // 直接发送用户的选择
        }
      }
    });
  }

  async initRoom(sessionId, name) {
    this.bindOnce();
    this.sessionId = sessionId;
    this.maxDisplay = MAX_DISPLAY;
    this.messages = [];
    this._lastSig = "";
    store.setState({ activeSessionId: sessionId });

    const e = this.els();
    if (e.title) e.title.textContent = name;
    if (e.scroll)
      e.scroll.innerHTML =
        '<div style="text-align:center;padding:20px;color:var(--text-secondary);">加载剧本中...</div>';

    this.clearImage();
    this._initial = true;
    await this.syncOnce();
  }

  onLeave() {
    this.stopPolling();
  }

  openRoomSettings() {
    if (!this.sessionId) return;
    import("./chatSettingsView.js").then((m) =>
      m.chatSettingsView.open(this.sessionId),
    );
  }

  async syncOnce() {
    if (!this.sessionId) return;
    if (this._isSyncing) return;
    this._isSyncing = true;
    try {
      const [msgs, status] = await Promise.all([
        api.fetchMessages(this.sessionId),
        api.fetchTypingStatus(this.sessionId),
      ]);
      this.messages = Array.isArray(msgs) ? msgs : [];
      this.pending = Boolean(status.pending);
      this.typingState = status.status || status.state || "对方正在思考...";

      this.setGenerating(this.pending);
      this.render();

      const last = this.messages[this.messages.length - 1];
      if (this.pending || (last && last.role === "user")) this.startPolling();
      else this.stopPolling();
    } catch (e) {
      console.error("syncOnce Error:", e);
    } finally {
      this._isSyncing = false;
    }
  }

  startPolling() {
    if (!this.pollTimer)
      this.pollTimer = setInterval(() => this.syncOnce(), 1500);
  }
  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  setGenerating(on) {
    this.generating = on;
    const e = this.els();
    if (!e.send) return;

    if (on) {
      e.send.className = "send-btn generating-stop";
      e.send.innerHTML = `<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`;
      e.send.title = "停止生成";
    } else {
      e.send.className = "send-btn";
      e.send.innerHTML = `<svg viewBox="0 0 24 24" width="30" height="30" fill="currentColor"><path d="M12 2c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3zm-6.5 3c-1.38 0-2.5 1.12-2.5 2.5s1.12 2.5 2.5 2.5 2.5-1.12 2.5-2.5-1.12-2.5-2.5-2.5zm13 0c-1.38 0-2.5 1.12-2.5 2.5s1.12 2.5 2.5 2.5 2.5-1.12 2.5-2.5-1.12-2.5-2.5-2.5zM12 10c-3.5 0-6.5 2.5-7.5 6-.5 1.5.5 3 2 3h11c1.5 0 2.5-1.5 2-3-1-3.5-4-6-7.5-6z"></path></svg>`;
      e.send.title = "发送";
    }
  }

  async interruptGeneration() {
    this.setGenerating(false);
    this.stopPolling();
    try {
      await api.interrupt(this.sessionId);
    } catch (e) {}
  }

  async onSend() {
    const e = this.els();
    const text = e.input.value.trim();
    if (!text && !this.pendingImage) return;

    const payload = { text };
    if (this.pendingImage) payload.image = this.pendingImage;

    this.messages.push({
      role: "user",
      text,
      image: this.pendingImage || undefined,
      ts: new Date().toISOString(),
    });

    // 彻底清空输入框并重置高度
    e.input.value = "";
    e.input.style.height = "auto";
    this.clearImage();

    this.pending = true;
    this.typingState = "正在思考...";
    this.setGenerating(true);
    this._lastSig = "";
    this.render();
    this.scrollToBottom(true);

    try {
      const res = await api.submitMessage(payload, this.sessionId);
      if (res.ok) this.startPolling();
      else {
        showToast("发送失败");
        this.setGenerating(false);
      }
    } catch (err) {
      showToast("网络错误");
      this.setGenerating(false);
    }
  }

  // 修复：重新启用 Canvas 进行图片压缩
  onPickImage(ev) {
    const file = ev.target.files[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      showToast("请选择图片文件");
      return;
    }

    const reader = new FileReader();
    reader.onload = (e2) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        const MAX_SIZE = 1200; // 自动压缩最大尺寸
        let w = img.width;
        let h = img.height;

        // 等比缩放
        if (w > MAX_SIZE || h > MAX_SIZE) {
          if (w > h) {
            h = Math.round((h * MAX_SIZE) / w);
            w = MAX_SIZE;
          } else {
            w = Math.round((w * MAX_SIZE) / h);
            h = MAX_SIZE;
          }
        }

        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);

        // 压缩为 JPEG，质量 0.85
        this.pendingImage = canvas.toDataURL("image/jpeg", 0.85);

        const e = this.els();
        e.previewImg.src = this.pendingImage;
        e.preview.classList.add("show");
      };
      img.src = e2.target.result;
    };
    reader.readAsDataURL(file);
    ev.target.value = "";
  }

  clearImage() {
    this.pendingImage = null;
    const e = this.els();
    if (e.preview) e.preview.classList.remove("show");
    if (e.previewImg) e.previewImg.src = "";
  }

  _getIcon(key, fallbackSvg) {
    return ICONS && ICONS[key] ? ICONS[key] : fallbackSvg;
  }

  render(preserveScroll = false) {
    const e = this.els();
    if (!e.scroll) return;

    const total = this.messages.length;
    const start = Math.max(0, total - this.maxDisplay);
    const visible = this.messages.slice(start);
    const bubbleMode = store.getState().config.bubbleMode;

    // 修复：将 tool_args 加入签名，防止工具参数改变时 UI 不刷新
    const sig =
      JSON.stringify(
        visible.map((m) => [
          m.role,
          m.text,
          m.image,
          m.type,
          m.tool_name,
          m.tool_args,
        ]),
      ) +
      "|" +
      this.pending +
      "|" +
      this.typingState +
      "|" +
      bubbleMode +
      "|" +
      start;

    if (sig === this._lastSig && !preserveScroll) return;
    this._lastSig = sig;

    const oldTop = e.scroll.scrollTop;
    const oldH = e.scroll.scrollHeight;

    let html = "";
    if (start > 0) html += `<div class="load-more-bar">向上追溯更早剧本</div>`;

    visible.forEach((m, i) => {
      const actualIdx = start + i;
      const isUser = m.role === "user";
      const isReasoning = m.type === "reasoning";
      const isToolCall = m.type === "tool_call";
      const isToolResult = m.type === "tool_result";

      const alignClass = isUser ? "right msg-user" : "left msg-ai";
      const imgHtml = m.image
        ? `<img src="${escHtml(m.image)}" class="msg-img">`
        : "";
      const body = isUser
        ? escHtml(m.text || "")
        : renderMarkdown(m.text || "");

      // 模块化分派：按 type 选渲染器（reasoning / tool_call / tool_result / default）
      const isRunning = this.pending && i === visible.length - 1;
      const bubblesHtml = this.renderBubbles(m, {
        isUser,
        imgHtml,
        body,
        bubbleMode,
        isRunning,
      });

      let actionsHtml = "";
      if (!isReasoning && !isToolCall && !isToolResult) {
        if (isUser) {
          actionsHtml = `
            <div class="action-btn" title="编辑" data-act="edit">${this._getIcon("edit", "✏️")}</div>
            <div class="action-btn" title="复制" data-act="copy">${this._getIcon("copy", "📋")}</div>
            <div class="action-btn" title="删除" data-act="del" style="color: var(--text-secondary); opacity: 0.7;">${this._getIcon("trash", "🗑️")}</div>
          `;
        } else {
          actionsHtml = `
            <div class="action-btn" title="朗读台词" data-act="tts">${this._getIcon("play", "▶️")}</div>
            <div class="action-btn" title="编辑" data-act="edit">${this._getIcon("edit", "✏️")}</div>
            <div class="action-btn" title="复制" data-act="copy">${this._getIcon("copy", "📋")}</div>
            <div class="action-btn" title="重新生成" data-act="reroll">${this._getIcon("reroll", "🔄")}</div>
            <div class="action-btn" title="更多展开" data-act="more">${this._getIcon("more", "···")}</div>
          `;
        }
      }

      html += `
        <div class="msg ${alignClass}" data-msg-index="${actualIdx}">
          <div class="msg-content" style="${isReasoning || isToolCall || isToolResult ? "width: 100%;" : ""}">
            ${bubblesHtml}
            ${actionsHtml ? `<div class="msg-actions">${actionsHtml}</div>` : ""}
          </div>
          ${isReasoning || isToolCall || isToolResult ? "" : `<div class="msg-time">${formatTime(m.ts)}</div>`}
        </div>
      `;
    });

    if (this.pending) {
      const last = this.messages[total - 1];
      if (!last || last.role === "user") {
        html += `
          <div class="msg left msg-ai">
            <div class="msg-content">
              <div class="msg-bubble" style="opacity:0.6;font-size:13px;">${escHtml(this.typingState || "对方正在思考...")}</div>
            </div>
          </div>
        `;
      }
    }

    e.scroll.innerHTML = html;

    if (this._initial) {
      e.scroll.scrollTop = e.scroll.scrollHeight;
      this._initial = false;
    } else if (preserveScroll) {
      e.scroll.scrollTop = oldTop + (e.scroll.scrollHeight - oldH);
    } else this.scrollToBottom();
  }

  scrollToBottom(force = false) {
    requestAnimationFrame(() => {
      const e = this.els();
      if (!e.scroll) return;
      const dist =
        e.scroll.scrollHeight - (e.scroll.scrollTop + e.scroll.clientHeight);
      if (force || dist < 200) e.scroll.scrollTop = e.scroll.scrollHeight;
    });
  }

  scrollToMessageIndex(msgIdx) {
    setTimeout(() => {
      const targetEl = this.els().scroll.querySelector(
        `.msg[data-msg-index="${msgIdx}"]`,
      );
      if (!targetEl) {
        showToast("该台词超出当前可视范围，请点击顶部加载更多");
        return;
      }
      targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
      targetEl.classList.add("msg-highlight-anim");
      setTimeout(() => targetEl.classList.remove("msg-highlight-anim"), 1600);
    }, 250);
  }

  handleMsgAction(act, idx, btnEl) {
    const m = this.messages[idx];
    if (!m) return;

    if (act === "copy") {
      const text = m.text || "";
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard
          .writeText(text)
          .then(() => showToast("已复制到剪贴板"))
          .catch(() => fallbackCopy(text));
      } else {
        fallbackCopy(text);
      }
      function fallbackCopy(t) {
        const ta = document.createElement("textarea");
        ta.value = t;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          showToast("已复制到剪贴板");
        } catch (err) {
          showToast("复制失败，您的浏览器不支持");
        }
        document.body.removeChild(ta);
      }
    } else if (act === "tts") {
      showToast("正在生成并播放语音...");
      api
        .postS("/api/tts", { text: m.text }, this.sessionId)
        .then((d) => {
          if (d.ok && d.audio) new Audio(d.audio).play();
          else showToast("语音生成失败");
        })
        .catch(() => showToast("网络错误"));
    } else if (act === "reroll") {
      this.rerollMessage(idx);
    } else if (act === "edit") {
      const msgEl = btnEl.closest(".msg");
      const bubbles = msgEl.querySelectorAll(".msg-bubble");
      if (!bubbles || bubbles.length === 0) return;
      const bubbleEl = bubbles[0];

      for (let bi = 1; bi < bubbles.length; bi++)
        bubbles[bi].style.display = "none";
      const origText = m.text || "";

      const textarea = document.createElement("textarea");
      textarea.value = origText;
      textarea.style.cssText =
        "width:100%; min-height:60px; padding:0; border:none; background:transparent; color:inherit; font-size:inherit; line-height:inherit; resize:none; outline:none; font-family:inherit; box-sizing:border-box; overflow-y:hidden;";

      const adjustHeight = () => {
        textarea.style.height = "auto";
        textarea.style.height = textarea.scrollHeight + "px";
      };
      textarea.addEventListener("input", adjustHeight);

      bubbleEl.innerHTML = "";
      bubbleEl.appendChild(textarea);
      adjustHeight();
      textarea.focus();

      textarea.addEventListener("blur", async () => {
        const newText = textarea.value.trim();
        if (newText && newText !== origText) {
          try {
            await api.postS(
              "/api/edit",
              { index: idx, text: newText },
              this.sessionId,
            );
            this.messages[idx].text = newText;
          } catch (e) {
            showToast("保存失败");
          }
        }
        this.render(true);
      });
    } else if (act === "del") {
      if (!confirm("确定彻底删除这条记录吗？")) return;
      api
        .postS("/api/delete", { index: idx }, this.sessionId)
        .then(() => {
          showToast("已删除");
          this.syncOnce();
        })
        .catch(() => showToast("删除请求失败"));
    } else if (act === "more") {
      this.openMoreMenu(idx, btnEl);
    }
  }

  openMoreMenu(idx, anchorEl) {
    const backdrop = document.createElement("div");
    backdrop.className = "context-backdrop";
    backdrop.style.backgroundColor = "transparent";

    const menuBox = document.createElement("div");
    menuBox.className = "popover-box";
    menuBox.style.zIndex = "8002";
    menuBox.style.minWidth = "auto";
    menuBox.style.display = "flex";
    menuBox.style.padding = "10px 8px";
    menuBox.style.gap = "14px";
    menuBox.style.flexDirection = "column";
    menuBox.style.borderRadius = "12px";

    menuBox.style.background = "var(--menu-bg, rgba(255,255,255,0.5))";
    menuBox.style.backdropFilter = "blur(16px)";
    menuBox.style.webkitBackdropFilter = "blur(16px)";
    menuBox.style.boxShadow = "0 8px 32px rgba(0,0,0,0.15)";
    menuBox.style.border = "0.5px solid rgba(128,128,128,0.2)";

    const rect = anchorEl.getBoundingClientRect();
    const popHeight = 160;
    if (rect.top > popHeight) {
      menuBox.style.top = rect.top - popHeight - 8 + "px";
    } else {
      menuBox.style.top = rect.bottom + 8 + "px";
    }
    menuBox.style.left =
      Math.min(rect.left - 20, window.innerWidth - 60) + "px";

    const getIcon = (key, fallback) =>
      ICONS && ICONS[key] ? ICONS[key] : fallback;

    menuBox.innerHTML = `
      <div class="action-btn" title="创建存档点" data-menu-act="savePoint">${getIcon("savePoint", "📌")}</div>
      <div class="action-btn" title="创建分支" data-menu-act="branch">${getIcon("branch", "🌿")}</div>
      <div class="action-btn" title="时光回溯" data-menu-act="rewind">${getIcon("rewind", "⏪")}</div>
      <div class="action-btn" title="彻底删除" data-menu-act="del" style="color: var(--text-secondary); opacity: 0.7;">${getIcon("trash", "🗑️")}</div>
    `;
    const dismiss = () => {
      backdrop.remove();
      menuBox.remove();
    };
    backdrop.onclick = dismiss;

    menuBox.onclick = async (e) => {
      const actItem = e.target.closest("[data-menu-act]");
      if (!actItem) return;
      const act = actItem.dataset.menuAct;
      dismiss();

      if (act === "del") {
        if (!confirm("确定彻底删除这条记录吗？")) return;
        try {
          await api.postS("/api/delete", { index: idx }, this.sessionId);
          showToast("已删除");
          this.syncOnce();
        } catch (err) {
          showToast("删除请求失败");
        }
      } else if (act === "savePoint") {
        showToast("存档点标记成功");
      } else if (act === "branch") {
        if (
          !confirm("将以此处为起点的历史记录，克隆生成一个全新的平行分支会话？")
        )
          return;
        this.branchFrom(idx);
      } else if (act === "rewind") {
        if (
          !confirm(
            "确定将时空回溯到该条消息吗？\n警告：它之后的所有剧情将被彻底裁切！",
          )
        )
          return;
        this.rewindTo(idx);
      }
    };

    document.body.appendChild(backdrop);
    document.body.appendChild(menuBox);
  }

  async rerollMessage(idx) {
    if (!confirm("确定要重新生成这条回复吗？\n(当前及之后的聊天将被覆盖)"))
      return;
    this.setGenerating(true);
    try {
      await api.postS("/api/reroll", { index: idx }, this.sessionId);
      this.messages.splice(idx);
      this.pending = true;
      this.render(true);
      this.startPolling();
    } catch (e) {
      showToast("重新生成失败");
      this.setGenerating(false);
    }
  }

  async rewindTo(idx) {
    this.setGenerating(true);
    try {
      const total = this.messages.length;
      let deleteCount = 0;
      for (let i = total - 1; i > idx; i--) {
        await api.postS("/api/delete", { index: i }, this.sessionId);
        deleteCount++;
      }
      this.messages.splice(idx + 1);
      this.render(true);
      showToast(`回溯成功，已裁剪 ${deleteCount} 条未来时间线`);
    } catch (e) {
      showToast("回溯出错，可能部分消息未删除");
    } finally {
      this.setGenerating(false);
    }
  }

  async branchFrom(idx) {
    showToast("正在开辟平行时空分支...");
    try {
      const res = await api.cloneSession(this.sessionId);
      const newSid = res.session_id || res.id;
      if (!newSid) throw new Error("未能获取分支ID");

      const total = this.messages.length;
      for (let i = total - 1; i > idx; i--) {
        await api.postS("/api/delete", { index: i }, newSid);
      }

      showToast("分支开辟成功！即将跳转...");

      const oldTitle = document.getElementById("chat-room-title").textContent;
      const newTitle = oldTitle.includes("分支")
        ? oldTitle
        : oldTitle + " (分支)";

      setTimeout(() => {
        this.initRoom(newSid, newTitle);
      }, 600);
    } catch (e) {
      showToast("创建分支失败");
    }
  }
}

export const chatView = new ChatView();
