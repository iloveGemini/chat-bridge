import { formatTime } from "../utils.js";
import { codeAgentView } from "./codeAgentView.js";

class CodeAgentHubView {
  constructor() {
    this._bound = false;
    this.tasks = [
      {
        id: "t1",
        title: "重构前端 API 模块",
        status: "进行中",
        progress: 40,
        updatedAt: new Date().toISOString(),
      },
      {
        id: "t2",
        title: "修复 Login 页面样式 Bug",
        status: "已挂起",
        progress: 80,
        updatedAt: new Date(Date.now() - 86400000).toISOString(),
      },
    ];
    this.activeSwipeEl = null; // 当前滑开的元素
  }

  els() {
    return {
      list: document.getElementById("ca-task-list"),
      newBtn: document.getElementById("ca-new-task-btn"),
    };
  }

  bindOnce() {
    if (this._bound) return;
    this._bound = true;
    const e = this.els();

    e.newBtn.addEventListener("click", () => {
      const title = prompt("请输入新任务名称:", "新需求开发");
      if (!title) return;
      const newTask = {
        id: "t" + Date.now(),
        title,
        status: "准备就绪",
        progress: 0,
        updatedAt: new Date().toISOString(),
      };
      this.tasks.unshift(newTask);
      this.render();
      codeAgentView.open(newTask.id, newTask.title);
    });

    // === 左滑逻辑 ===
    let startX = 0,
      currentX = 0;

    e.list.addEventListener(
      "touchstart",
      (ev) => {
        const item = ev.target.closest(".ca-task-item");
        if (!item) return;
        // 点击其他地方时，收起已经打开的滑块
        if (this.activeSwipeEl && this.activeSwipeEl !== item) {
          this.activeSwipeEl.style.transform = `translateX(0px)`;
          this.activeSwipeEl = null;
        }
        startX = ev.touches[0].clientX;
        currentX = startX;
      },
      { passive: true },
    );

    e.list.addEventListener(
      "touchmove",
      (ev) => {
        const item = ev.target.closest(".ca-task-item");
        if (!item) return;
        currentX = ev.touches[0].clientX;
        let diff = currentX - startX;
        // 只允许向左滑动 (3个按钮，每个60px，最大滑动-180px)
        if (diff < 0) {
          item.style.transform = `translateX(${Math.max(diff, -180)}px)`;
          item.style.transition = "none";
        }
      },
      { passive: true },
    );

    e.list.addEventListener("touchend", (ev) => {
      const item = ev.target.closest(".ca-task-item");
      if (!item) return;
      let diff = currentX - startX;
      item.style.transition = "transform 0.2s ease-out";

      // 滑动超过 60px 自动展开，否则收回
      if (diff < -60) {
        item.style.transform = `translateX(-180px)`;
        this.activeSwipeEl = item;
      } else {
        item.style.transform = `translateX(0px)`;
        if (this.activeSwipeEl === item) this.activeSwipeEl = null;
      }
    });

    // === 列表点击代理 (包含按钮和进入终端) ===
    e.list.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".ca-task-action-btn");
      const item = ev.target.closest(".ca-task-item");

      if (btn && item) {
        // 点击了后面的操作按钮
        const action = btn.dataset.action;
        const taskId = item.dataset.id;

        if (action === "delete") {
          this.tasks = this.tasks.filter((t) => t.id !== taskId);
        } else if (action === "archive") {
          const t = this.tasks.find((t) => t.id === taskId);
          if (t) t.status = "已归档";
        } else if (action === "pin") {
          const idx = this.tasks.findIndex((t) => t.id === taskId);
          if (idx > 0) {
            const [t] = this.tasks.splice(idx, 1);
            this.tasks.unshift(t);
          }
        }
        // 重置滑动状态并重新渲染
        this.activeSwipeEl = null;
        this.render();
        return;
      }

      // 如果点击主体且当前元素没有被滑开，则进入终端
      if (item && item !== this.activeSwipeEl) {
        codeAgentView.open(item.dataset.id, item.dataset.title);
      }
    });
  }

  open() {
    window.router.pushView("code-agent-hub-view");
    this.bindOnce();
    this.render();
  }

  render() {
    const e = this.els();
    if (!e.list) return;

    if (this.tasks.length === 0) {
      e.list.innerHTML =
        '<div style="text-align:center; color:#888; margin-top:50px;">暂无任务</div>';
      return;
    }

    e.list.innerHTML = this.tasks
      .map(
        (t) => `
      <div class="ca-task-swipe-wrap">
        <div class="ca-task-actions">
          <div class="ca-task-action-btn btn-pin" data-action="pin">置顶</div>
          <div class="ca-task-action-btn btn-archive" data-action="archive">归档</div>
          <div class="ca-task-action-btn btn-delete" data-action="delete">删除</div>
        </div>
        
        <div class="ca-task-item" data-id="${t.id}" data-title="${t.title}">
          <div style="display:flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <strong style="font-size: 16px; color: var(--text-color);">${t.title}</strong>
            <span style="font-size: 11px; color: #007acc; background: rgba(0,122,204,0.1); padding: 2px 6px; border-radius: 4px;">${t.status}</span>
          </div>
          <div style="height: 4px; background: var(--border-color); border-radius: 2px; margin-bottom: 8px; overflow: hidden;">
            <div style="width: ${t.progress}%; height: 100%; background: #007acc;"></div>
          </div>
          <div style="font-size: 12px; color: var(--text-secondary);">更新于: ${formatTime(t.updatedAt)}</div>
        </div>
      </div>
    `,
      )
      .join("");
  }
}

export const codeAgentHubView = new CodeAgentHubView();
