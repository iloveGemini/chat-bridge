import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, getFallbackAvatar, actionSheet, showToast, ICONS } from '../utils.js';
import { chatsView } from './chatsView.js';

class ContactProfileView {
  constructor() {
    this.currentKey = null;
    this.characterData = null;
  }

  async open(key) {
    this.currentKey = key;
    const contentEl = document.getElementById('contact-profile-content');
    contentEl.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载角色档案中...</div>';
    router.pushView('contact-profile-view');

    try {
      const res = await api.getPrompt('character', key);
      this.characterData = res.ok ? res.data : { name: key, content: '暂无简介' };
    } catch (e) {
      this.characterData = { name: key, content: '加载设定失败' };
    }
    this.render();
  }

  render() {
    const contentEl = document.getElementById('contact-profile-content');
    if (!contentEl) return;

    const c = this.characterData;
    const name = c.name || this.currentKey;
    const avatar = c.avatar || getFallbackAvatar(name);
    // 截取前 30 个字作为“心情/签名”
    const mood = (c.content || '未填写简介').slice(0, 30) + ((c.content || '').length > 30 ? '...' : '');
    const voiceObj = c.voice || {};
    const voiceName = voiceObj.voice_id ? voiceObj.voice_id.split('_').pop() : '未配置';

    contentEl.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; background:var(--surface); padding:16px 20px; border-bottom:0.5px solid var(--border-color);">
        <div style="display:flex; align-items:center; flex:1; overflow:hidden;">
          <div class="profile-avatar-box" id="profile-avatar-btn" style="margin:0; width:56px; height:56px; flex-shrink:0;">
            <img id="profile-avatar-img" src="${avatar}" style="border-radius:12px; border:none; box-shadow:none;">
            <div class="camera-badge" style="width:20px;height:20px;font-size:10px;right:-2px;bottom:-2px;">${ICONS.camera}</div>
            <input type="file" id="profile-avatar-upload" accept="image/*" style="display:none;">
          </div>
          <div style="margin-left:15px; flex:1; min-width:0;">
            <div style="font-size:18px; font-weight:bold; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(name)}</div>
            <div style="font-size:12px; color:var(--text-secondary); margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">✍️ ${escHtml(mood)}</div>
          </div>
        </div>
      </div>

      <div class="ios-group" style="margin-top:20px;">
        <div class="ios-item" id="cp-edit">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.userPlus}</span> 编辑角色设定</span>
          <span class="val"></span>
        </div>
        <div class="ios-item" id="cp-voice">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.voice}</span> 语音音色</span>
          <span class="val">${escHtml(voiceName)}</span>
        </div>
        <div class="ios-item" id="cp-lore">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.book}</span> 世界书 (设定集)</span>
          <span class="val">专属绑定</span>
        </div>
        <div class="ios-item" id="cp-moments">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.photo}</span> 朋友圈相册</span>
          <span class="val">进入</span>
        </div>
      </div>

      <div style="padding: 0 15px; margin-top: 35px;">
        <button class="btn-primary" id="cp-send-btn" style="height:46px; font-size:16px;">发消息</button>
      </div>
      
      ${this.currentKey !== 'default' ? `
      <div class="ios-group" style="margin-top:15px;">
        <div class="ios-item" id="cp-del-btn" style="justify-content:center; color:#ff3b30; font-weight:500;">删除该角色</div>
      </div>
      ` : ''}
    `;

    this.bindEvents();
  }

  bindEvents() {
    const avatarBtn = document.getElementById('profile-avatar-btn');
    const fileInput = document.getElementById('profile-avatar-upload');

    avatarBtn.onclick = () => {
      actionSheet([
        { label: '查看大头像', action: 'view' },
        { label: '从相册选择新头像', action: 'upload' }
      ], (act) => {
        if (act === 'view') this.showBigAvatar();
        else if (act === 'upload') fileInput.click();
      });
    };

    fileInput.onchange = (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const r = new FileReader();
      r.onload = async (ev) => {
        const b64 = ev.target.result;
        document.getElementById('profile-avatar-img').src = b64;
        this.characterData.avatar = b64;
        await api.savePrompt({
          category: 'character', name: this.currentKey, content: this.characterData.content,
          display_name: this.characterData.name, avatar: b64, voice: this.characterData.voice
        });
        showToast('头像更换成功');
      };
      r.readAsDataURL(f);
    };

    document.getElementById('cp-send-btn').onclick = () => chatsView.createAndOpen(this.currentKey);

    const delBtn = document.getElementById('cp-del-btn');
    if (delBtn) {
      delBtn.onclick = async () => {
        if (!confirm(`确定彻底删除「${this.characterData.name}」吗？`)) return;
        const r = await api.deletePrompt('character', this.currentKey);
        if (r.ok) {
          showToast('已删除联系人'); router.popView();
          import('./contactsView.js').then(m => m.contactsView.refresh());
        } else showToast(r.error || '删除失败');
      };
    }

    document.getElementById('cp-edit').onclick = () => {
      import('../modals.js').then(m => m.openPromptEditor('character', this.currentKey, () => this.open(this.currentKey)));
    };
    document.getElementById('cp-voice').onclick = () => {
      import('../modals.js').then(m => m.openPromptEditor('character', this.currentKey, () => this.open(this.currentKey)));
    };
    document.getElementById('cp-lore').onclick = () => {
      import('./worldbooksView.js').then(m => m.worldbooksView.open());
    };
    document.getElementById('cp-moments').onclick = () => { router.switchTab('tab-moments'); router.popView(); };
  }

  showBigAvatar() {
    const mask = document.createElement('div');
    mask.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.9);z-index:9999;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity 0.2s ease;cursor:pointer;';
    mask.innerHTML = `<img src="${this.characterData.avatar || getFallbackAvatar(this.characterData.name)}" style="max-width:90%;max-height:90%;object-fit:contain;border-radius:16px;">`;
    mask.onclick = () => { mask.style.opacity = '0'; setTimeout(() => mask.remove(), 200); };
    document.body.appendChild(mask);
    requestAnimationFrame(() => mask.style.opacity = '1');
  }
}

export const contactProfileView = new ContactProfileView();