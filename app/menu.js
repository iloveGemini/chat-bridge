    // ====== 长按菜单系统 ======
    (function () {
      let lpTimer = null;
      let lpTarget = null;
      let ctxOpen = false;

      const chatEl = $('chat');

      function getMsgEl(target) {
        return target.closest('.msg');
      }

      function handlePressStart(e) {
        if (ctxOpen) return;
        if (document.querySelector('.msg-editing')) return;
        const msgEl = getMsgEl(e.target);
        if (!msgEl) return;
        lpTarget = msgEl;
        lpTimer = setTimeout(() => {
          if (e.cancelable) e.preventDefault();
          msgEl.classList.add('msg-shaking');
          setTimeout(() => {
            const idx = parseInt(msgEl.dataset.msgIndex);
            showContextMenu(msgEl, idx, e);
          }, 150);
        }, 650);
      }

      function handlePressEnd(e) {
        if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
      }

      function handlePressMove(e) {
        if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
      }

      chatEl.addEventListener('touchstart', handlePressStart, { passive: false });
      chatEl.addEventListener('touchend', handlePressEnd);
      chatEl.addEventListener('touchmove', handlePressMove);
      chatEl.addEventListener('mousedown', handlePressStart);
      chatEl.addEventListener('mouseup', handlePressEnd);

      function showContextMenu(msgEl, idx, e) {
        ctxOpen = true;
        const isUser = msgEl.classList.contains('msg-user');

        const overlay = document.createElement('div');
        overlay.className = 'ctx-overlay';
        overlay.addEventListener('click', dismissCtx);

        const menu = document.createElement('div');
        menu.className = 'ctx-menu';

        const items = isUser ? [
          { label: '编辑', action: 'edit' },
          { label: '删除', action: 'delete', cls: 'destructive' }
        ] : [
          { label: '重新生成', action: 'reroll' },
          { label: '删除', action: 'delete', cls: 'destructive' }
        ];

        items.forEach(item => {
          const row = document.createElement('div');
          row.className = 'ctx-menu-item' + (item.cls ? ' ' + item.cls : '');
          row.textContent = item.label;
          row.addEventListener('click', () => {
            if (item.action === 'edit') handleEdit(idx);
            else if (item.action === 'delete') handleDelete(idx, msgEl);
            else if (item.action === 'reroll') handleReroll(idx, msgEl);
          });
          menu.appendChild(row);
        });

        document.body.appendChild(overlay);
        document.body.appendChild(menu);

        const bubble = msgEl.querySelector('.msg-bubble');
        const rect = bubble.getBoundingClientRect();
        const menuW = 200;
        let left, top;

        if (isUser) {
          left = rect.right - menuW;
        } else {
          left = rect.left;
        }
        left = Math.max(12, Math.min(left, window.innerWidth - menuW - 12));

        top = rect.top - 10;
        const menuH = items.length * 48 + 8;
        if (top - menuH < 60) {
          top = rect.bottom + 10;
        } else {
          top = top - menuH;
        }
        top = Math.max(60, Math.min(top, window.innerHeight - menuH - 12));

        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
        menu.style.minWidth = menuW + 'px';
      }

      function dismissCtx() {
        document.querySelectorAll('.ctx-overlay, .ctx-menu').forEach(el => el.remove());
        document.querySelectorAll('.msg-shaking').forEach(el => el.classList.remove('msg-shaking'));
        ctxOpen = false;
      }

      function handleEdit(idx) {
        dismissCtx();
        const m = messages[idx];
        if (!m) return;
        const msgEl = chatEl.querySelector('.msg[data-msg-index="' + idx + '"]');
        if (!msgEl) return;

        // 折叠成单个编辑气泡（兼容气泡模式的多气泡布局），保留时间
        const timeHtml = '<div class="msg-time">' + formatTime(m.ts) + '</div>';
        msgEl.classList.add('msg-editing');
        msgEl.classList.remove('msg-multi');
        msgEl.innerHTML = '<div class="msg-bubble"></div>' + timeHtml;
        const bubble = msgEl.querySelector('.msg-bubble');

        const ta = document.createElement('textarea');
        ta.className = 'edit-area';
        ta.value = m.text;

        const actions = document.createElement('div');
        actions.className = 'edit-actions';
        const btnSave = document.createElement('button');
        btnSave.className = 'edit-btn-save';
        btnSave.textContent = '保存';
        const btnCancel = document.createElement('button');
        btnCancel.className = 'edit-btn-cancel';
        btnCancel.textContent = '取消';
        actions.appendChild(btnCancel);
        actions.appendChild(btnSave);

        bubble.innerHTML = '';
        bubble.appendChild(ta);
        bubble.appendChild(actions);

        ta.style.height = 'auto';
        ta.style.height = Math.max(48, ta.scrollHeight) + 'px';
        ta.focus();

        ta.addEventListener('input', function () {
          this.style.height = 'auto';
          this.style.height = Math.max(48, this.scrollHeight) + 'px';
        });

        btnCancel.addEventListener('click', () => {
          msgEl.classList.remove('msg-editing');
          msgEl.innerHTML = paintBubbles(msgEl, m) + timeHtml;
        });

        btnSave.addEventListener('click', async () => {
          const newText = ta.value.trim();
          if (!newText) { showToast('内容不能为空'); return; }
          try {
            const r = await fetch(withSid(API + '/api/edit'), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ index: idx, text: newText })
            });
            const data = await r.json();
            if (data.ok) {
              messages[idx].text = newText;
              m.text = newText;
              msgEl.classList.remove('msg-editing');
              msgEl.innerHTML = paintBubbles(msgEl, m) + timeHtml;
              showToast('已保存');
            } else {
              showToast('保存失败: ' + (data.error || ''));
            }
          } catch (e) { showToast('无法连接'); }
        });
      }

      function handleDelete(idx, msgEl) {
        dismissCtx();
        if (!msgEl) return;
        msgEl.classList.add('msg-deleting');
        setTimeout(async () => {
          try {
            const r = await fetch(withSid(API + '/api/delete'), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ index: idx })
            });
            const data = await r.json();
            if (data.ok) {
              messages.splice(idx, 1);
              lastCount = messages.length;
              prevMsgCount = messages.length;
              msgEl.remove();
              // re-index remaining messages
              chatEl.querySelectorAll('.msg').forEach((el, i) => { el.dataset.msgIndex = i; });
              if (messages.length === 0) {
                $('welcome').style.display = '';
                $('typing').classList.remove('show');
              }
              showToast('已删除');
            } else {
              msgEl.classList.remove('msg-deleting');
              showToast('删除失败');
            }
          } catch (e) {
            msgEl.classList.remove('msg-deleting');
            showToast('无法连接');
          }
        }, 300);
      }

      function handleReroll(idx, msgEl) {
        dismissCtx(); // 关闭长按菜单

        // 1. 判断是否已经在生成中
        if (typeof isGenerating !== 'undefined' && isGenerating) {
          showToast('正在生成中...');
          return;
        }
        if (!msgEl) return;

        // 2. 🚨 锁住 UI，进入等待状态
        if (typeof setGeneratingState === 'function') {
          setGeneratingState(true);
        }

        msgEl.classList.add('msg-deleting');

        setTimeout(async () => {
          try {
            const r = await fetch(withSid(API + '/api/reroll'), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ index: idx })
            });
            const data = await r.json();

            if (data.ok) {
              // 成功删除了旧消息，触发打字机动画
              messages.splice(idx, 1);
              lastCount = messages.length;
              prevMsgCount = messages.length;
              msgEl.remove();
              chatEl.querySelectorAll('.msg').forEach((el, i) => { el.dataset.msgIndex = i; });
              $('typing').classList.add('show');
              // 这里不需要自己解锁，后端的 api/reroll 会触发模型生成
              // 你的 pollTick 轮询会接管后续的拉取，并在生成结束时自动 setGeneratingState(false)
            } else {
              msgEl.classList.remove('msg-deleting');
              showToast('操作失败: ' + (data.error || ''));
              if (typeof setGeneratingState === 'function') setGeneratingState(false); // 🚨 失败时立刻解锁
            }
          } catch (e) {
            msgEl.classList.remove('msg-deleting');
            showToast('无法连接');
            if (typeof setGeneratingState === 'function') setGeneratingState(false); // 🚨 断网时立刻解锁
          }
        }, 300);
      }
    })();

    // ======================================================================
