export class Router {
  constructor() {
    this.history = []; // 二级页栈
  }

  switchTab(targetId) {
    document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    const targetView = document.getElementById(targetId);
    if (targetView) targetView.classList.add('active');
    const targetBtn = document.querySelector(`.tab-btn[data-target="${targetId}"]`);
    if (targetBtn) targetBtn.classList.add('active');

    if (targetId === 'tab-chats') import('./views/chatsView.js').then(m => m.chatsView.refresh());
    if (targetId === 'tab-contacts') import('./views/contactsView.js').then(m => m.contactsView.refresh());
    if (targetId === 'tab-moments') import('./views/momentsView.js').then(m => m.momentsView.refresh());
    if (targetId === 'tab-me') import('./views/meView.js').then(m => m.meView.refresh());
  }

  // 二级页（HTML 中 id 即视图 id，无前缀）
  pushView(viewId, params = {}) {
    const view = document.getElementById(viewId);
    if (!view) return;
    view.classList.add('show');
    this.history.push(viewId);

    if (viewId === 'chat-room') {
      import('./views/chatView.js').then(module => {
        module.chatView.initRoom(params.id || 'default', params.name || '聊天');
      });
    }
    if (viewId === 'settings-view') {
      import('./views/settingsFormView.js').then(module => {
        module.settingsFormView.init(params.type, params.title);
      });
    }
  }

  popView() {
    if (this.history.length === 0) return;
    const viewId = this.history.pop();
    const view = document.getElementById(viewId);
    if (view) view.classList.remove('show');
    if (viewId === 'chat-room') {
      import('./views/chatView.js').then(m => m.chatView.onLeave());
    }
    if (this.history.length === 0) {
      import('./views/chatsView.js').then(m => m.chatsView.refresh());
    }
  }
}

export const router = new Router();
