import { router } from './router.js';
import { store } from './store.js';
import { applyTheme } from './utils.js';
import { VConsole } from './vconsole.js';

window.vConsole = new VConsole(); // 全局挂载，以后其他模块也能 window.vConsole.log()

// 暴露给 HTML 内联 onclick
window.router = router;

// 应用初始主题
applyTheme(store.getState().config.theme || 'dark');

document.addEventListener('DOMContentLoaded', () => {
  // 标签切换
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => router.switchTab(btn.getAttribute('data-target')));
  });

  // 新建会话按钮（消息页右上角）
  const newBtn = document.getElementById('chats-new-btn');
  if (newBtn) newBtn.addEventListener('click', () => {
    import('./views/chatsView.js').then(m => m.chatsView.startNewChatFlow());
  });

  // 聊天室设置按钮
  const settBtn = document.getElementById('chat-room-settings');
  if (settBtn) settBtn.addEventListener('click', () => {
    import('./views/chatView.js').then(m => m.chatView.openRoomSettings());
  });

  // Android/浏览器返回键：优先弹出二级页
  window.addEventListener('popstate', () => {
    if (router.history.length > 0) {
      router.popView();
      history.pushState(null, null, location.pathname);
    }
  });
  history.pushState(null, null, location.pathname);

  // 初始化首屏（消息列表）
  import('./views/chatsView.js').then(m => m.chatsView.refresh());
});
