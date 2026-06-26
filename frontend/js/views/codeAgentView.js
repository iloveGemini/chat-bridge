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
    this.pinnedContext = [];
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
      todosPanel: document.getElementById("ca-todos-panel"),
      todosList: document.getElementById("ca-todos-list"),
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

    e.filesPanel.addEventListener("click", async (ev) => {
      if (ev.target.classList.contains("remove-file")) {
        await api.agentContextRemove(
          this.currentTaskId,
          ev.target.dataset.file,
        );
        this.refreshContext();
      }
    });

    // Add Path：工作区是沙箱目录，上下文文件由 agent 自己读写，这里仅提示。
    const serverModal = document.getElementById("ca-server-file-modal");
    const serverModalClose = document.getElementById("ca-modal-close-btn");
    const fileTreeList = document.getElementById("ca-file-tree-list");

    if (e.addCtxBtn) {
      e.addCtxBtn.addEventListener("click", () => this.openContextPicker());
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

    e.scroll.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("clarify-submit-btn")) {
        const card = ev.target.closest(".terminal-msg");
        const inputs = card.querySelectorAll("input[type=radio]:checked");
        const customs = card.querySelectorAll("input[type=text]");
        let reply = "【需求确认回复】\n";
        inputs.forEach((inp) => {
          const qText = inp.closest("div").querySelector("div").textContent;
          reply += `${qText} -> 选择了: ${inp.value}\n`;
        });
        customs.forEach((inp) => {
          if (inp.value.trim()) {
            reply += `补充说明: ${inp.value.trim()}\n`;
          }
        });

        e.input.value = reply;
        this.onSend();
      }
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
  // 后端 turn 结构（runtime/coding_runtime.add_turn）：
  //   { id, role, type, content, tool_name, ts }
  // 关键：tool_call / tool_result 的 role 也是 "assistant"，所以必须
  //   先按 type 分派，最后才落到「普通 assistant 文本」分支，
  //   否则工具调用会被当成空文本吞掉、永远不显示。
  ingestTurn(t) {
    this._lastTurnId = Math.max(this._lastTurnId, t.id || 0);
    const type = t.type;

    if (type === "tool_call") {
      // content = JSON 字符串 {"name": ..., "args": {...}}
      let payload = t.content;
      if (typeof payload === "string") {
        try {
          payload = JSON.parse(payload);
        } catch (e) {
          payload = {};
        }
      }
      payload = payload || {};
      const name = payload.name || t.tool_name || "";
      const args = payload.args || {};
      this.messages.push({
        role: "tool",
        toolName: name,
        cmd: this._fmtToolCall(name, args),
        result: "执行中...",
        pending: true,
        isError: false,
      });
      return;
    }

    if (type === "tool_result") {
      // content = JSON 字符串（工具返回值）；按 FIFO 配对最早一个同名 pending 调用
      let parsedRes = null;
      const raw = typeof t.content === "string" ? t.content : JSON.stringify(t.content);
      try {
        parsedRes = typeof t.content === "string" ? JSON.parse(t.content) : t.content;
      } catch (e) {
        parsedRes = null;
      }
      const isError = !!(parsedRes && parsedRes.error);
      const resultText = this._fmtToolResult(parsedRes, raw);

      const pending = this.messages.find(
        (m) =>
          m.role === "tool" &&
          m.pending &&
          (m.toolName === t.tool_name || !t.tool_name),
      );
      if (pending) {
        pending.result = resultText;
        pending.pending = false;
        pending.isError = isError;
      } else {
        // 没找到配对的调用（历史不完整）：单独显示结果
        this.messages.push({
          role: "tool",
          toolName: t.tool_name,
          cmd: t.tool_name ? this._fmtToolCall(t.tool_name, {}) : "(tool)",
          result: resultText,
          pending: false,
          isError,
        });
      }
      return;
    }

    if (type === "reasoning") {
      this.messages.push({ role: "thinking", text: t.content, time: "" });
      return;
    }

    if (type === "clarification_card") {
      let args = {};
      try {
        args = JSON.parse(t.content);
      } catch (e) {
        args = t.content || {};
      }
      this.messages.push({
        role: "clarification",
        args: args,
        pending: true,
        tool: t.tool_name,
      });
      return;
    }

    // type === "text"（或其它）：按 role 区分用户 / 系统 / AI
    if (type === "text" || !type) {
      if (t.role === "user") {
        this.messages.push({ role: "user", text: t.content });
      } else if (t.role === "system" || t.role === "sys") {
        this.messages.push({ role: "sys", text: t.content });
      } else if (t.content && t.content.trim()) {
        this.messages.push({ role: "ai", text: t.content });
      }
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
    if (parsed.results) {
      const parts = parsed.results.map((r) => {
        const q = r.instruction || r.id || "";
        const a = (r.result || "").trim() || "(无结论)";
        const t = r.elapsed != null ? ` (${r.elapsed}s)` : "";
        return `▸ ${q}${t}\n${a}`;
      });
      return (
        `[子Agent研究完成] ${parsed.count} 个问题：\n\n` + parts.join("\n\n")
      );
    }
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
    if (this._polling) return; // 防止两次轮询重叠 -> 同一条 turn 被 ingest 两次（重复显示）
    const view = document.getElementById("code-agent-view");
    if (!view || !view.classList.contains("show")) return;
    this._polling = true;
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
    } finally {
      this._polling = false;
    }
  }

  async refreshTaskState() {
    try {
      const res = await api.agentTask(this.currentTaskId);
      if (res && res.ok) {
        const card = res.checkpoint || {};
        const status = card.summary || res.task.status || "运行中";
        this.updateTask(status, res.task.progress || card.progress || 0);
        if (res.task.todos && res.task.todos.length > 0) {
          this.renderTodos(res.task.todos);
        } else {
          const e = this.els();
          if (e.todosPanel) e.todosPanel.style.display = "none";
        }
      }
      await this.refreshContext();
    } catch (e) {}
  }

  // 拉取该任务钉住的固定上下文（agent / 用户都可能改）
  async refreshContext() {
    try {
      const res = await api.agentContext(this.currentTaskId);
      if (res && res.ok) {
        this.pinnedContext = res.context || [];
        this.renderFiles();
      }
    } catch (e) {}
  }

  // 打开「固定上下文」选择器：从工作区文件里挑，选 大纲/全代码 钉住
  async openContextPicker() {
    const ov = document.createElement("div");
    ov.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;display:flex;align-items:center;justify-content:center;";
    const box = document.createElement("div");
    box.style.cssText =
      "width:92%;max-width:560px;max-height:82vh;display:flex;flex-direction:column;background:#1e1e1e;color:#ddd;border:1px solid #333;border-radius:8px;overflow:hidden;";
    box.innerHTML =
      '<div style="padding:11px 14px;border-bottom:1px solid #333;font-weight:bold;font-size:14px;">固定上下文（钉住的文件每轮都注入给 Agent）</div>' +
      '<div id="_cp_pinned" style="padding:6px 10px;border-bottom:1px solid #2a2a2a;font-size:12px;"></div>' +
      '<div style="padding:6px 14px;color:#888;font-size:11px;">点文件右侧按钮钉入：大纲(省) / 全代码</div>' +
      '<div id="_cp_files" style="flex:1;overflow:auto;min-height:160px;"></div>' +
      '<div style="padding:10px 14px;border-top:1px solid #333;text-align:right;"><button id="_cp_close" style="padding:6px 14px;border:1px solid #3a3a3a;border-radius:5px;background:#2d2d2d;color:#ddd;cursor:pointer;">关闭</button></div>';
    document.body.appendChild(ov);
    ov.appendChild(box);

    const taskId = this.currentTaskId;
    const renderPinned = () => {
      const wrap = box.querySelector("#_cp_pinned");
      const items = this.pinnedContext || [];
      if (!items.length) {
        wrap.innerHTML =
          '<span style="color:#666;">（暂未钉住任何文件）</span>';
        return;
      }
      wrap.innerHTML = items
        .map((c) => {
          const badge = c.mode === "full" ? "全码" : "大纲";
          return (
            '<span style="display:inline-block;margin:2px 4px;padding:2px 6px;background:#2d2d2d;border-radius:4px;">' +
            badge +
            " " +
            escHtml(c.filepath) +
            ' <span class="_cp_unpin" data-file="' +
            escHtml(c.filepath) +
            '" style="color:#f14c4c;cursor:pointer;">✕</span></span>'
          );
        })
        .join("");
    };
    const loadFiles = async () => {
      const filesBox = box.querySelector("#_cp_files");
      filesBox.innerHTML =
        '<div style="padding:14px;color:#888;">加载中…</div>';
      try {
        const res = await api.agentFiles(taskId);
        const files = (res && res.ok && res.files) || [];
        filesBox.innerHTML =
          files
            .map(
              (f) =>
                '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-bottom:1px solid #2a2a2a;">' +
                '<span style="font-size:12px;word-break:break-all;flex:1;">' +
                escHtml(f) +
                "</span>" +
                '<span style="white-space:nowrap;margin-left:8px;">' +
                '<button class="_cp_add" data-f="' +
                escHtml(f) +
                '" data-m="outline" style="margin-left:4px;padding:3px 8px;border:1px solid #4ec9b0;border-radius:4px;background:transparent;color:#4ec9b0;cursor:pointer;font-size:12px;">大纲</button>' +
                '<button class="_cp_add" data-f="' +
                escHtml(f) +
                '" data-m="full" style="margin-left:4px;padding:3px 8px;border:1px solid #e0a458;border-radius:4px;background:transparent;color:#e0a458;cursor:pointer;font-size:12px;">全码</button>' +
                "</span></div>",
            )
            .join("") ||
          '<div style="padding:14px;color:#888;">工作区暂无文件</div>';
      } catch (e) {
        filesBox.innerHTML =
          '<div style="padding:14px;color:#f14c4c;">加载失败: ' +
          e.message +
          "</div>";
      }
    };
    box.addEventListener("click", async (ev) => {
      const add = ev.target.closest("._cp_add");
      const unpin = ev.target.closest("._cp_unpin");
      if (add) {
        await api.agentContextAdd(taskId, add.dataset.f, add.dataset.m);
        await this.refreshContext();
        renderPinned();
        showToast("已钉入: " + add.dataset.f);
      } else if (unpin) {
        await api.agentContextRemove(taskId, unpin.dataset.file);
        await this.refreshContext();
        renderPinned();
      }
    });
    box.querySelector("#_cp_close").onclick = () =>
      document.body.removeChild(ov);
    ov.addEventListener("click", (ev) => {
      if (ev.target === ov) document.body.removeChild(ov);
    });
    renderPinned();
    loadFiles();
  }

  updateTask(status, progressPercent) {
    this.task.status = status;
    this.task.progress = progressPercent;
    const e = this.els();
    if (e.taskStatus) e.taskStatus.textContent = status;
    if (e.progressFill) e.progressFill.style.width = `${progressPercent}%`;
  }

  renderTodos(todos) {
    const e = this.els();
    if (!e.todosPanel) return;
    e.todosPanel.style.display = "block";

    const total = todos.length;
    const doneCount = todos.filter((t) => t.done).length;
    const currentTodo = todos.find((t) => !t.done);
    const currentText = currentTodo ? currentTodo.text : "全部完成";
    const stepNum = currentTodo ? doneCount + 1 : total;

    const details = e.todosPanel.querySelector("details");
    const isOpen = details && details.open ? "open" : "";

    const listHtml = todos
      .map((t) => {
        const icon = t.done
          ? `<svg viewBox="0 0 24 24" width="14" height="14" stroke="#4ec9b0" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`
          : `<svg viewBox="0 0 24 24" width="14" height="14" stroke="#888" stroke-width="2" fill="none"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg>`;
        const style = t.done
          ? `text-decoration: line-through; color: #888;`
          : ``;
        return `<div style="display:flex; align-items:flex-start; gap:6px; line-height:1.4;">
        <div style="margin-top:2px;">${icon}</div>
        <div style="${style}">${escHtml(t.text)}</div>
      </div>`;
      })
      .join("");

    e.todosPanel.innerHTML = `
      <details ${isOpen} style="cursor: pointer;">
        <summary style="font-size: 12px; color: #888; font-weight: bold; outline: none; user-select: none;">
          任务计划 <span style="color:#4ec9b0; margin-left:8px; font-weight: normal;">step:${stepNum}/${total} - ${escHtml(currentText)}</span>
        </summary>
        <div id="ca-todos-list" style="font-size: 13px; color: #ddd; display: flex; flex-direction: column; gap: 6px; margin-top: 8px; cursor: default;">
          ${listHtml}
        </div>
      </details>
    `;
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
    const items = this.pinnedContext || [];
    if (items.length === 0) {
      e.filesPanel.innerHTML =
        '<div style="color:#666;font-size:11px;padding-top:4px;">未钉住文件。点上方「+ Add Path」把参考文件钉入固定上下文（每轮注入给 Agent，Agent 也能自己增减）。</div>';
      return;
    }
    e.filesPanel.innerHTML = items
      .map((c) => {
        const full = c.mode === "full";
        const badge = full ? "全码" : "大纲";
        const col = full ? "#e0a458" : "#4ec9b0";
        return `
      <div class="file-chip">
        <span style="color:${col};font-size:10px;border:1px solid ${col};border-radius:3px;padding:0 3px;margin-right:4px;">${badge}</span>
        <span style="word-break:break-all;">${escHtml(c.filepath)}</span>
        <span class="remove-file" data-file="${escHtml(c.filepath)}" style="margin-left:6px;cursor:pointer;color:#f14c4c;">✕</span>
      </div>`;
      })
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
        html += `<div class="terminal-msg user-msg" style="white-space:pre-wrap;"><span class="prompt-symbol">❯</span> ${escHtml(m.text)}</div>`;
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
            <div class="tool-result ${isErr}" style="white-space:pre-wrap;">${escHtml(m.result)}</div>
          </div>
        `;
      } else if (m.role === "clarification") {
        // 检查这个确认卡是否已经被回复
        const cardIndex = this.messages.indexOf(m);
        const subsequentUserMessage = this.messages
          .slice(cardIndex + 1)
          .find((msg) => msg.role === "user");
        const isAnswered =
          subsequentUserMessage &&
          subsequentUserMessage.text.includes("【需求确认回复】");

        html += this._renderClarificationCard(m, isAnswered);
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

  _renderClarificationCard(m, isAnswered) {
    const qHtml = (m.args.questions || [])
      .map((q, i) => {
        const opts = (q.options || [])
          .map((o) => {
            const rec = o.recommended
              ? `<span style="color:#e0a458;font-size:10px;border:1px solid #e0a458;border-radius:3px;padding:0 2px;margin-left:4px;">推荐</span>`
              : "";
            const checked = o.recommended ? "checked" : "";
            return `<label style="display:block;margin-top:4px;cursor:pointer;"><input type="radio" name="clarify_${q.id}" value="${escHtml(o.value)}" ${checked}> ${escHtml(o.label)}${rec}</label>`;
          })
          .join("");
        const custom = q.allow_custom
          ? `<input type="text" id="clarify_custom_${q.id}" placeholder="自定义补充..." style="margin-top:6px;width:100%;background:#2d2d2d;border:1px solid #444;color:#ddd;padding:4px 8px;border-radius:4px;font-size:12px;">`
          : "";
        return `<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #333;">
        <div style="font-weight:bold;margin-bottom:6px;">${i + 1}. ${escHtml(q.text)}</div>
        ${opts}
        ${custom}
      </div>`;
      })
      .join("");

    const btn = !isAnswered
      ? `<button class="clarify-submit-btn" style="background:#007acc;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">提交回复</button>`
      : `<div style="color:#4ec9b0;">已回复</div>`;

    return `<div class="terminal-msg" style="background:#252526;border:1px solid #3c3c3c;border-radius:6px;padding:12px;margin:8px 0;">
      <div style="color:#4ec9b0;font-weight:bold;margin-bottom:10px;display:flex;align-items:center;gap:6px;">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
        需求确认
      </div>
      ${qHtml}
      <div style="text-align:right;margin-top:8px;">${btn}</div>
    </div>`;
  }
}

export const codeAgentView = new CodeAgentView();
