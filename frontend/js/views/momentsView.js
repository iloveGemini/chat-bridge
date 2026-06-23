import { store } from '../store.js';

export class MomentsView {
  constructor() {
    this.container = document.getElementById('moments-list');
  }

  refresh() {
    this.container = document.getElementById('moments-list');
    this.init();
  }

  init() {
    // 假想的朋友圈数据，未来可以通过服务端长时记忆提取重大事件
    const moments = [
      {
        id: 1,
        author: '迟宴舟',
        avatarBg: 'hsl(200, 45%, 55%)',
        content: '今天看到了很美的夕阳，不知道你那边天气如何？这里的时间流逝得很特别。',
        time: '10分钟前'
      },
      {
        id: 2,
        author: '系统助手',
        avatarBg: '#a78bfa',
        content: '🌟 里程碑达成：你们已经完成了第一次深度的世界观探讨。羁绊值提升！',
        time: '昨天'
      }
    ];
    this.render(moments);
  }

  render(moments) {
    if (!this.container) return;
    
    let html = '';
    moments.forEach(m => {
      html += `
        <div class="moment-card">
          <div class="moment-header">
            <div class="avatar" style="background: ${m.avatarBg};">${m.author.charAt(0)}</div>
            <div class="info">
              <div class="name">${m.author}</div>
            </div>
          </div>
          <div class="moment-content">${m.content}</div>
          <div class="moment-time">${m.time}</div>
        </div>
      `;
    });
    
    this.container.innerHTML = html;
  }
}

export const momentsView = new MomentsView();
