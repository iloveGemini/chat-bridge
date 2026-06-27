import { api } from "./api.js";
import { store } from "./store.js";

export class VConsole {
  constructor() {
    this.fab = document.getElementById("vconsole-fab");
    this.panel = document.getElementById("vconsole-panel");
    this.body = document.getElementById("vc-body");
    this.btnClose = document.getElementById("vc-close");
    this.btnClear = document.getElementById("vc-clear");

    this.feedTimer = null;
    this._logSeq = 0; // 已拉取到的后端日志游标
    this._side = localStorage.getItem("vconsole_side") || "right"; // 吸附在哪一侧
    this._tuckTimer = null; // 空闲后把球缩进边缘的定时器

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

      this.btnPrompt.onclick = () => this.showLastPrompt();
    }

    if (this.fab) {
      this.initDraggable();
      this.syncVisibility();
      // 鼠标移上去/触摸时探出来，移开一会儿后自动缩回边缘
      this.fab.addEventListener("mouseenter", () => this.peekOut());
      this.fab.addEventListener("mouseleave", () => this.scheduleTuck());
      this.scheduleTuck(); // 初始就缩到边缘当个不打扰的小球
    }
    if (this.btnClose) this.btnClose.onclick = () => this.hide();
    if (this.btnClear) this.btnClear.onclick = () => this.clear();
  }

  syncVisibility() {
    const enabled = localStorage.getItem("vconsole_en") === "1";
    if (this.fab) this.fab.style.display = enabled ? "flex" : "none";
    if (!enabled && this.panel.classList.contains("show")) this.hide();
  }

  // 把球缩到屏幕边缘，只露出一小半、半透明，不打扰阅读
  tuck() {
    if (!this.fab) return;
    if (this.panel && this.panel.classList.contains("show")) return; // 面板开着时不缩
    this.fab.style.transition = "transform 0.3s, opacity 0.3s";
    const dir = this._side === "left" ? "-55%" : "55%";
    this.fab.style.transform = `translateX(${dir})`;
    this.fab.style.opacity = "0.4";
  }

  // 完整探出，恢复不透明
  peekOut() {
    if (!this.fab) return;
    if (this._tuckTimer) {
      clearTimeout(this._tuckTimer);
      this._tuckTimer = null;
    }
    this.fab.style.transform = "translateX(0)";
    this.fab.style.opacity = "1";
  }

  // 空闲若干秒后自动缩回边缘
  scheduleTuck(delay = 2000) {
    if (this._tuckTimer) clearTimeout(this._tuckTimer);
    this._tuckTimer = setTimeout(() => this.tuck(), delay);
  }

  // 一键查看最近一次发给模型的 prompt：
  // 在 Code Agent 界面 -> 拉 agent 的 payload；否则 -> 拉当前聊天会话的 /api/debug/last_prompt
  async showLastPrompt() {
    if (!this.panel.classList.contains("show")) this.show();
    const agentView = document.getElementById("code-agent-view");
    const inAgent = agentView && agentView.classList.contains("show");

    if (inAgent) {
      import("./views/codeAgentView.js").then((m) => {
        m.codeAgentView.dumpLastPrompt();
        this.log("[System] 已请求 Code Agent 的最近 Prompt", "#56b6ff");
      });
      return;
    }

    // 普通聊天：用当前激活会话拉调试 payload
    // 普通聊天：用当前激活会话拉调试 payload
    const sid = (store.getState && store.getState().activeSessionId) || null;
    if (!sid) {
      this.log("[System] 当前没有激活的聊天会话", "#ffae57");
      return;
    }
    try {
      const lp = await api.debugLastPrompt(sid);
      if (!lp || lp.error || !lp.messages) {
        this.log(`[System] 该会话暂无 Prompt 记录（${sid}）`, "#ffae57");
        return;
      }

      const totalTokensInfo = lp.tokens ? ` | 总估算 Tokens: ${lp.tokens}` : "";
      this.log(
        `\n===== chat last_prompt session=${sid} model=${lp.model} @${lp.ts}${totalTokensInfo} =====`,
        "#ffae57",
      );

      (lp.messages || []).forEach((m, i) => {
        const c =
          typeof m.content === "string" ? m.content : JSON.stringify(m.content);

        // 在 UI 打印角色头部
        this.log(`[#${i} ${m.role}] ----------------`, "#e5c07b");

        // 在 F12 控制台开启一个可折叠的 Message 组
        console.groupCollapsed(`[#${i} ${m.role}] 完整折叠视图 (点击展开)`);

        const xmlRegex = /<([a-zA-Z0-9_:-]+)(?:\s+[^>]*)?>([\s\S]*?)<\/\1>/g;
        let match;
        let lastIndex = 0;

        while ((match = xmlRegex.exec(c)) !== null) {
          // 1. 处理 XML 标签外的纯文本
          if (match.index > lastIndex) {
            const textBefore = c.substring(lastIndex, match.index).trim();
            if (textBefore) {
              this.log(textBefore, "#9cdcfe"); // 打印到 UI
              console.log(textBefore); // 打印到 F12
            }
          }

          // 2. 处理提取出的 XML 块
          const tagName = match[1];
          const body = match[2];
          const subTokens = Math.ceil(body.trim().length / 2); // 粗算 Token

          // UI 上的纯文本视觉分割（换个紫色高亮，更醒目）
          this.log(
            `\n▼ ▼ ▼ <${tagName}> (约 ${subTokens} Tokens) ▼ ▼ ▼`,
            "#c678dd",
          );
          this.log(body.trim() || "(无内容)", "#9cdcfe");
          this.log(`▲ ▲ ▲ /${tagName} 结束 ▲ ▲ ▲\n`, "#c678dd");

          // F12 控制台里的原生折叠卡片！
          console.groupCollapsed(`<${tagName}> (约 ${subTokens} Tokens)`);
          console.log(body.trim() || "(无内容)");
          console.groupEnd();

          lastIndex = xmlRegex.lastIndex;
        }

        // 3. 处理结尾剩下的纯文本
        if (lastIndex < c.length) {
          const textAfter = c.substring(lastIndex).trim();
          if (textAfter) {
            this.log(textAfter, "#9cdcfe");
            console.log(textAfter);
          }
        }

        console.groupEnd(); // 结束 F12 里的这条 Message 组
      });

      this.log(
        `===== end (${(lp.messages || []).length} 条) =====\n`,
        "#ffae57",
      );
    } catch (err) {
      this.log("[System] 获取 Prompt 失败: " + err.message, "#ff5f56");
    }
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
      this.peekOut(); // 抓起来时先完整探出，方便操作
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
          "left 0.3s cubic-bezier(0.25, 0.8, 0.25, 1), transform 0.3s, opacity 0.3s";
        const rect = this.fab.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;

        if (centerX < window.innerWidth / 2) {
          this.fab.style.left = "10px"; // 吸附到左边缘
          this._side = "left";
        } else {
          this.fab.style.left = window.innerWidth - rect.width - 10 + "px"; // 吸附到右边缘
          this._side = "right";
        }
        localStorage.setItem("vconsole_side", this._side);
      }
      startX = 0;
      startY = 0;
      this.scheduleTuck();
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
    this.peekOut(); // 面板打开时球完整显示，别缩着
    this.panel.classList.add("show");
    this.log("[System] vConsole 已挂载，开始接收后端日志…");
    this.startFeed();
  }

  hide() {
    this.panel.classList.remove("show");
    this.stopFeed();
    this.scheduleTuck(); // 关掉面板后球重新缩回边缘
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
