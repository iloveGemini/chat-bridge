import { store } from "../store.js";
import { api } from "../api.js";
import { router } from "../router.js";
import {
  escHtml,
  getFallbackAvatar,
  actionSheet,
  selectSheet,
  showToast,
  formatTime,
  ICONS,
} from "../utils.js";
import { chatMultiSelectView } from "./chatMultiSelectView.js";

class ChatsView {
  constructor() {
    this.container = document.getElementById("chats-list");
    this.sessions = [];
    this._bound = false;
    this.activeSwipeEl = null; // 当前滑开的会话卡片
  }

  // 事件绑定：处理滑动与点击
  bindOnce() {
    if (this._bound) return;
    this._bound = true;

    // 我们使用 document.getElementById 确保拿到最新的 DOM
    const listEl = document.getElementById("chats-list");
    if (!listEl) return;

    let startX = 0,
      currentX = 0;

    // 滑动开始
    listEl.addEventListener(
      "touchstart",
      (ev) => {
        const item = ev.target.closest(".chat-item-inner");
        if (!item) return;
        // 点击其他地方时，收起已经滑开的菜单
        if (this.activeSwipeEl && this.activeSwipeEl !== item) {
          this.activeSwipeEl.style.transform = `translateX(0px)`;
          this.activeSwipeEl = null;
        }
        startX = ev.touches[0].clientX;
        currentX = startX;
      },
      { passive: true },
    );

    // 滑动中
    listEl.addEventListener(
      "touchmove",
      (ev) => {
        const item = ev.target.closest(".chat-item-inner");
        if (!item) return;
        currentX = ev.touches[0].clientX;
        let diff = currentX - startX;
        // 允许向左滑动 (3个按钮共 195px)
        if (diff < 0) {
          item.style.transform = `translateX(${Math.max(diff, -195)}px)`;
          item.style.transition = "none";
        }
      },
      { passive: true },
    );

    // 滑动结束
    listEl.addEventListener("touchend", (ev) => {
      const item = ev.target.closest(".chat-item-inner");
      if (!item) return;
      let diff = currentX - startX;
      item.style.transition = "transform 0.2s ease-out";

      // 滑动超过 60px 自动展开，否则收回
      if (diff < -60) {
        item.style.transform = `translateX(-195px)`;
        this.activeSwipeEl = item;
      } else {
        item.style.transform = `translateX(0px)`;
        if (this.activeSwipeEl === item) this.activeSwipeEl = null;
      }
    });

    // 统一处理点击事件
    listEl.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".chat-action-btn");
      const item = ev.target.closest(".chat-item-inner");

      // 1. 点击了底部的操作按钮
      if (btn) {
        const action = btn.dataset.action;
        const sessionId = btn.dataset.id;
        const session = this.sessions.find((s) => s.id === sessionId);
        if (!session) return;

        if (action === "more") {
          // 调用原有的底部弹窗
          this.showActions(session);
          // 收起侧滑
          if (this.activeSwipeEl) {
            this.activeSwipeEl.style.transform = "translateX(0px)";
            this.activeSwipeEl = null;
          }
        } else {
          // pin 或 delete
          this.handleAction(action, session);
        }
        return;
      }

      // 2. 点击了会话卡片本身
      if (item && item !== this.activeSwipeEl) {
        router.pushView("chat-room", {
          id: item.dataset.id,
          name: item.dataset.name,
        });
      }
    });
  }

  async refresh() {
    this.container = document.getElementById("chats-list");
    this.bindOnce(); // 确保绑定了滑动事件
    try {
      const data = await api.fetchSessions();
      this.sessions = data.sessions || [];
      store.setState({ sessions: this.sessions });
      this.render();
    } catch (e) {
      console.error("加载会话失败", e);
      if (this.container)
        this.container.innerHTML =
          '<div style="text-align:center;padding:40px;color:var(--text-secondary);">无法连接服务器</div>';
    }
  }

  render() {
    if (!this.container) return;
    const list = this.sessions
      .slice()
      .sort(
        (a, b) =>
          (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) ||
          (b.updated_at || 0) - (a.updated_at || 0),
      );
    if (list.length === 0) {
      this.container.innerHTML =
        '<div style="text-align:center;padding:40px;color:var(--text-secondary);">暂无对话，点击右上角发起</div>';
      return;
    }

    this.container.innerHTML = list
      .map((s) => {
        const name = s.character_name || s.character || s.id;
        const avatar = s.avatar || getFallbackAvatar(name);
        const time = s.updated_at ? formatTime(s.updated_at * 1000) : "";
        const pinnedCls = s.pinned ? " pinned" : "";

        const pinText = s.pinned ? "取消<br>置顶" : "置顶";
        const pinBg = s.pinned ? "#8c8c8c" : "#007acc";

        return `
        <div class="chat-swipe-wrap">
          <div class="chat-actions">
            <div class="chat-action-btn" data-action="pin" data-id="${escHtml(s.id)}" style="background:${pinBg};">${pinText}</div>
            <div class="chat-action-btn" data-action="more" data-id="${escHtml(s.id)}" style="background:#e6a23c;">更多</div>
            <div class="chat-action-btn" data-action="delete" data-id="${escHtml(s.id)}" style="background:#f56c6c;">删除</div>
          </div>
          
          <div class="list-item chat-item-inner${pinnedCls}" data-id="${escHtml(s.id)}" data-name="${escHtml(name)}">
            <img class="avatar" style="object-fit:cover;" src="${avatar}">
            <div class="info">
              <div class="name">${escHtml(name)}
                <span style="float:right;font-size:12px;color:var(--text-faint);font-weight:normal;">${time}</span>
              </div>
              <div class="msg">${escHtml(s.preview || "...")}</div>
            </div>
          </div>
        </div>`;
      })
      .join("");
  }

  showActions(s) {
    actionSheet(
      [
        { label: s.pinned ? "取消置顶" : "置顶", action: "pin" },
        { label: "改名", action: "rename" },
        { label: "开始新聊天（同角色）", action: "new" },
        { label: "克隆聊天", action: "clone" },
        { label: "清空消息", action: "clear" },
        { label: "删除", action: "delete", destructive: true },
      ],
      (act) => this.handleAction(act, s),
    );
  }

  async handleAction(act, s) {
    try {
      if (act === "pin") {
        const r = await api.pinSession(s.id);
        if (r.ok) {
          showToast(r.pinned ? "已置顶" : "已取消置顶");
          this.refresh();
        }
      } else if (act === "rename") {
        const name = prompt("输入新名称：", s.character_name || s.id);
        if (!name || !name.trim()) return;
        const r = await api.renameSession(s.id, name.trim());
        if (r.ok) {
          showToast("已改名");
          this.refresh();
        } else showToast(r.error || "改名失败");
      } else if (act === "new") {
        this.createAndOpen(s.character || "default");
      } else if (act === "clone") {
        const r = await api.cloneSession(s.id);
        if (r.ok) {
          showToast("已克隆");
          this.refresh();
        } else showToast(r.error || "克隆失败");
      } else if (act === "clear") {
        if (!confirm("清空该会话所有消息？")) return;
        const r = await api.clear(s.id);
        if (r.ok) {
          showToast("已清空");
          this.refresh();
        }
      } else if (act === "delete") {
        if (this.sessions.length <= 1) {
          showToast("至少保留一个会话");
          return;
        }
        if (!confirm("删除该会话？不可恢复。")) return;
        const r = await api.deleteSession(s.id);
        if (r.ok) {
          showToast("已删除");
          this.refresh();
        } else showToast(r.error || "删除失败");
      }
    } catch (e) {
      showToast("操作失败");
    }
  }

  // 新建会话：先选角色
  async startNewChatFlow() {
    const btn = document.getElementById("chats-new-btn");
    const rect = btn
      ? btn.getBoundingClientRect()
      : { right: window.innerWidth - 15, bottom: 50 };

    // 1. 弹出右上角 Popover 气泡菜单
    const mask = document.createElement("div");
    mask.className = "popover-mask";

    const popover = document.createElement("div");
    popover.className = "popover-box";
    popover.style.top = rect.bottom + 8 + "px";
    popover.style.right = window.innerWidth - rect.right + "px";

    popover.innerHTML = `
      <div class="popover-item" id="pop-chat">
        <span style="color:var(--text-secondary);display:flex;">${ICONS.chat}</span>
        <span>发起聊天</span>
      </div>
      <div class="popover-item" id="pop-char">
        <span style="color:var(--text-secondary);display:flex;">${ICONS.userPlus}</span>
        <span>新建角色</span>
      </div>
    `;

    const dismiss = () => {
      popover.remove();
      mask.remove();
    };
    mask.onclick = dismiss;

    popover.querySelector("#pop-chat").onclick = () => {
      dismiss();
      chatMultiSelectView.open();
    };
    popover.querySelector("#pop-char").onclick = () => {
      dismiss();
      import("../modals.js").then((m) =>
        m.openPromptEditor("character", null, () => {
          import("./contactsView.js").then((cv) => cv.contactsView.refresh());
        }),
      );
    };

    document.body.appendChild(mask);
    document.body.appendChild(popover);
  }

  async createAndOpen(charKey) {
    try {
      const data = await api.createSession(charKey);
      if (data.ok) {
        await this.refresh();
        const s = this.sessions.find((x) => x.id === data.session_id);
        router.pushView("chat-room", {
          id: data.session_id,
          name: (s && s.character_name) || charKey,
        });
      } else showToast("无法发起对话");
    } catch (e) {
      showToast("发起对话失败");
    }
  }
}

export const chatsView = new ChatsView();
