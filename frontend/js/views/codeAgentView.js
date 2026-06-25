import { renderMarkdown, escHtml, showToast } from "../utils.js";
import { api } from "../api.js";

// SVG 图标常量 (极简极客风)
const ICONS = {
  file: `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`,
  loading: `<svg class="spin-anim" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>`,
};

const POLL_INTERVAL = 1500; // 轮询后端 turns 的间隔(ms)

class CodeAgentView {
  constructor() {
    this.currentTaskId = null;
    this.messages = [];
    this.workspaceFiles = [];
    this.task = { status: "就绪", progress: 0 };
    this._bound = false;
    this.generating = false;
    this._lastTurnId = 0;
    this._pollTimer = null;
    this.workspaceTree = "";
  }

  els() {
    return {
      scroll: document.getElementById("ca-terminal-scroll"),
      input: document.getElementById("ca-input"),
      send: document.getElementById("ca-send-btn"),
      queueBtn: document.getElementById("ca-queue-btn"),
      filesPanel: document.getElementById("ca-workspace-files"),
      progressFill: document.getElementById("ca-progress-fill"),
      taskStatus: document.getElementById("ca-task-status"),
      addCtxBtn: document.getElementById("ca-add-ctx-btn"),
      attachBtn: document.getElementById("ca-attach-btn"),
      localFileInput: document.getElementById("ca-local-file-input"),
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

    if (e.queueBtn) {
      e.queueBtn.addEventListener("click", () => this.onQueue());
    }

    // 任务设置按钮：把最近一次发给主模型的完整 prompt 打到控制台（调试用）
    const settingsBtn = document.getElementById("ca-settings-btn");
    if (settingsBtn) {
      settingsBtn.addEventListener("click", () => this.dumpLastPrompt());
    }

    e.input.addEventListener("input", () => {
      e.input.style.height = "auto";
      e.input.style.height = Math.min(e.input.scrollHeight, 120) + "px";
    });

    e.input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !isMobile) {
        ev.preventDefault();
        if (this.generating) this.onQueue();
        else this.onSend();
      }
    });

    e.filesPanel.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("remove-file")) {
        this.removeFileFromWorkspace(ev.target.dataset.file);
      }
    });

    // Add Path：工作区是沙箱目录，上下文文件由 agent 自己读写，这里仅提示。
    const serverModal = document.getElementById("ca-server-file-modal");
    const serverModalClose = document.getElementById("ca-modal-close-btn");
    const fileTreeList = document.getElementById("ca-file-tree-list");

    if (e.addCtxBtn) {
      e.addCtxBtn.addEventListener("click", () => {
        showToast("工作区文件由 Agent 在沙箱内自动管理");
      });
    }
    if (serverModalClose && serverModal) {
      serverModalClose.addEventListener("click", () => {
        serverModal.style.display = "none";
      });
    }
    if (serverModal) {
      serverModal.addEventListener("click", (ev) => {
        if (ev.target === serverModal) serverModal.style.display = "none";
      });
    }
    if (fileTreeList) {
      fileTreeList.addEventListener("click", (ev) => {
        if (ev.target.classList.contains("file")) {
          const serverPath = ev.target.dataset.path;
          this.addFileToWorkspace(serverPath, "loaded");
          showToast(`已加载: ${serverPath}`);
          if (serverModal) serverModal.style.display = "none";
        }
      });
    }

    e.attachBtn.addEventListener("click", () => e.localFileInput.click());

    e.localFileInput.addEventListener("change", (ev) => {
      const files = ev.target.files;
      if (!files || files.length === 0) return;
      let appendText = "";
      for (let i = 0; i < files.length; i++) {
        appendText += `\n[Local File: ${files[i].name}]\n`;
      }
      e.input.value += appendText;
      e.input.style.height = "auto";
      e.input.style.height = Math.min(e.input.scrollHeight, 120) + "px";
      e.input.focus();
      e.localFileInput.value = "";
    });
  }

  async open(taskId = "default", taskTitle = "Code Agent") {
    window.router.pushView("code-agent-view");
    this.bindOnce();

    if (this.currentTaskId !== taskId) {
      this.currentTaskId = taskId;
      this.messages = [];
      this.workspaceFiles = [];
      this._lastTurnId = 0;

      const titleEl = document.querySelector("#code-agent-view .agent-name");
      if (titleEl) titleEl.textContent = taskTitle;

      this.updateTask("加载中...", 0);
      this.renderAll();
      await this.loadHistory();
    } else {
      this.renderAll();
    }
    this.startPolling();
  }

  // 拉取该任务已有事件流（断点恢复 / 二次进入）
  async loadHistory() {
    try {
      const res = await api.agentTurns(this.currentTaskId, 0);
      if (res && res.ok) {
        if (!res.turns.length) {
          this.messages = [
            {
              role: "sys",
              text: `[SYSTEM] Task ${this.currentTaskId} ready.\n[SYSTEM] 输入你的需求开始（Agent 第一轮会先规划拆任务，再在沙箱里写代码并自测）。`,
            },
          ];
        }
        res.turns.forEach((t) => this.ingestTurn(t));
        this.generating = !!res.running;
        this._refreshSendBtn();
      }
      await this.refreshTaskState();
    } catch (err) {
      this.messages.push({
        role: "sys",
        text: "[SYSTEM] 加载历史失败: " + err.message,
      });
    }
    this.renderAll();
    this.scrollToBottom();
  }

  // 把一个后端 turn 映射进本地 messages
  ingestTurn(t) {
    this._lastTurnId = Math.max(this._lastTurnId, t.id || 0);
    if (t.role === "user" && t.type === "text") {
      this.messages.push({ role: "user", text: t.content });
    } else if (t.type === "reasoning") {
      this.messages.push({ role: "thinking", text: t.content, time: "" });
    } else if (t.type === "tool_call") {
      let args = {};
      try {
        args = JSON.parse(t.content).args || {};
      } catch (e) {}
      const cmd = this._fmtToolCall(t.tool_name, args);
      this.messages.push({
        role: "tool",
        cmd,
        result: "执行中...",
        tool: t.tool_name,
        pending: true,
      });
      const fp =
        args.filepath ||
        (args.files && args.files[0] && args.files[0].filepath);
      if (fp) this.addFileToWorkspace(fp, "reading");
    } else if (t.type === "tool_result") {
      const m = [...this.messages]
        .reverse()
        .find((x) => x.role === "tool" && x.pending && x.tool === t.tool_name);
      let parsed = null;
      try {
        parsed = JSON.parse(t.content);
      } catch (e) {}
      const isErr = parsed && (parsed.error || parsed.exit_code > 0);
      const summary = this._fmtToolResult(parsed, t.content);
      if (m) {
        m.result = summary;
        m.isError = !!isErr;
        m.pending = false;
      } else {
        this.messages.push({
          role: "tool",
          cmd: t.tool_name,
          result: summary,
          isError: !!isErr,
        });
      }
      this.workspaceFiles.forEach((f) => {
        if (f.status === "reading") f.status = "loaded";
      });
    } else if (t.role === "assistant" && t.type === "text") {
      this.messages.push({ role: "ai", text: t.content });
    } else if (t.role === "system" && t.type === "text") {
      this.messages.push({ role: "sys", text: t.content });
    }
  }

  _fmtToolCall(name, args) {
    if (name === "run_terminal_command") return `$ ${args.command || ""}`;
    if (name === "read_file_with_lines")
      return `read_file('${args.filepath || ""}')`;
    if (name === "apply_file_edits") return `edit('${args.filepath || ""}')`;
    if (name === "grep_files") return `grep('${args.pattern || ""}')`;
    if (name === "get_outline") return `outline('${args.filepath || ""}')`;
    if (name === "get_function_code") return `getFn('${args.name || ""}')`;
    if (name === "explore_codebase") {
      const n = (args.questions || []).length;
      return `explore：并发研究 ${n} 个问题`;
    }
    if (name === "batch_write_files") {
      const fs = (args.files || []).map((f) => f.filepath).join(", ");
      return `write(${fs})`;
    }
    return `${name}(${JSON.stringify(args).slice(0, 60)})`;
  }

  _fmtToolResult(parsed, raw) {
    if (!parsed) return (raw || "").slice(0, 300);
    if (parsed.error) return "[ERR] " + parsed.error;
    if (parsed.results) return `[研究完成] ${parsed.count} 个问题已汇总（带行号引用）`;
    if (parsed.command !== undefined) {
      const out =
        (parsed.stdout || "") + (parsed.stderr ? "\n" + parsed.stderr : "");
      return `[exit ${parsed.exit_code}] ${out.trim().slice(0, 400) || "(no output)"}`;
    }
    if (parsed.msg) return "[OK] " + parsed.msg;
    if (parsed.content)
      return "[OK] " + String(parsed.content).split("\n").length + " 行已读取";
    return "[OK] " + JSON.stringify(parsed).slice(0, 200);
  }

  startPolling() {
    this.stopPolling();
    this._pollTimer = setInterval(() => this.poll(), POLL_INTERVAL);
  }

  stopPolling() {
    if (this._pollTimer) clearInterval(this._pollTimer);
    this._pollTimer = null;
  }

  async poll() {
    if (!this.currentTaskId) return;
    const view = document.getElementById("code-agent-view");
    if (!view || !view.classList.contains("show")) return;
    try {
      const res = await api.agentTurns(this.currentTaskId, this._lastTurnId);
      if (res && res.ok) {
        let changed = false;
        res.turns.forEach((t) => {
          this.ingestTurn(t);
          changed = true;
        });
        const wasGen = this.generating;
        this.generating = !!res.running;
        if (changed) {
          this.renderAll();
          this.scrollToBottom();
        }
        if (wasGen !== this.generating) this._refreshSendBtn();
        if (changed || wasGen !== this.generating) this.refreshTaskState();
      }
    } catch (e) {
      /* 静默重试 */
    }
  }

  async refreshTaskState() {
    try {
      const res = await api.agentTask(this.currentTaskId);
      if (res && res.ok) {
        const card = res.checkpoint || {};
        const status = card.summary || res.task.status || "运行中";
        this.updateTask(status, res.task.progress || card.progress || 0);
        this.workspaceTree = res.tree || "";
        this.renderFiles();
      }
    } catch (e) {}
  }

  updateTask(status, progressPercent) {
    this.task.status = status;
    this.task.progress = progressPercent;
    const e = this.els();
    if (e.taskStatus) e.taskStatus.textContent = status;
    if (e.progressFill) e.progressFill.style.width = `${progressPercent}%`;
  }

  addFileToWorkspace(filename, status = "loaded") {
    if (!this.workspaceFiles.some((f) => f.name === filename)) {
      this.workspaceFiles.push({ name: filename, status });
      this.renderFiles();
    }
  }

  removeFileFromWorkspace(filename) {
    this.workspaceFiles = this.workspaceFiles.filter(
      (f) => f.name !== filename,
    );
    this.renderFiles();
  }

  renderFiles() {
    const e = this.els();
    if (!e.filesPanel) return;
    // 优先展示完整沙箱工作区目录树（后端每次 /api/agent/task 返回）
    if (this.workspaceTree) {
      e.filesPanel.innerHTML =
        '<pre style="margin:0;color:#9cdcfe;font-size:11px;line-height:1.5;white-space:pre;max-height:26vh;overflow:auto;">' +
        escHtml(this.workspaceTree) +
        "</pre>";
      return;
    }
    if (this.workspaceFiles.length === 0) {
      e.filesPanel.innerHTML =
        '<div style="color:#555;font-size:11px;padding-top:4px;">工作区为空（Agent 尚未创建文件）</div>';
      return;
    }
    e.filesPanel.innerHTML = this.workspaceFiles
      .map(
        (f) => `
      <div class="file-chip ${f.status === "reading" ? "active-reading" : ""}">
        <span style="color:#9cdcfe;">${f.status === "reading" ? ICONS.loading : ICONS.file}</span>
        <span style="margin-left:4px;">${escHtml(f.name)}</span>
        <span class="remove-file" data-file="${escHtml(f.name)}">✕</span>
      </div>
    `,
      )
      .join("");
  }

  async onSend() {
    const e = this.els();
    const text = e.input.value.trim();
    if (!text) return;
    if (this.generating) return;

    e.input.value = "";
    e.input.style.height = "auto";

    this.generating = true;
    this._refreshSendBtn();
    this.updateTask("提交中...", 5);

    try {
      const res = await api.agentSend(this.currentTaskId, text);
      if (!res || !res.ok) {
        this.generating = false;
        this._refreshSendBtn();
        showToast((res && res.error) || "发送失败");
        return;
      }
      this.startPolling();
    } catch (err) {
      this.generating = false;
      this._refreshSendBtn();
      showToast("发送失败: " + err.message);
    }
  }

  _refreshSendBtn() {
    const e = this.els();
    if (!e.send) return;
    if (this.generating) {
      // 工作中：发送键变“停止”（中断任务），并露出“排队补充”键
      e.send.innerHTML = '<rect x="6" y="6" width="12" height="12"/>';
      e.send.style.color = "#f14c4c";
      e.send.setAttribute("title", "中断当前任务");
      if (e.queueBtn) e.queueBtn.style.display = "inline-block";
      const inp = e.input;
      if (inp)
        inp.placeholder = "Agent 工作中：回车/绿色键=排队补充，红色键=中断";
    } else {
      e.send.innerHTML =
        '<line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>';
      e.send.style.color = "#007acc";
      e.send.setAttribute("title", "发送");
      if (e.queueBtn) e.queueBtn.style.display = "none";
      const inp = e.input;
      if (inp) inp.placeholder = "输入指令或挂载本地文件...";
    }
  }

  // 排队补充：不打断 agent，消息持久化并在下一轮注入它的上下文
  async onQueue() {
    const e = this.els();
    const text = e.input.value.trim();
    if (!text) {
      showToast("先输入要补充/修改的内容");
      return;
    }
    e.input.value = "";
    e.input.style.height = "auto";
    try {
      const res = await api.agentEnqueue(this.currentTaskId, text);
      if (res && res.ok) {
        showToast("已排队，将并入 Agent 下一轮");
        this.poll(); // 立即拉回这条排队消息显示
      } else {
        showToast((res && res.error) || "排队失败");
      }
    } catch (err) {
      showToast("排队失败: " + err.message);
    }
  }

  // 把最近一次发给主模型的完整 last_llm_payload 输出到 vConsole（调试用）
  async dumpLastPrompt() {
    try {
      const res = await api.agentLastPrompt(this.currentTaskId);
      const lp = res && res.last_prompt;
      if (!lp) {
        showToast("还没有发送过 prompt");
        return;
      }
      if (window.vConsole) {
        window.vConsole.show();
        window.vConsole.log(
          `===== last_llm_payload  round=${lp.round}  model=${lp.model}  @${lp.ts} =====`,
          "#ffae57",
        );
        (lp.messages || []).forEach((m, i) => {
          const c =
            typeof m.content === "string"
              ? m.content
              : JSON.stringify(m.content);
          window.vConsole.log(`[#${i} ${m.role}] ${c}`, "#9cdcfe");
          if (m.tool_calls) {
            window.vConsole.log(
              `   tool_calls: ${JSON.stringify(m.tool_calls)}`,
              "#dcdcaa",
            );
          }
        });
        window.vConsole.log(
          `===== end (${(lp.messages || []).length} 条) =====`,
          "#ffae57",
        );
      }
      showToast("已在控制台输出最近 prompt");
    } catch (err) {
      showToast("获取 prompt 失败: " + err.message);
    }
  }

  // 真中断：通知后端在下一个检查点停止循环；轮询会拉回“已中断”系统消息与“已挂起”状态。
  async interruptGeneration() {
    if (
      !window.confirm(
        "确定要中断当前任务吗？\n（已完成的进度会保存为进度卡，可稍后继续）",
      )
    )
      return;
    try {
      await api.agentInterrupt(this.currentTaskId);
      showToast("已请求中断，正在停止…");
      this.updateTask("正在中断…", this.task.progress);
    } catch (err) {
      showToast("中断失败: " + err.message);
    }
    // 不在前端强行翻 generating；等后端 running 变 false、轮询自然收尾。
  }

  renderAll() {
    this.renderFiles();
    this.renderTerminal();
  }

  renderTerminal() {
    const e = this.els();
    if (!e.scroll) return;
    let html = "";
    this.messages.forEach((m) => {
      if (m.role === "sys") {
        html += `<div class="terminal-msg" style="color:#858585; font-size:11px;">${escHtml(m.text)}</div>`;
      } else if (m.role === "user") {
        html += `<div class="terminal-msg user-msg"><span class="prompt-symbol">❯</span> ${escHtml(m.text)}</div>`;
      } else if (m.role === "thinking") {
        html += `
          <details class="terminal-msg agent-thinking">
            <summary><span style="color:#c586c0;font-weight:bold;">[Thought process]</span>${m.time ? "(" + m.time + ")" : ""}</summary>
            <div class="think-content">${escHtml(m.text)}</div>
          </details>
        `;
      } else if (m.role === "tool") {
        const isErr = m.isError ? "error" : "";
        const spinner = m.pending
          ? ` <span style="color:#dcdcaa;">${ICONS.loading}</span>`
          : "";
        html += `
          <div class="terminal-msg tool-call">
            <div class="tool-cmd"><span style="color:#dcdcaa;font-weight:bold;">[EXEC]</span> ${escHtml(m.cmd)}${spinner}</div>
            <div class="tool-result ${isErr}">${escHtml(m.result)}</div>
          </div>
        `;
      } else if (m.role === "ai") {
        html += `<div class="terminal-msg agent-reply">${renderMarkdown(m.text)}</div>`;
      }
    });
    if (this.generating) {
      html += `<div class="terminal-msg agent-reply" style="opacity:0.6; animation: pulse 1.5s infinite;">_ executing...</div>`;
    }
    e.scroll.innerHTML = html;
  }

  scrollToBottom() {
    requestAnimationFrame(() => {
      const e = this.els();
      if (e.scroll) e.scroll.scrollTop = e.scroll.scrollHeight;
    });
  }
}

export const codeAgentView = new CodeAgentView();
