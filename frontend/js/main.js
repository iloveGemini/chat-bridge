import { router } from "./router.js";
import { store } from "./store.js";
import { applyTheme } from "./utils.js";
import { VConsole } from "./vconsole.js";

window.vConsole = new VConsole(); // 全局挂载，以后其他模块也能 window.vConsole.log()

// 暴露给 HTML 内联 onclick
window.router = router;

// 应用初始主题
applyTheme(store.getState().config.theme || "dark");

document.addEventListener("DOMContentLoaded", () => {
  // 标签切换
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () =>
      router.switchTab(btn.getAttribute("data-target")),
    );
  });

  // 新建会话按钮（消息页右上角）
  const newBtn = document.getElementById("chats-new-btn");
  if (newBtn)
    newBtn.addEventListener("click", () => {
      import("./views/chatsView.js").then((m) =>
        m.chatsView.startNewChatFlow(),
      );
    });

  // 聊天室设置按钮
  const settBtn = document.getElementById("chat-room-settings");
  if (settBtn)
    settBtn.addEventListener("click", () => {
      import("./views/chatView.js").then((m) => m.chatView.openRoomSettings());
    });

  // Android/浏览器返回键：优先弹出二级页
  window.addEventListener("popstate", () => {
    if (router.history.length > 0) {
      router.popView();
      history.pushState(null, null, location.pathname);
    }
  });
  history.pushState(null, null, location.pathname);

  // ====== 全局边缘手势：向右滑动返回上一页 ======
  let touchStartX = 0;
  let touchStartY = 0;

  document.addEventListener(
    "touchstart",
    (e) => {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    },
    { passive: true },
  );

  document.addEventListener("touchend", (e) => {
    const touchEndX = e.changedTouches[0].clientX;
    const touchEndY = e.changedTouches[0].clientY;
    const deltaX = touchEndX - touchStartX;
    const deltaY = touchEndY - touchStartY;

    // 判断逻辑：
    // 1. 起始点在屏幕左边缘 30px 以内 (模拟系统边缘手势)
    // 2. 向右滑动超过 50px
    // 3. X 轴位移大于 Y 轴位移 (确保是横向滑动)
    if (
      touchStartX < 30 &&
      deltaX > 50 &&
      Math.abs(deltaX) > Math.abs(deltaY)
    ) {
      if (router.history.length > 0) {
        // 如果在二级页面，执行 UI 后退
        router.popView();
      }
      // 如果 router.history 为空，已经在顶层，啥也不干 (因为 CSS 已经屏蔽了网页原生后退)
    }
  });

  // 初始化首屏（消息列表）
  import("./views/chatsView.js").then((m) => m.chatsView.refresh());
});
