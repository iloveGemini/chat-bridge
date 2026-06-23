import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, formatTime, ICONS } from '../utils.js';

class ChatHistorySearchView {
  constructor() {
    this.sessionId = null;
    this.messages = [];
    this.currentDate = new Date();
    this.searchKeyword = '';
  }

  async open(sessionId) {
    this.sessionId = sessionId;
    this.searchKeyword = '';
    const container = document.getElementById('chat-history-search-content');
    container.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载剧本记录中...</div>';
    router.pushView('chat-history-search-view');

    try {
      this.messages = await api.fetchMessages(sessionId) || [];
    } catch (e) { this.messages = []; }

    this.render();
  }

  render() {
    const container = document.getElementById('chat-history-search-content');
    if (!container) return;

    const kw = this.searchKeyword.trim().toLowerCase();

    container.innerHTML = `
      <!-- 固定的顶部搜索栏 -->
      <div style="padding:10px 15px; background:var(--surface); border-bottom:0.5px solid var(--border-color); position:sticky; top:0; z-index:10;">
        <div style="display:flex; align-items:center; background:var(--input-bg); border-radius:10px; padding:7px 12px; gap:8px;">
          <span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span>
          <input type="text" id="chs-input" placeholder="搜索剧本台词..." value="${escHtml(this.searchKeyword)}" style="border:none;background:transparent;outline:none;width:100%;color:var(--text);font-size:14px;">
          ${this.searchKeyword ? `<span id="chs-clear" style="cursor:pointer;color:var(--text-secondary);">✕</span>` : ''}
        </div>
      </div>

      <!-- 视图分流：无搜索词显日历，有搜索词显列表 -->
      <div id="chs-body-container"></div>
    `;

    this.renderBody();
    this.bindTopEvents();
  }

  renderBody() {
    const bodyEl = document.getElementById('chs-body-container');
    if (!bodyEl) return;

    const kw = this.searchKeyword.trim().toLowerCase();

    if (!kw) {
      // ======== 模式A：日历寻迹模式 ========
      const year = this.currentDate.getFullYear();
      const month = this.currentDate.getMonth();
      const monthStr = `${year}年 ${month + 1}月`;

      // 提取本房间所有产生过聊天的 YYYY-MM-DD
      const chatDays = new Set();
      this.messages.forEach(m => {
        if (!m.ts) return;
        const dStr = m.ts.substring(0, 10); // 后端标准返回 2026-06-23T...
        chatDays.add(dStr);
      });

      // 计算日历网格
      const firstDayIndex = new Date(year, month, 1).getDay(); // 0=周日
      const totalDays = new Date(year, month + 1, 0).getDate();

      let gridHtml = '';
      const dayNames = ['日', '一', '二', '三', '四', '五', '六'];
      dayNames.forEach(d => gridHtml += `<div class="calendar-th">${d}</div>`);

      // 填充月初空白
      for (let i = 0; i < firstDayIndex; i++) gridHtml += `<div class="calendar-cell empty"></div>`;

      // 填充日期
      for (let d = 1; d <= totalDays; d++) {
        const mPad = String(month + 1).padStart(2, '0');
        const dPad = String(d).padStart(2, '0');
        const fullDateStr = `${year}-${mPad}-${dPad}`;
        const hasChat = chatDays.has(fullDateStr);

        gridHtml += `
          <div class="calendar-cell ${hasChat ? 'has-chat' : ''}" data-date="${fullDateStr}">
            ${d}
          </div>
        `;
      }

      bodyEl.innerHTML = `
        <div class="calendar-view-box">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:0 8px;">
            <div style="font-size:16px;font-weight:bold;">${monthStr}</div>
            <div style="display:flex;gap:15px;font-size:14px;color:var(--active-color);cursor:pointer;user-select:none;">
              <span id="cal-prev">〈 上月</span>
              <span id="cal-next">下月 〉</span>
            </div>
          </div>
          <div class="calendar-grid">${gridHtml}</div>
          <div style="text-align:center;font-size:12px;color:var(--text-secondary);margin-top:20px;">
            注：带紫点的日期表示当天存有对话记录，点击可直达当天初始。
          </div>
        </div>
      `;

      this.bindCalendarEvents();

    } else {
      // ======== 模式B：台词搜索列表模式 ========
      const matched = [];
      this.messages.forEach((m, idx) => {
        if ((m.text || '').toLowerCase().includes(kw)) matched.push({ ...m, index: idx });
      });

      let listHtml = '<div style="padding:15px;">';
      matched.reverse().forEach(m => {
        const isUser = m.role === 'user';
        const speaker = isUser ? '我' : 'AI';
        const snippet = m.text;
        listHtml += `
          <div class="history-snippet-card" data-msg-idx="${m.index}">
            <div style="display:flex;justify-content:space-between;color:var(--text-secondary);font-size:12px;margin-bottom:6px;">
              <span style="color:${isUser ? 'var(--user-bubble)' : 'var(--active-color)'};font-weight:bold;">${speaker}</span>
              <span>${m.ts ? m.ts.replace('T', ' ') : ''}</span>
            </div>
            <div style="color:var(--text);line-height:1.4;">${escHtml(snippet)}</div>
          </div>
        `;
      });
      listHtml += matched.length === 0 ? `<div style="text-align:center;padding:40px;color:var(--text-secondary);">未检索到相关台词</div>` : '';
      listHtml += '</div>';

      bodyEl.innerHTML = listHtml;
      this.bindListEvents();
    }
  }

  bindTopEvents() {
    const inp = document.getElementById('chs-input');
    const clr = document.getElementById('chs-clear');
    if (inp) inp.oninput = (e) => { this.searchKeyword = e.target.value; this.renderBody(); };
    if (clr) clr.onclick = () => { this.searchKeyword = ''; inp.value = ''; this.renderBody(); };
  }

  bindCalendarEvents() {
    document.getElementById('cal-prev').onclick = () => { this.currentDate.setMonth(this.currentDate.getMonth() - 1); this.renderBody(); };
    document.getElementById('cal-next').onclick = () => { this.currentDate.setMonth(this.currentDate.getMonth() + 1); this.renderBody(); };

    // 点击有记录的日期 -> Pop 回聊天室并瞬间精准滚动！
    document.getElementById('chs-body-container').querySelectorAll('.calendar-cell.has-chat').forEach(cell => {
      cell.onclick = () => {
        const targetDate = cell.dataset.date;
        // 寻找该日期当天的第一条消息索引
        const targetIdx = this.messages.findIndex(m => m.ts && m.ts.startsWith(targetDate));
        if (targetIdx !== -1) this.jumpToRoomMessage(targetIdx);
      };
    });
  }

  bindListEvents() {
    document.getElementById('chs-body-container').querySelectorAll('.history-snippet-card').forEach(card => {
      card.onclick = () => this.jumpToRoomMessage(parseInt(card.dataset.msgIdx));
    });
  }

  jumpToRoomMessage(msgIndex) {
    // 连续 Pop 两次：退回设置页 -> 再退回房间！
    router.popView();
    router.popView();
    import('./chatView.js').then(c => c.chatView.scrollToMessageIndex(msgIndex));
  }
}

export const chatHistorySearchView = new ChatHistorySearchView();