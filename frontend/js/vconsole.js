import { api } from "./api.js";

export class VConsole {
  constructor() {
    this.fab = document.getElementById('vconsole-fab');
    this.panel = document.getElementById('vconsole-panel');
    this.body = document.getElementById('vc-body');
    this.btnClose = document.getElementById('vc-close');
    this.btnClear = document.getElementById('vc-clear');

    this.feedTimer = null;
    this._logSeq = 0; // 已拉取到的后端日志游标

    if (this.fab) {
      this.initDraggable();
      this.syncVisibility();
    }
    if (this.btnClose) this.btnClose.onclick = () => this.hide();
    if (this.btnClear) this.btnClear.onclick = () => this.clear();
  }

  syncVisibility() {
    const enabled = localStorage.getItem('vconsole_en') === '1';
    if (this.fab) this.fab.style.display = enabled ? 'flex' : 'none';
    if (!enabled && this.panel.classList.contains('show')) this.hide();
  }

  initDraggable() {
    let isDragging = false;
    let startY = 0;
    const handleStart = (y) => { startY = y; isDragging = false; };
    const handleMove = (y) => {
      if (Math.abs(y - startY) > 10) {
        isDragging = true;
        let newY = Math.max(20, Math.min(window.innerHeight - 60, y - 22));
        this.fab.style.top = newY + 'px';
        this.fab.style.bottom = 'auto';
      }
    };
    this.fab.addEventListener('touchstart', e => handleStart(e.touches[0].clientY), { passive: true });
    this.fab.addEventListener('touchmove', e => handleMove(e.touches[0].clientY), { passive: true });
    this.fab.addEventListener('mousedown', e => handleStart(e.clientY));
    window.addEventListener('mousemove', e => { if (e.buttons === 1 && startY) handleMove(e.clientY); });
    window.addEventListener('mouseup', () => { startY = 0; });
    this.fab.onclick = (e) => {
      if (isDragging) { e.preventDefault(); return; }
      if (this.panel.classList.contains('show')) this.hide();
      else this.show();
    };
  }

  show() {
    this.panel.classList.add('show');
    this.log('[System] vConsole 已挂载，开始接收后端日志…');
    this.startFeed();
  }

  hide() {
    this.panel.classList.remove('show');
    this.stopFeed();
  }

  clear() {
    if (this.body) this.body.innerHTML = '<div style="color:#888;">[System] 屏幕已清空</div>';
  }

  // 轮询后端 /api/logs，把 agent 的调试日志推进控制台
  startFeed() {
    if (this.feedTimer) return;
    const tick = async () => {
      try {
        const res = await api.fetchLogs(this._logSeq);
        if (res && res.ok && res.logs && res.logs.length) {
          res.logs.forEach((e) => {
            this._logSeq = Math.max(this._logSeq, e.id);
            this.log(e.line, this._colorFor(e.line), e.ts);
          });
        }
      } catch (err) {
        /* 静默：拉日志失败不打扰 */
      }
    };
    tick();
    this.feedTimer = setInterval(tick, 1000);
  }

  stopFeed() {
    if (this.feedTimer) { clearInterval(this.feedTimer); this.feedTimer = null; }
  }

  _colorFor(line) {
    if (/⚠|错误|error|失败|exit [1-9]/i.test(line)) return '#ff5f56';
    if (/↗ LLM|限流/.test(line)) return '#56b6ff';
    if (/⚙ tool|↩/.test(line)) return '#dcdcaa';
    if (/🚀 fanout|worker/.test(line)) return '#4ec9b0';
    if (/📌 进度卡/.test(line)) return '#c586c0';
    if (/📥/.test(line)) return '#ffae57';
    if (/⏹/.test(line)) return '#ff8787';
    return '#9cdc8a';
  }

  log(msg, color = '#0f0', ts) {
    if (!this.body) return;
    const line = document.createElement('div');
    line.style.color = color;
    line.textContent = `[${ts || new Date().toLocaleTimeString()}] ${msg}`;
    this.body.appendChild(line);
    // 限制 DOM 行数，避免长跑卡顿
    while (this.body.childNodes.length > 600) this.body.removeChild(this.body.firstChild);
    this.body.scrollTop = this.body.scrollHeight;
  }
}
