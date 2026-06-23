export class VConsole {
  constructor() {
    this.fab = document.getElementById('vconsole-fab');
    this.panel = document.getElementById('vconsole-panel');
    this.body = document.getElementById('vc-body');
    this.btnClose = document.getElementById('vc-close');
    this.btnClear = document.getElementById('vc-clear');
    
    this.mockTimer = null;

    if (this.fab) {
      this.initDraggable();
      this.syncVisibility();
    }
    if (this.btnClose) this.btnClose.onclick = () => this.hide();
    if (this.btnClear) this.btnClear.onclick = () => this.clear();
  }

  // 根据 UI 设置页的开关控制显示/隐藏
  syncVisibility() {
    const enabled = localStorage.getItem('vconsole_en') === '1';
    if (this.fab) this.fab.style.display = enabled ? 'flex' : 'none';
    if (!enabled && this.panel.classList.contains('show')) this.hide();
  }

  initDraggable() {
    let isDragging = false;
    let startY = 0;

    const handleStart = (y) => {
      startY = y;
      isDragging = false;
    };

    const handleMove = (y) => {
      // 滑动超过 10 像素才判定为拖拽，过滤掉手指轻触的微小颤抖
      if (Math.abs(y - startY) > 10) {
        isDragging = true;
        let newY = Math.max(20, Math.min(window.innerHeight - 60, y - 22));
        this.fab.style.top = newY + 'px';
        this.fab.style.bottom = 'auto';
      }
    };

    // 手机端触摸
    this.fab.addEventListener('touchstart', e => handleStart(e.touches[0].clientY), { passive: true });
    this.fab.addEventListener('touchmove', e => handleMove(e.touches[0].clientY), { passive: true });
    
    // PC端鼠标
    this.fab.addEventListener('mousedown', e => handleStart(e.clientY));
    window.addEventListener('mousemove', e => { if (e.buttons === 1 && startY) handleMove(e.clientY); });
    window.addEventListener('mouseup', () => { startY = 0; });

    // 原生点击事件：如果是拖拽结尾，就拦截掉；否则清脆弹起！
    this.fab.onclick = (e) => {
      if (isDragging) {
        e.preventDefault();
        return;
      }
      if (this.panel.classList.contains('show')) this.hide();
      else this.show();
    };
  }

  show() {
    this.panel.classList.add('show');
    this.log('[System] vConsole 控制台已挂载...');
    if (!this.mockTimer) {
      this.mockTimer = setInterval(() => {
        this.log(`[Mock] 等待后端 /api/logs 接口接入... ${Math.floor(Math.random()*100)}ms`);
      }, 3000);
    }
  }

  hide() {
    this.panel.classList.remove('show');
    if (this.mockTimer) { clearInterval(this.mockTimer); this.mockTimer = null; }
  }

  clear() {
    if (this.body) this.body.innerHTML = '<div style="color:#888;">[System] 屏幕已清空</div>';
  }

  log(msg, color = '#0f0') {
    if (!this.body) return;
    const line = document.createElement('div');
    line.style.color = color;
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    this.body.appendChild(line);
    this.body.scrollTop = this.body.scrollHeight;
  }
}