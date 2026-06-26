import { api } from "./api.js";

export class VConsole {
  constructor() {
    this.fab = document.getElementById("vconsole-fab");
    this.panel = document.getElementById("vconsole-panel");
    this.body = document.getElementById("vc-body");
    this.btnClose = document.getElementById("vc-close");
    this.btnClear = document.getElementById("vc-clear");

    this.feedTimer = null;
    this._logSeq = 0; // 已拉取到的后端日志游标

    // 动态创建 Prompt 按钮，追加到 Clear 按钮旁边
    if (this.btnClear && !document.getElementById("vc-prompt-btn")) {
      this.btnPrompt = document.createElement("button");
      this.btnPrompt.id = "vc-prompt-btn";
      this.btnPrompt.textContent = "Last Prompt";
      this.btnPrompt.style.cssText =
        "margin-left: 8px; padding: 2px 8px; background: transparent; color: #56b6ff; border: 1px solid #56b6ff; border-radius: 4px; cursor: pointer;";

      this.btnClear.parentNode.insertBefore(
        this.btnPrompt,
        this.btnClear.nextSibling,
      );

      this.btnPrompt.onclick = () => {
        // 巧妙调用：直接触发 CodeAgentView 里现成的设置按钮点击事件
        const agentSettingsBtn = document.getElementById("ca-settings-btn");
        if (agentSettingsBtn) {
          agentSettingsBtn.click();
          this.log(
            "[System] 已请求输出最后一次 Prompt，请查看下方日志",
            "#56b6ff",
          );
        } else {
          this.log(
            "[System] 当前不在 Agent 界面，或未找到 Prompt 按钮",
            "#ffae57",
          );
        }
      };
    }

    if (this.fab) {
      this.initDraggable();
      this.syncVisibility();
    }
    if (this.btnClose) this.btnClose.onclick = () => this.hide();
    if (this.btnClear) this.btnClear.onclick = () => this.clear();
  }

  syncVisibility() {
    const enabled = localStorage.getItem("vconsole_en") === "1";
    if (this.fab) this.fab.style.display = enabled ? "flex" : "none";
    if (!enabled && this.panel.classList.contains("show")) this.hide();
  }

  initDraggable() {
    let isDragging = false;
    let startX = 0,
      startY = 0;
    let startLeft = 0,
      startTop = 0;

    const handleStart = (x, y) => {
      startX = x;
      startY = y;
      const rect = this.fab.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      isDragging = false;
      // 拖拽时取消动画过渡，跟随手指更紧密
      this.fab.style.transition = "none";
    };

    const handleMove = (x, y) => {
      if (!startX && !startY) return; // 尚未触发 start
      const dx = x - startX;
      const dy = y - startY;

      // 移动超过 5px 判定为拖拽，而非点击
      if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
        isDragging = true;
        let newX = startLeft + dx;
        let newY = startTop + dy;

        // 边界限制，防止拖出屏幕外
        const maxX = window.innerWidth - this.fab.offsetWidth;
        const maxY = window.innerHeight - this.fab.offsetHeight;
        newX = Math.max(0, Math.min(maxX, newX));
        newY = Math.max(0, Math.min(maxY, newY));

        this.fab.style.left = newX + "px";
        this.fab.style.top = newY + "px";
        this.fab.style.right = "auto";
        this.fab.style.bottom = "auto";
      }
    };

    const handleEnd = () => {
      if (isDragging) {
        // 松手时，计算悬浮球在中线的左侧还是右侧，自动吸附边缘
        this.fab.style.transition =
          "left 0.3s cubic-bezier(0.25, 0.8, 0.25, 1)";
        const rect = this.fab.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;

        if (centerX < window.innerWidth / 2) {
          this.fab.style.left = "10px"; // 吸附到左边缘
        } else {
          this.fab.style.left = window.innerWidth - rect.width - 10 + "px"; // 吸附到右边缘
        }
      }
      startX = 0;
      startY = 0;
    };

    // 触摸屏事件绑定
    this.fab.addEventListener(
      "touchstart",
      (e) => handleStart(e.touches[0].clientX, e.touches[0].clientY),
      { passive: true },
    );
    this.fab.addEventListener(
      "touchmove",
      (e) => handleMove(e.touches[0].clientX, e.touches[0].clientY),
      { passive: true },
    );
    this.fab.addEventListener("touchend", handleEnd);

    // 鼠标事件绑定
    this.fab.addEventListener("mousedown", (e) =>
      handleStart(e.clientX, e.clientY),
    );
    window.addEventListener("mousemove", (e) => {
      if (e.buttons === 1 && startX) handleMove(e.clientX, e.clientY);
    });
    window.addEventListener("mouseup", handleEnd);

    // 点击展开/收起面板
    this.fab.onclick = (e) => {
      if (isDragging) {
        e.preventDefault();
        return;
      }
      if (this.panel.classList.contains("show")) this.hide();
      else this.show();
    };
  }

  show() {
    this.panel.classList.add("show");
    this.log("[System] vConsole 已挂载，开始接收后端日志…");
    this.startFeed();
  }

  hide() {
    this.panel.classList.remove("show");
    this.stopFeed();
  }

  clear() {
    if (this.body)
      this.body.innerHTML =
        '<div style="color:#888;">[System] 屏幕已清空</div>';
  }

  startFeed() {
    if (this.feedTimer) return;
    const tick = async () => {
      try {
        const res = await api.fetchLogs(this._logSeq);
        if (res && res.ok && res.logs && res.logs.length) {
          res.logs.forEach((e) => {
            this._logSeq = Math.max(this._logSeq, e.id);
            this.log(e.line, this._colorFor(e.line), e.ts);
          });
        }
      } catch (err) {
        /* 静默：拉日志失败不打扰 */
      }
    };
    tick();
    this.feedTimer = setInterval(tick, 1000);
  }

  stopFeed() {
    if (this.feedTimer) {
      clearInterval(this.feedTimer);
      this.feedTimer = null;
    }
  }

  _colorFor(line) {
    if (/⚠|错误|error|失败|exit [1-9]/i.test(line)) return "#ff5f56";
    if (/↗ LLM|限流/.test(line)) return "#56b6ff";
    if (/⚙ tool|↩/.test(line)) return "#dcdcaa";
    if (/🚀 fanout|worker/.test(line)) return "#4ec9b0";
    if (/📌 进度卡/.test(line)) return "#c586c0";
    if (/📥/.test(line)) return "#ffae57";
    if (/⏹/.test(line)) return "#ff8787";
    return "#9cdc8a";
  }

  log(msg, color = "#0f0", ts) {
    if (!this.body) return;

    // 【核心防滚屏判定】：检查当前视口底部距离真实内容底部是否小于 30px（容差）
    const isAtBottom =
      Math.abs(
        this.body.scrollHeight - this.body.scrollTop - this.body.clientHeight,
      ) < 30;

    const line = document.createElement("div");
    line.style.color = color;
    line.style.whiteSpace = "pre-wrap";
    line.style.wordBreak = "break-all";
    line.style.lineHeight = "1.4";
    line.style.marginBottom = "6px";
    line.style.borderBottom = "1px dashed #333";
    line.style.paddingBottom = "4px";

    let formattedMsg = msg;
    if (typeof formattedMsg === "string") {
      formattedMsg = formattedMsg.replace(/\\n/g, "\n");
    }

    line.textContent = `[${ts || new Date().toLocaleTimeString()}] ${formattedMsg}`;
    this.body.appendChild(line);

    // 限制 DOM 行数，避免长跑卡顿
    while (this.body.childNodes.length > 600) {
      this.body.removeChild(this.body.firstChild);
    }

    // 如果插入前用户就在底部，那么自动跟随到底部；否则保持用户当前的阅读位置不动
    if (isAtBottom) {
      this.body.scrollTop = this.body.scrollHeight;
    }
  }
}
