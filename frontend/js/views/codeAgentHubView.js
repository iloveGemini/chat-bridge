import { formatTime, showToast } from "../utils.js";
import { api } from "../api.js";
import { codeAgentView } from "./codeAgentView.js";

const BTN = "padding:6px 12px;border:1px solid #3a3a3a;border-radius:5px;background:#2d2d2d;color:#ddd;cursor:pointer;font-size:13px;";
const BTN_PRIMARY = "padding:6px 12px;border:1px solid #0a6cc4;border-radius:5px;background:#0a6cc4;color:#fff;cursor:pointer;font-size:13px;";

class CodeAgentHubView {
  constructor() {
    this._bound = false;
    this.tasks = [];
    this.activeSwipeEl = null;
  }

  els() {
    return {
      list: document.getElementById("ca-task-list"),
      newBtn: document.getElementById("ca-new-task-btn"),
    };
  }

  _map(t) {
    return {
      id: t.id,
      title: t.title,
      status: t.status,
      progress: t.progress || 0,
      updatedAt: t.updated_at || t.created_at || new Date().toISOString(),
    };
  }

  async refresh() {
    try {
      const res = await api.agentTasks();
      this.tasks = (res && res.ok ? res.tasks : [])
        .filter((t) => t.status !== "已归档")
        .map((t) => this._map(t));
    } catch (e) {
      this.tasks = [];
    }
    this.render();
  }

  // 服务端文件夹选择器。resolve 为 {mode:'in_place',dir} / {mode:'blank'} / null(取消)
  pickFolder() {
    return new Promise((resolve) => {
      const ov = document.createElement("div");
      ov.style.cssText =
        "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;display:flex;align-items:center;justify-content:center;";
      const box = document.createElement("div");
      box.style.cssText =
        "width:92%;max-width:520px;max-height:82vh;display:flex;flex-direction:column;background:#1e1e1e;color:#ddd;border:1px solid #333;border-radius:8px;overflow:hidden;";
      box.innerHTML =
        '<div style="padding:11px 14px;border-bottom:1px solid #333;font-weight:bold;font-size:14px;">选择项目文件夹（Agent 将直接在其中工作，请先备份/提交 git）</div>' +
        '<div id="_fp_path" style="padding:8px 14px;color:#9cdcfe;font-size:12px;word-break:break-all;background:#252526;"></div>' +
        '<div id="_fp_list" style="flex:1;overflow:auto;min-height:180px;"></div>' +
        '<div style="padding:10px 14px;border-top:1px solid #333;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;">' +
        '<button id="_fp_up" style="' + BTN + '">上一级</button>' +
        '<button id="_fp_blank" style="' + BTN + '">空白项目</button>' +
        '<button id="_fp_cancel" style="' + BTN + '">取消</button>' +
        '<button id="_fp_pick" style="' + BTN_PRIMARY + '">选此目录</button>' +
        "</div>";
      ov.appendChild(box);
      document.body.appendChild(ov);

      let cur = "";
      const q = (sel) => box.querySelector(sel);
      const close = (val) => {
        document.body.removeChild(ov);
        resolve(val);
      };
      const load = async (path) => {
        try {
          const res = await api.fsList(path);
          if (!res || !res.ok) {
            showToast((res && res.error) || "读取目录失败");
            return;
          }
          cur = res.path || "";
          q("#_fp_path").textContent = cur || "（根 / 盘符）";
          const list = q("#_fp_list");
          list.dataset.parent = res.parent || "";
          list.innerHTML =
            (res.dirs || [])
              .map((d) => {
                const name = d.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || d;
                const safe = d.replace(/"/g, "&quot;");
                return (
                  '<div class="_fp_item" data-path="' +
                  safe +
                  '" style="padding:10px 14px;border-bottom:1px solid #2a2a2a;cursor:pointer;">📁 ' +
                  name +
                  "</div>"
                );
              })
              .join("") ||
            '<div style="padding:14px;color:#888;">（无子目录）</div>';
        } catch (e) {
          showToast("读取目录失败: " + e.message);
        }
      };

      q("#_fp_list").addEventListener("click", (ev) => {
        const it = ev.target.closest("._fp_item");
        if (it) load(it.dataset.path);
      });
      q("#_fp_up").onclick = () => load(q("#_fp_list").dataset.parent || "");
      q("#_fp_cancel").onclick = () => close(null);
      q("#_fp_blank").onclick = () => close({ mode: "blank" });
      q("#_fp_pick").onclick = () => {
        if (!cur) {
          showToast("请先进入一个目录");
          return;
        }
        close({ mode: "in_place", dir: cur });
      };
      ov.addEventListener("click", (ev) => {
        if (ev.target === ov) close(null);
      });
      load(""); // 从盘符/根开始
    });
  }

  bindOnce() {
    if (this._bound) return;
    this._bound = true;
    const e = this.els();

    e.newBtn.addEventListener("click", async () => {
      const title = prompt("请输入新任务名称:", "新需求开发");
      if (!title) return;
      const pick = await this.pickFolder();
      if (pick === null) return; // 取消
      const payload = { title, goal: title };
      if (pick.mode === "in_place" && pick.dir) payload.work_dir = pick.dir;
      try {
        const res = await api.agentCreate(payload);
        if (res && res.ok && res.task) {
          this.tasks.unshift(this._map(res.task));
          this.render();
          codeAgentView.open(res.task.id, res.task.title);
        }
      } catch (err) {
        alert("创建失败: " + err.message);
      }
    });

    // === 左滑逻辑 ===
    let startX = 0,
      currentX = 0;

    e.list.addEventListener(
      "touchstart",
      (ev) => {
        const item = ev.target.closest(".ca-task-item");
        if (!item) return;
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
      if (diff < -60) {
        item.style.transform = `translateX(-180px)`;
        this.activeSwipeEl = item;
      } else {
        item.style.transform = `translateX(0px)`;
        if (this.activeSwipeEl === item) this.activeSwipeEl = null;
      }
    });

    // === 列表点击代理 ===
    e.list.addEventListener("click", async (ev) => {
      const btn = ev.target.closest(".ca-task-action-btn");

      // 操作按钮与 .ca-task-item 是兄弟节点，要从 swipe-wrap 里取 item
      if (btn) {
        const wrap = btn.closest(".ca-task-swipe-wrap");
        const itemEl = wrap && wrap.querySelector(".ca-task-item");
        const taskId = itemEl && itemEl.dataset.id;
        if (!taskId) return;
        const action = btn.dataset.action;
        try {
          if (action === "delete") {
            if (!window.confirm("确定删除该任务？此操作不可撤销。")) return;
            await api.agentDelete(taskId);
          } else if (action === "archive") {
            await api.agentUpdate({ task_id: taskId, status: "已归档" });
          } else if (action === "pin") {
            // 触发后端 updated_at 刷新即可冒泡到顶（列表按更新时间倒序）
            const cur = this.tasks.find((t) => t.id === taskId);
            await api.agentUpdate({
              task_id: taskId,
              status: (cur && cur.status) || "就绪",
            });
          }
        } catch (err) {
          alert("操作失败: " + err.message);
        }
        this.activeSwipeEl = null;
        await this.refresh(); // 从后端重新拉取，状态真实
        return;
      }

      const item = ev.target.closest(".ca-task-item");
      if (item && item !== this.activeSwipeEl) {
        codeAgentView.open(item.dataset.id, item.dataset.title);
      }
    });
  }

  open() {
    window.router.pushView("code-agent-hub-view");
    this.bindOnce();
    this.refresh();
  }

  render() {
    const e = this.els();
    if (!e.list) return;

    if (this.tasks.length === 0) {
      e.list.innerHTML =
        '<div style="text-align:center; color:#888; margin-top:50px;">暂无任务，点右上角新建</div>';
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
