import { renderMarkdown, escHtml, showToast } from "../utils.js";

// SVG 图标常量 (极简极客风)
const ICONS = {
  file: `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`,
  loading: `<svg class="spin-anim" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>`,
};

class CodeAgentView {
  constructor() {
    this.currentTaskId = null;
    this.messages = [];
    this.workspaceFiles = [];
    this.task = { status: "就绪", progress: 0 };
    this._bound = false;
    this.generating = false;
  }

  els() {
    return {
      scroll: document.getElementById("ca-terminal-scroll"),
      input: document.getElementById("ca-input"),
      send: document.getElementById("ca-send-btn"),
      filesPanel: document.getElementById("ca-workspace-files"),
      progressFill: document.getElementById("ca-progress-fill"),
      taskStatus: document.getElementById("ca-task-status"),
      addCtxBtn: document.getElementById("ca-add-ctx-btn"),
      attachBtn: document.getElementById("ca-attach-btn"),
      localFileInput: document.getElementById("ca-local-file-input"), // 新增本地文件输入框
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

    e.input.addEventListener("input", () => {
      e.input.style.height = "auto";
      e.input.style.height = Math.min(e.input.scrollHeight, 120) + "px";
    });

    e.input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !isMobile) {
        ev.preventDefault();
        this.onSend();
      }
    });

    e.filesPanel.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("remove-file")) {
        this.removeFileFromWorkspace(ev.target.dataset.file);
      }
    });

    // 预加载 Context 文件
    // 工作区预加载服务器端路径 (Server-side path)
    // 弹窗元素获取
    const serverModal = document.getElementById("ca-server-file-modal");
    const serverModalClose = document.getElementById("ca-modal-close-btn");
    const fileTreeList = document.getElementById("ca-file-tree-list");

    // 1. 点击 + Add Path 呼出文件树弹窗
    e.addCtxBtn.addEventListener("click", () => {
      serverModal.style.display = "flex";
    });

    // 2. 关闭弹窗
    serverModalClose.addEventListener("click", () => {
      serverModal.style.display = "none";
    });
    serverModal.addEventListener("click", (ev) => {
      if (ev.target === serverModal) serverModal.style.display = "none"; // 点击蒙层关闭
    });

    // 3. 点击文件树中的文件加载到工作区
    fileTreeList.addEventListener("click", (ev) => {
      if (ev.target.classList.contains("file")) {
        const serverPath = ev.target.dataset.path;
        this.addFileToWorkspace(serverPath, "loaded");
        showToast(`已加载: ${serverPath}`);
        serverModal.style.display = "none"; // 加载后自动关闭
      }
    });

    // 左下角挂载本地手机文件 (Local file)
    e.attachBtn.addEventListener("click", () => {
      e.localFileInput.click();
    });

    // 本地文件选择后，拼接到输入框
    e.localFileInput.addEventListener("change", (ev) => {
      const files = ev.target.files;
      if (!files || files.length === 0) return;

      let appendText = "";
      for (let i = 0; i < files.length; i++) {
        // [Local] 前缀用于提示 AI 这是本地临时上传的文件
        appendText += `\n[Local File: ${files[i].name}]\n`;
      }

      e.input.value += appendText;
      e.input.style.height = "auto";
      e.input.style.height = Math.min(e.input.scrollHeight, 120) + "px";
      e.input.focus();

      // 清空 input value，以便下次选同一个文件依然能触发 change 事件
      e.localFileInput.value = "";
    });
  }

  open(taskId = "default", taskTitle = "Code Agent") {
    if (this.currentTaskId !== taskId) {
      this.currentTaskId = taskId;
      this.messages = [];
      this.workspaceFiles = [];

      const titleEl = document.querySelector("#code-agent-view .agent-name");
      if (titleEl) titleEl.textContent = taskTitle;

      this.updateTask("环境初始化...", 0);

      // 纯文字的系统开场白，显得专业
      setTimeout(() => {
        this.messages = [
          {
            role: "sys",
            text: `[SYSTEM] Workspace initialized. Task ID: ${taskId}\n[SYSTEM] Awaiting instructions...`,
          },
        ];
        this.updateTask("就绪", 0);
        this.renderAll();
      }, 300);
    }
    window.router.pushView("code-agent-view");
    this.bindOnce();
    this.renderAll();
  }

  updateTask(status, progressPercent) {
    this.task.status = status;
    this.task.progress = progressPercent;
    const e = this.els();
    if (e.taskStatus) e.taskStatus.textContent = status;
    if (e.progressFill) e.progressFill.style.width = `${progressPercent}%`;
  }

  addFileToWorkspace(filename, status = "loaded") {
    if (!this.workspaceFiles.includes(filename)) {
      this.workspaceFiles.push({ name: filename, status: status });
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

    if (this.workspaceFiles.length === 0) {
      e.filesPanel.innerHTML =
        '<div style="color:#555;font-size:11px;padding-top:4px;">No context files loaded.</div>';
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

    this.messages.push({ role: "user", text });
    e.input.value = "";
    e.input.style.height = "auto";

    this.generating = true;
    e.send.innerHTML = '<rect x="6" y="6" width="12" height="12"/>'; // 停止方块
    e.send.style.color = "#f14c4c";
    this.renderAll();

    this.updateTask("Parsing prompt...", 20);

    // 模拟思考和工具调用
    setTimeout(() => {
      this.messages.push({
        role: "thinking",
        time: "1.2s",
        text: "Analyzing user request.\nTargeting UI components to inject new structure.",
      });
      this.renderAll();
      this.scrollToBottom();
      this.updateTask("Running tool...", 40);
    }, 600);

    setTimeout(() => {
      this.messages.push({
        role: "tool",
        cmd: "read_file('frontend/index.html')",
        result: "[OK] 4.2kb read. Added to workspace.",
      });
      this.addFileToWorkspace("frontend/index.html");
      this.renderAll();
      this.scrollToBottom();
      this.updateTask("Generating...", 80);
    }, 1500);

    setTimeout(() => {
      this.messages.push({
        role: "ai",
        text: "解析完成。接下来我将修改 `index.html` 注入新的 UI 结构。",
      });
      this.generating = false;
      this.updateTask("任务挂起", 100);
      e.send.innerHTML =
        '<line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>'; // 恢复发送箭头
      e.send.style.color = "#007acc";
      this.renderAll();
      this.scrollToBottom();
    }, 2500);
  }

  interruptGeneration() {
    this.generating = false;
    const e = this.els();
    e.send.innerHTML =
      '<line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>';
    e.send.style.color = "#007acc";
    this.updateTask("Process Terminated", 0);
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
        // 使用纯文本标签代替 Emoji
        html += `
          <details class="terminal-msg agent-thinking">
            <summary><span style="color:#c586c0;font-weight:bold;">[THINK]</span> process (${m.time || "..."})</summary>
            <div class="think-content">${escHtml(m.text)}</div>
          </details>
        `;
      } else if (m.role === "tool") {
        const isErr = m.isError ? "error" : "";
        html += `
          <div class="terminal-msg tool-call">
            <div class="tool-cmd"><span style="color:#dcdcaa;font-weight:bold;">[EXEC]</span> $ ${escHtml(m.cmd)}</div>
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
