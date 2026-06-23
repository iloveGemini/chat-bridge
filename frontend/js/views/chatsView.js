import { store } from '../store.js';
import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, actionSheet, selectSheet, showToast, formatTime, ICONS } from '../utils.js';
import { chatMultiSelectView } from './chatMultiSelectView.js';

class ChatsView {
  constructor() {
    this.container = document.getElementById('chats-list');
    this.sessions = [];
  }

  async refresh() {
    this.container = document.getElementById('chats-list');
    try {
      const data = await api.fetchSessions();
      this.sessions = data.sessions || [];
      store.setState({ sessions: this.sessions });
      this.render();
    } catch (e) {
      console.error('加载会话失败', e);
      if (this.container) this.container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">无法连接服务器</div>';
    }
  }

render() {
    if (!this.container) return;
    const list = this.sessions.slice().sort(
      (a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) || (b.updated_at || 0) - (a.updated_at || 0)
    );
    if (list.length === 0) {
      this.container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary);">暂无对话，点击右上角发起</div>';
      return;
    }

    this.container.innerHTML = list.map(s => {
      const name = s.character_name || s.character || s.id;
      const avatar = s.avatar || getFallbackAvatar(name);
      const time = s.updated_at ? formatTime(s.updated_at * 1000) : '';
      // 完美仿微信：置顶项加上 pinned class 改变底色，没有任何Emoji
      const pinnedCls = s.pinned ? ' pinned' : '';
      
      return `
        <div class="list-item${pinnedCls}" data-id="${escHtml(s.id)}" data-name="${escHtml(name)}">
          <img class="avatar" style="object-fit:cover;" src="${avatar}">
          <div class="info">
            <div class="name">${escHtml(name)}
              <span style="float:right;font-size:12px;color:var(--text-faint);font-weight:normal;">${time}</span>
            </div>
            <div class="msg">${escHtml(s.preview || '...')}</div>
          </div>
        </div>`;
    }).join('');

    // 极其纯粹：点击列表项，唯一的宿命就是进入房间！
    this.container.querySelectorAll('.list-item').forEach(el => {
      el.onclick = () => router.pushView('chat-room', { id: el.dataset.id, name: el.dataset.name });
    });
  }

  showActions(s) {
    actionSheet([
      { label: s.pinned ? '取消置顶' : '置顶', action: 'pin' },
      { label: '改名', action: 'rename' },
      { label: '开始新聊天（同角色）', action: 'new' },
      { label: '克隆聊天', action: 'clone' },
      { label: '清空消息', action: 'clear' },
      { label: '删除', action: 'delete', destructive: true },
    ], (act) => this.handleAction(act, s));
  }

  async handleAction(act, s) {
    try {
      if (act === 'pin') {
        const r = await api.pinSession(s.id);
        if (r.ok) { showToast(r.pinned ? '已置顶' : '已取消置顶'); this.refresh(); }
      } else if (act === 'rename') {
        const name = prompt('输入新名称：', s.character_name || s.id);
        if (!name || !name.trim()) return;
        const r = await api.renameSession(s.id, name.trim());
        if (r.ok) { showToast('已改名'); this.refresh(); } else showToast(r.error || '改名失败');
      } else if (act === 'new') {
        this.createAndOpen(s.character || 'default');
      } else if (act === 'clone') {
        const r = await api.cloneSession(s.id);
        if (r.ok) { showToast('已克隆'); this.refresh(); } else showToast(r.error || '克隆失败');
      } else if (act === 'clear') {
        if (!confirm('清空该会话所有消息？')) return;
        const r = await api.clear(s.id);
        if (r.ok) { showToast('已清空'); this.refresh(); }
      } else if (act === 'delete') {
        if (this.sessions.length <= 1) { showToast('至少保留一个会话'); return; }
        if (!confirm('删除该会话？不可恢复。')) return;
        const r = await api.deleteSession(s.id);
        if (r.ok) { showToast('已删除'); this.refresh(); } else showToast(r.error || '删除失败');
      }
    } catch (e) { showToast('操作失败'); }
  }

  // 新建会话：先选角色
  async startNewChatFlow() {
    const btn = document.getElementById('chats-new-btn');
    const rect = btn ? btn.getBoundingClientRect() : { right: window.innerWidth - 15, bottom: 50 };

    // 1. 弹出右上角 Popover 气泡菜单
    const mask = document.createElement('div');
    mask.className = 'popover-mask';

    const popover = document.createElement('div');
    popover.className = 'popover-box';
    popover.style.top = (rect.bottom + 8) + 'px';
    popover.style.right = (window.innerWidth - rect.right) + 'px';

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

    const dismiss = () => { popover.remove(); mask.remove(); };
    mask.onclick = dismiss;

    popover.querySelector('#pop-chat').onclick = () => { dismiss(); chatMultiSelectView.open(); };
    popover.querySelector('#pop-char').onclick = () => {
      dismiss();
      import('../modals.js').then(m => m.openPromptEditor('character', null, () => {
        import('./contactsView.js').then(cv => cv.contactsView.refresh());
      }));
    };

    document.body.appendChild(mask);
    document.body.appendChild(popover);
  }

  async createAndOpen(charKey) {
    try {
      const data = await api.createSession(charKey);
      if (data.ok) {
        await this.refresh();
        const s = this.sessions.find(x => x.id === data.session_id);
        router.pushView('chat-room', { id: data.session_id, name: (s && s.character_name) || charKey });
      } else showToast('无法发起对话');
    } catch (e) { showToast('发起对话失败'); }
  }
}

export const chatsView = new ChatsView();
