import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, showToast } from '../utils.js';
import { chatsView } from './chatsView.js';

class ChatMultiSelectView {
  constructor() {
    this.characters = [];
    this.selectedKeys = new Set();
  }

  async open() {
    this.selectedKeys.clear();
    const container = document.getElementById('chat-multiselect-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载通讯录中...</div>';
    this.updateConfirmBtn();
    router.pushView('chat-multiselect-view');

    try {
      const data = await api.fetchPrompts();
      this.characters = data.characters || [];
    } catch (e) {
      this.characters = [];
    }
    this.render();
  }

  render() {
    const container = document.getElementById('chat-multiselect-content');
    if (!container) return;

    if (this.characters.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">通讯录空空如也，先去新建一个角色吧</div>';
      return;
    }

    // 采用沉浸式的无边框列表风格
    let listHtml = '<div style="background:var(--surface); border-top:0.5px solid var(--border-color); border-bottom:0.5px solid var(--border-color); margin-top:15px;">';
    this.characters.forEach(c => {
      const name = c.name || c.key;
      const avatar = c.avatar || getFallbackAvatar(name);
      const isChecked = this.selectedKeys.has(c.key);
      
      listHtml += `
        <div class="multi-check-item ${isChecked ? 'checked' : ''}" data-key="${escHtml(c.key)}" style="padding: 14px 16px;">
          <div style="display:flex; align-items:center; gap:14px;">
            <img src="${avatar}" style="width:42px;height:42px;border-radius:10px;object-fit:cover;">
            <span style="font-size:16px;color:var(--text);font-weight:500;">${escHtml(name)}</span>
          </div>
          <div class="custom-checkbox"></div>
        </div>
      `;
    });
    listHtml += '</div>';

    container.innerHTML = listHtml;
    this.bindEvents();
  }

  bindEvents() {
    const container = document.getElementById('chat-multiselect-content');
    const confirmBtn = document.getElementById('cms-confirm-btn');

    container.querySelectorAll('.multi-check-item').forEach(item => {
      item.onclick = () => {
        const k = item.dataset.key;
        if (this.selectedKeys.has(k)) {
          this.selectedKeys.delete(k);
          item.classList.remove('checked');
        } else {
          this.selectedKeys.add(k);
          item.classList.add('checked');
        }
        this.updateConfirmBtn();
      };
    });

    // 重新绑定右上角的确定按钮
    confirmBtn.onclick = null;
    confirmBtn.onclick = async () => {
      if (this.selectedKeys.size === 0) return;
      const keys = Array.from(this.selectedKeys);
      
      router.popView(); // 退回上一页

      if (keys.length === 1) {
        // 单选：直接调用 chatsView 发起单聊
        chatsView.createAndOpen(keys[0]);
      } else {
        // 多选：自动建群
        const names = keys.map(k => { const tgt = this.characters.find(x => x.key === k); return tgt ? (tgt.name || tgt.key) : k; });
        const groupTitle = names.slice(0, 3).join('、') + (names.length > 3 ? '等群聊' : '');
        const compositeKey = 'group:' + keys.join(',');

        try {
          const res = await api.post('/api/sessions/create', { character: compositeKey, name: groupTitle });
          if (res.ok) {
            await chatsView.refresh();
            router.pushView('chat-room', { id: res.session_id, name: groupTitle });
          } else {
            showToast('群聊创建失败');
          }
        } catch (e) { showToast('创建群聊出错'); }
      }
    };
  }

  updateConfirmBtn() {
    const confirmBtn = document.getElementById('cms-confirm-btn');
    if (!confirmBtn) return;
    const cnt = this.selectedKeys.size;
    if (cnt === 0) {
      confirmBtn.textContent = '确定';
      confirmBtn.style.opacity = '0.4';
      confirmBtn.style.pointerEvents = 'none';
    } else {
      confirmBtn.textContent = `确定 (${cnt})`;
      confirmBtn.style.opacity = '1';
      confirmBtn.style.pointerEvents = 'auto';
    }
  }
}

export const chatMultiSelectView = new ChatMultiSelectView();