    // ====== 多会话 / 角色 / 提示词模块 抽屉与弹层 ======
    // ======================================================================
    (function () {
      const CAT_LABEL = { world: '世界设定', user: '用户设定', character: '角色' };
      let allSessions = [];
      let promptTree = { main: [], world: [], character: [], user: [], style: [], post: [] };
      let promptActive = {};
      let characterMeta = []; // [{key, name, avatar}]
      let presetList = [];    // 缓存的预设名字列表
      let pickCallback = null;

      // 当前会话的绑定：只存 4 个名字。选择只改它（纯本地），发消息时随 config 一起送服务器。
      let sessionBinding = { preset: 'default', world: 'default', user: 'default', character: 'default' };
      function seedBindingFromServer() {
        sessionBinding = {
          preset: promptActive.preset || 'default',
          world: promptActive.world || 'default',
          user: promptActive.user || 'default',
          character: promptActive.character || (allSessions.find(s => s.id === currentSessionId) || {}).character || 'default',
        };
      }
      // 暴露给外层作用域的 doSubmit：发消息时把当前绑定随 config 一起送出
      window.getSessionBinding = () => sessionBinding;

      async function api(path, opts) {
        try {
          const r = await fetch(API + path, opts);
          return await r.json();
        } catch (e) { showToast('网络错误'); return { ok: false }; }
      }

      // ---------- 抽屉开关 ----------
      function openLeft() { $('drawer-left').classList.add('show'); $('mask-left').classList.add('show'); loadSessions(); loadPromptTree(); }
      function closeLeft() { $('drawer-left').classList.remove('show'); $('mask-left').classList.remove('show'); }
      function openRight() { $('drawer-right').classList.add('show'); $('mask-right').classList.add('show'); refreshRightPanel(); }
      function closeRight() { $('drawer-right').classList.remove('show'); $('mask-right').classList.remove('show'); }
      $('btn-drawer-left').addEventListener('click', openLeft);
      $('mask-left').addEventListener('click', closeLeft);
      $('btn-drawer-right').addEventListener('click', openRight);
      $('mask-right').addEventListener('click', closeRight);

      function closeSheet(sheetId, maskId) {
        $(sheetId).classList.remove('show');
        $(maskId).classList.remove('show');
      }
      function openSheet(sheetId, maskId) {
        $(sheetId).classList.add('show');
        $(maskId).classList.add('show');
      }
      $('mask-sheet').addEventListener('click', () => closeSheet('sheet-select', 'mask-sheet'));
      $('mask-edit').addEventListener('click', () => closeSheet('sheet-edit', 'mask-edit'));
      $('mask-preset-edit').addEventListener('click', () => closeSheet('sheet-preset-edit', 'mask-preset-edit'));

      // ---------- 会话列表 ----------
      async function loadSessions() {
        const data = await api('/api/sessions/list');
        allSessions = data.sessions || [];
        renderSessionFilter();
        renderSessionList();
      }

      function renderSessionFilter() {
        const sel = $('session-filter');
        const prev = sel.value;
        const seen = new Map();
        allSessions.forEach(s => seen.set(s.character, s.character_name));
        sel.innerHTML = '<option value="__all__">全部聊天</option>' +
          Array.from(seen.entries()).map(([key, name]) => '<option value="' + escHtml(key) + '">' + escHtml(name) + '</option>').join('');
        if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
      }

      function renderSessionList() {
        const filter = $('session-filter').value || '__all__';
        const list = (filter === '__all__' ? allSessions : allSessions.filter(s => s.character === filter))
          .slice().sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) || b.updated_at - a.updated_at);
        const box = $('session-list');
        box.innerHTML = '';
        list.forEach(s => {
          const row = document.createElement('div');
          row.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
          const pinIcon = s.pinned ? '<span style="font-size:11px;margin-right:2px;">📌</span>' : '';
          row.innerHTML =
            '<img src="' + (s.avatar || '/logo.png') + '">' +
            '<div class="session-item-text">' +
            '<div class="session-item-name">' + pinIcon + escHtml(s.character_name) + '</div>' +
            '<div class="session-item-preview">' + escHtml(s.preview || '暂无消息') + '</div>' +
            '</div>' +
            '<button class="session-kebab" title="更多">⋮</button>';
          row.querySelector('.session-item-text').addEventListener('click', () => switchSession(s.id));
          row.querySelector('img').addEventListener('click', () => switchSession(s.id));
          row.querySelector('.session-kebab').addEventListener('click', (e) => {
            e.stopPropagation();
            showSessionActions(s);
          });
          box.appendChild(row);
        });
        if (list.length === 0) box.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:13px;">暂无会话</div>';
      }

      function showSessionActions(s) {
        const mask = document.createElement('div');
        mask.className = 'action-sheet-mask';
        const sheet = document.createElement('div');
        sheet.className = 'action-sheet';

        const mainGroup = document.createElement('div');
        mainGroup.className = 'action-sheet-group';
        const items = [
          { label: s.pinned ? '取消置顶' : '置顶', action: 'pin' },
          { label: '改名', action: 'rename' },
          { label: '开始新聊天', action: 'new' },
          { label: '克隆聊天', action: 'clone' },
          { label: '清空消息', action: 'clear' },
        ];
        items.forEach(it => {
          const row = document.createElement('div');
          row.className = 'action-sheet-item';
          row.textContent = it.label;
          row.addEventListener('click', () => { dismiss(); handleSessionAction(it.action, s); });
          mainGroup.appendChild(row);
        });
        sheet.appendChild(mainGroup);

        const delGroup = document.createElement('div');
        delGroup.className = 'action-sheet-group';
        const delRow = document.createElement('div');
        delRow.className = 'action-sheet-item destructive';
        delRow.textContent = '删除';
        delRow.addEventListener('click', () => { dismiss(); handleSessionAction('delete', s); });
        delGroup.appendChild(delRow);
        sheet.appendChild(delGroup);

        const cancel = document.createElement('div');
        cancel.className = 'action-sheet-cancel';
        cancel.textContent = '取消';
        cancel.addEventListener('click', dismiss);
        sheet.appendChild(cancel);
        mask.addEventListener('click', dismiss);
        document.body.appendChild(mask);
        document.body.appendChild(sheet);
        requestAnimationFrame(() => { mask.classList.add('show'); sheet.classList.add('show'); });
        function dismiss() { sheet.classList.remove('show'); mask.classList.remove('show'); setTimeout(() => { sheet.remove(); mask.remove(); }, 300); }
      }

      async function handleSessionAction(action, s) {
        if (action === 'clear') {
          const r = await api('/api/clear?session_id=' + encodeURIComponent(s.id), { method: 'POST' });
          if (r.ok) {
            if (s.id === currentSessionId) { messages = []; lastCount = 0; $('chat').querySelectorAll('.msg').forEach(el => el.remove()); $('welcome').style.display = ''; }
            await loadSessions(); showToast('已清空');
          }
        } else if (action === 'delete') {
          if (allSessions.length <= 1) { showToast('至少保留一个会话'); return; }
          const r = await api('/api/sessions/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: s.id }) });
          if (r.ok) {
            if (s.id === currentSessionId) { const rem = allSessions.filter(x => x.id !== s.id); switchSession(rem.length ? rem[0].id : 'default'); }
            await loadSessions(); showToast('已删除');
          } else { showToast(r.error || '删除失败'); }
        } else if (action === 'rename') {
          const newName = prompt('输入新名称：', s.id);
          if (!newName || !newName.trim()) return;
          const r = await api('/api/sessions/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: s.id, name: newName.trim() }) });
          if (r.ok) {
            if (s.id === currentSessionId) { currentSessionId = r.session_id; localStorage.setItem('session_id', r.session_id); }
            await loadSessions(); showToast('已改名');
          } else { showToast(r.error || '改名失败'); }
        } else if (action === 'new') {
          createSession(s.character);
        } else if (action === 'clone') {
          const r = await api('/api/sessions/clone', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: s.id }) });
          if (r.ok) {
            await loadSessions(); switchSession(r.session_id); showToast('已克隆');
          } else { showToast(r.error || '克隆失败'); }
        } else if (action === 'pin') {
          const r = await api('/api/sessions/pin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: s.id }) });
          if (r.ok) {
            await loadSessions(); showToast(r.pinned ? '已置顶' : '已取消置顶');
          }
        }
      }
      $('session-filter').addEventListener('change', renderSessionList);

      function switchSession(id) {
        if (id === currentSessionId) { closeLeft(); return; }
        currentSessionId = id;
        localStorage.setItem('session_id', id);
        maxDisplayCount = 40; // <--- ⭐️ 补上这一行：切换会话时重置折叠
        messages = []; lastCount = 0; prevMsgCount = 0; prevLastRole = '';
        renderMessages();
        $('welcome').style.display = '';
        closeLeft();
        syncOnce();
        refreshRightPanel(true); // 切换会话：用新会话的服务器绑定重置
      }

      // ---------- 新建会话 ----------
      $('btn-new-session').addEventListener('click', () => {
        const filter = $('session-filter').value;
        if (filter && filter !== '__all__') {
          createSession(filter);
        } else {
          closeLeft();
          openCharacterPicker((charKey) => createSession(charKey));
        }
      });

      async function createSession(charKey) {
        const data = await api('/api/sessions/create', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ character: charKey })
        });
        if (data.ok) {
          await loadSessions();
          switchSession(data.session_id);
          showToast('已创建新会话');
        } else { showToast('创建失败'); }
      }

      $('btn-new-same-char').addEventListener('click', () => {
        const cur = allSessions.find(s => s.id === currentSessionId);
        createSession(cur ? cur.character : 'default');
      });

      // ---------- 提示词缓存（名字列表 + 当前会话绑定）----------
      // 选择面板全部同步地从这些缓存渲染，不在打开时联网。缓存只在：初始化、打开抽屉、
      // 切换会话、以及增删改提示词文件之后刷新。
      async function loadPromptTree() {
        const data = await api(withSid('/api/prompts/list'));
        promptTree = data.tree || promptTree;
        promptActive = data.active || {};
        characterMeta = data.characters || [];
        const pdata = await api('/api/presets/list');
        presetList = pdata.presets || [];
      }

      // ---------- 右侧设置面板 ----------
      // seed=true 时用服务器值重置当前会话绑定（仅切换会话/首次加载）。
      // 仅打开设置面板时 seed=false，避免覆盖用户已选但尚未发消息的本地选择。
      async function refreshRightPanel(seed) {
        await loadPromptTree();
        if (seed) seedBindingFromServer();
        renderBindingLabels();
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        $('val-theme').textContent = isDark ? '深色' : '浅色';

        const cur = allSessions.find(s => s.id === currentSessionId);

        if (cur) {
          $('rp-avatar').src = cur.avatar || '/logo.png';
          $('rp-name').textContent = cur.character_name;
          $('topbar-avatar').src = cur.avatar || '/logo.png';
          $('topbar-title').textContent = cur.character_name;
        } else if (allSessions.length > 0) {
          // ⭐️ 补上这 3 行：如果本地记忆的 ID 在服务器端找不到，自动强行切到列表里的第一个有效会话！
          switchSession(allSessions[0].id);
        } else if (allSessions.length === 0) {
          await loadSessions();
          refreshRightPanel(seed);
        }
      }

      function renderBindingLabels() {
        $('val-world').textContent = sessionBinding.world || 'default';
        $('val-user').textContent = sessionBinding.user || 'default';
        $('val-preset').textContent = sessionBinding.preset || 'default';
      }

      $('row-world').addEventListener('click', () => openSelectSheet('world', '世界设定'));
      $('row-user').addEventListener('click', () => openSelectSheet('user', '用户设定'));
      $('row-preset').addEventListener('click', openPresetSelectSheet);
      $('row-theme').addEventListener('click', openThemeSheet);
      // 气泡模式开关：点击切换
      $('val-bubble').textContent = bubbleMode ? '开' : '关';
      $('row-bubble').addEventListener('click', () => {
        bubbleMode = !bubbleMode;
        localStorage.setItem('chat-bubble', bubbleMode ? '1' : '0');
        $('val-bubble').textContent = bubbleMode ? '开' : '关';
        lastCount = 0; // 避免误触发打字机
        renderMessages(true);
      });
      $('row-debug').addEventListener('click', async () => {
        const data = await api(withSid('/api/debug/last_prompt'));
        if (data.error) { showToast(data.error); return; }
        const lines = [];
        lines.push('⏱ ' + (data.ts || ''));
        lines.push('🤖 ' + (data.model || ''));
        lines.push('─'.repeat(30));
        (data.messages || []).forEach(m => {
          const role = m.role.toUpperCase();
          const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content).substring(0, 500);
          lines.push('\n【' + role + '】');
          lines.push(content);
        });
        const mask = document.createElement('div');
        mask.className = 'action-sheet-mask';
        const box = document.createElement('div');
        box.style.cssText = 'position:fixed;top:5%;left:3%;right:3%;bottom:5%;z-index:610;background:var(--surface);border-radius:14px;display:flex;flex-direction:column;overflow:hidden;';
        const header = document.createElement('div');
        header.style.cssText = 'padding:14px 16px;font-weight:600;font-size:15px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;';
        header.innerHTML = '<span>调试日志</span><button style="background:none;border:none;font-size:20px;color:var(--text);cursor:pointer;">✕</button>';
        const body = document.createElement('pre');
        body.style.cssText = 'flex:1;overflow:auto;padding:12px 16px;margin:0;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;color:var(--text);font-family:monospace;';
        body.textContent = lines.join('\n');
        box.appendChild(header);
        box.appendChild(body);
        const dismiss = () => { box.remove(); mask.remove(); };
        header.querySelector('button').addEventListener('click', dismiss);
        mask.addEventListener('click', dismiss);
        document.body.appendChild(mask);
        document.body.appendChild(box);
        requestAnimationFrame(() => mask.classList.add('show'));
      });

      // ---------- 记忆管理面板 ----------
      $('row-memory').addEventListener('click', openMemoryPanel);

      function openMemoryPanel() {
        const esc = s => (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        const post = (path, obj) => api(withSid(path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) });
        const BTN = 'padding:5px 11px;border:none;border-radius:8px;font-size:12px;cursor:pointer;background:var(--user-bubble,#0a84ff);color:#fff;';
        const BTNG = 'padding:5px 11px;border:none;border-radius:8px;font-size:12px;cursor:pointer;background:var(--border);color:var(--text);';
        const INP = 'flex:1;min-width:0;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px;';
        const TA = 'width:100%;box-sizing:border-box;padding:7px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px;line-height:1.5;resize:vertical;min-height:46px;';
        const SECH = 'font-size:12px;color:var(--text-secondary);margin:14px 0 6px;font-weight:600;';
        const DEL = 'background:none;border:none;color:#ff3b30;cursor:pointer;font-size:13px;padding:4px 6px;';

        const mask = document.createElement('div');
        mask.className = 'action-sheet-mask';
        const box = document.createElement('div');
        box.style.cssText = 'position:fixed;top:5%;left:3%;right:3%;bottom:5%;z-index:610;background:var(--surface);border-radius:14px;display:flex;flex-direction:column;overflow:hidden;';
        const header = document.createElement('div');
        header.style.cssText = 'padding:14px 16px;font-weight:600;font-size:15px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;';
        header.innerHTML = '<span>记忆管理</span><button style="background:none;border:none;font-size:20px;color:var(--text);cursor:pointer;">✕</button>';
        const statusBar = document.createElement('div');
        statusBar.style.cssText = 'padding:10px 16px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center;flex-wrap:wrap;';
        const bodyEl = document.createElement('div');
        bodyEl.style.cssText = 'flex:1;overflow:auto;padding:6px 16px 20px;';
        box.appendChild(header); box.appendChild(statusBar); box.appendChild(bodyEl);
        const dismiss = () => { box.remove(); mask.remove(); };
        header.querySelector('button').addEventListener('click', dismiss);
        mask.addEventListener('click', dismiss);
        document.body.appendChild(mask); document.body.appendChild(box);
        requestAnimationFrame(() => mask.classList.add('show'));

        async function render() { draw(await api(withSid('/api/memory/overview'))); }

        function draw(d) {
          if (!d || !d.counts) { bodyEl.innerHTML = '<div style="color:var(--text-secondary);padding:20px;text-align:center;">读取失败</div>'; return; }
          const m = d.meta || {};
          const unsumm = (m.total_messages || 0) - (m.boundary || 0);
          let st = `已总结 ${m.boundary || 0}/${m.total_messages || 0} 条 · 事实${d.counts.facts}·事件${d.counts.events}·切片${d.counts.chunks}`;
          let col = 'var(--text-secondary)';
          if (m.state === 'running') st += ' · 总结中…';
          else if (m.last_status === 'success') st += ` · ✓ ${m.last_time || ''}`;
          else if (m.last_status === 'failed') { st += ` · ⚠ ${m.last_error || '失败'}`; col = '#ff3b30'; }
          const running = m.state === 'running';
          statusBar.innerHTML =
            `<div style="font-size:12px;color:${col};flex:1;min-width:140px;">${esc(st)}</div>` +
            `<button class="mem-sum" ${running ? 'disabled' : ''} style="${BTN}${running ? 'opacity:.5;' : ''}">${running ? '总结中…' : (unsumm > 0 ? ('补总结 (' + unsumm + ')') : '重新总结')}</button>` +
            `<button class="mem-refresh" style="${BTNG}">刷新</button>`;

          let h = '';
          h += `<div style="${SECH}">关系弧</div><textarea class="mem-sum-ta" data-key="${esc(d.arc_key)}" style="${TA}">${esc(d.arc)}</textarea><div style="text-align:right;margin-top:4px;"><button class="mem-save-sum" data-key="${esc(d.arc_key)}" style="${BTN}">保存</button></div>`;
          h += `<div style="${SECH}">近况</div><textarea class="mem-sum-ta" data-key="${esc(d.session_key)}" style="${TA}">${esc(d.session_summary)}</textarea><div style="text-align:right;margin-top:4px;"><button class="mem-save-sum" data-key="${esc(d.session_key)}" style="${BTN}">保存</button></div>`;

          h += `<div style="${SECH}">硬事实 (${d.facts.length})</div>`;
          d.facts.forEach(f => {
            h += `<div class="mem-fact" data-id="${f.id}" style="display:flex;gap:5px;align-items:center;margin:5px 0;">` +
              `<input data-f="subject" value="${esc(f.subject)}" style="${INP}max-width:80px;">` +
              `<input data-f="predicate" value="${esc(f.predicate)}" style="${INP}max-width:90px;">` +
              `<input data-f="object" value="${esc(f.object)}" style="${INP}">` +
              `<button class="mem-fact-save" style="${BTN}padding:5px 8px;">✓</button>` +
              `<button class="mem-fact-del" style="${DEL}">✕</button></div>`;
          });

          h += `<div style="${SECH}">事件 (${d.events.length})</div>`;
          d.events.forEach(e => {
            h += `<div class="mem-ev" data-id="${e.id}" style="margin:8px 0;padding:8px;border:1px solid var(--border);border-radius:8px;">` +
              `<div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">[${esc(e.type) || '—'} / ${esc(e.weight) || '—'}]　重要度 ${e.importance || 3}</div>` +
              `<textarea class="mem-ev-sum" style="${TA}min-height:40px;">${esc(e.summary)}</textarea>` +
              `<div style="text-align:right;margin-top:4px;"><button class="mem-ev-save" style="${BTN}">保存</button> <button class="mem-ev-del" style="${DEL}">删除</button></div></div>`;
          });
          if (!d.facts.length && !d.events.length) h += `<div style="color:var(--text-secondary);font-size:13px;padding:14px 0;">该角色还没有记忆。点上面「补总结」从历史对话生成。</div>`;
          bodyEl.innerHTML = h;
          wire();
        }

        function wire() {
          box.querySelector('.mem-refresh').addEventListener('click', render);
          box.querySelector('.mem-sum').addEventListener('click', async (ev) => {
            if (ev.target.disabled) return;
            await post('/api/memory/summarize', {});
            showToast('开始总结…');
            for (let i = 0; i < 25; i++) {
              await new Promise(r => setTimeout(r, 3000));
              const d = await api(withSid('/api/memory/overview'));
              draw(d);
              if (!d.meta || d.meta.state !== 'running') {
                showToast(d.meta && d.meta.last_status === 'failed' ? ('总结失败: ' + (d.meta.last_error || '')) : '总结完成');
                return;
              }
            }
          });
          box.querySelectorAll('.mem-save-sum').forEach(b => b.addEventListener('click', async () => {
            const key = b.dataset.key;
            const ta = box.querySelector(`.mem-sum-ta[data-key="${key}"]`);
            const r = await post('/api/memory/edit', { table: 'summaries', key, text: ta.value });
            showToast(r.ok ? '已保存' : '保存失败');
          }));
          box.querySelectorAll('.mem-fact').forEach(row => {
            const id = +row.dataset.id;
            row.querySelector('.mem-fact-save').addEventListener('click', async () => {
              const g = f => row.querySelector(`[data-f="${f}"]`).value;
              const r = await post('/api/memory/edit', { table: 'facts', id, subject: g('subject'), predicate: g('predicate'), object: g('object') });
              showToast(r.ok ? '已保存' : '保存失败');
            });
            row.querySelector('.mem-fact-del').addEventListener('click', async () => {
              await post('/api/memory/forget', { table: 'facts', id }); showToast('已删除'); render();
            });
          });
          box.querySelectorAll('.mem-ev').forEach(row => {
            const id = +row.dataset.id;
            row.querySelector('.mem-ev-save').addEventListener('click', async () => {
              const r = await post('/api/memory/edit', { table: 'events', id, summary: row.querySelector('.mem-ev-sum').value });
              showToast(r.ok ? '已保存（已重算向量）' : '保存失败');
            });
            row.querySelector('.mem-ev-del').addEventListener('click', async () => {
              await post('/api/memory/forget', { table: 'events', id }); showToast('已删除'); render();
            });
          });
        }

        render();
      }

      // ---------- 通用：选择某分类的提示词文件 ----------
      function pencilSvg() {
        return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';
      }

      // 统一的下弹选择框：同步打开 + 同步从缓存渲染 + 事件委托（一个监听器，永不因重建而失效）。
      // 这是根治“点选框点了没反应”的核心：打开路径里没有任何 await/联网。
      let _sheetClickHandler = null;
      function renderSheet(title, items, opts) {
        $('sheet-title').textContent = title;
        $('sheet-new-btn').style.display = opts.onNew ? '' : 'none';
        const box = $('sheet-list');
        box.innerHTML = '';
        items.forEach(it => {
          const row = document.createElement('div');
          row.className = 'sheet-option' + (it.selected ? ' selected' : '');
          row.dataset.name = it.name;
          let html = '<span class="sheet-option-name">' +
            (it.avatar ? '<img src="' + it.avatar + '">' : '') + escHtml(it.label) + '</span>';
          if (opts.onEdit) html += '<button class="sheet-edit-pencil" data-act="edit">' + pencilSvg() + '</button>';
          if (opts.onKebab) html += '<button class="session-kebab" data-act="kebab" style="font-size:18px;">⋮</button>';
          row.innerHTML = html;
          box.appendChild(row);
        });
        if (_sheetClickHandler) box.removeEventListener('click', _sheetClickHandler);
        _sheetClickHandler = (e) => {
          const row = e.target.closest('.sheet-option');
          if (!row) return;
          const name = row.dataset.name;
          const actBtn = e.target.closest('[data-act]');
          const act = actBtn ? actBtn.dataset.act : 'select';
          if (act === 'edit' && opts.onEdit) { e.stopPropagation(); opts.onEdit(name); }
          else if (act === 'kebab' && opts.onKebab) { e.stopPropagation(); opts.onKebab(name); }
          else opts.onSelect(name);
        };
        box.addEventListener('click', _sheetClickHandler);
        $('sheet-new-btn').onclick = opts.onNew || null;
        openSheet('sheet-select', 'mask-sheet');
      }

      // 增删改提示词文件之后刷新缓存，再回到原选择框
      async function refreshCachesThen(cb) {
        await loadPromptTree();
        if (cb) cb();
      }

      // 选择世界/用户：纯本地，零联网。改 sessionBinding + 标签，下次发消息随 config 送出。
      function openSelectSheet(category, title) {
        const names = promptTree[category] || [];
        const cur = sessionBinding[category];
        renderSheet(title, names.map(n => ({ name: n, label: n, selected: n === cur })), {
          onSelect: async (name) => {
            sessionBinding[category] = name;
            renderBindingLabels();
            closeSheet('sheet-select', 'mask-sheet');
            // 修复：立即提交到后端保存，不再等到发消息时
            await api(withSid('/api/prompts/use'), {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ [category]: name })
            });
          },
          onEdit: (name) => openEditSheet(category, name, () => refreshCachesThen(() => openSelectSheet(category, title))),
          onNew: () => openEditSheet(category, null, () => refreshCachesThen(() => openSelectSheet(category, title))),
        });
      }

      async function openEditSheet(category, name, onDone) {
        $('edit-avatar-row').style.display = category === 'character' ? '' : 'none';
        $('edit-name').value = '';
        $('edit-name').disabled = false;
        $('edit-content').value = '';
        $('edit-avatar-preview').src = '/logo.png';
        let avatarData = '';
        let displayName = '';
        if (name) {
          const data = await api('/api/prompts/get?category=' + category + '&name=' + encodeURIComponent(name));
          if (data.ok) {
            displayName = data.data.name || name;
            $('edit-name').value = displayName;
            $('edit-content').value = data.data.content || '';
            avatarData = data.data.avatar || '';
            if (avatarData) $('edit-avatar-preview').src = avatarData;
          }
        }
        $('edit-avatar-file').onchange = (e) => {
          const file = e.target.files[0]; if (!file) return;
          const reader = new FileReader();
          reader.onload = (ev) => { avatarData = ev.target.result; $('edit-avatar-preview').src = avatarData; };
          reader.readAsDataURL(file);
        };
        $('edit-delete-btn').style.visibility = (name && name !== 'default') ? 'visible' : 'hidden';
        $('edit-delete-btn').onclick = async () => {
          if (!confirm('确认删除「' + (displayName || name) + '」？')) return;
          await api('/api/prompts/delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category, name })
          });
          closeSheet('sheet-edit', 'mask-edit');
          setTimeout(() => { if (onDone) onDone(); }, 300);
        };
        $('edit-save-btn').onclick = async () => {
          const newDisplayName = $('edit-name').value.trim();
          if (!newDisplayName) { showToast('请填写名称'); return; }
          const payload = { category, name: name || newDisplayName, content: $('edit-content').value, display_name: newDisplayName };
          if (name) payload.old_name = name;
          if (category === 'character') payload.avatar = avatarData;
          const r = await api('/api/prompts/save', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          if (r.ok) {
            showToast('已保存');
            closeSheet('sheet-edit', 'mask-edit');
            setTimeout(() => { if (onDone) onDone(); }, 300);
          } else { showToast('保存失败'); }
        };
        openSheet('sheet-edit', 'mask-edit');
      }

      // ---------- 角色选择/管理 ----------
      function openCharacterPicker(onPick) {
        renderCharacterSheet('选择角色', (name) => { closeSheet('sheet-select', 'mask-sheet'); onPick(name); });
      }
      function openCharacterManager() {
        renderCharacterSheet('角色管理', (name) => openEditSheet('character', name, openCharacterManager));
      }
      $('btn-char-manage').addEventListener('click', openCharacterManager);

      function renderCharacterSheet(title, onRowClick) {
        renderSheet(title, characterMeta.map(m => ({
          name: m.key, label: m.name || m.key, avatar: m.avatar || '/logo.png'
        })), {
          onSelect: (key) => onRowClick(key),
          onKebab: (key) => {
            const meta = characterMeta.find(m => m.key === key) || { key };
            showCharacterActions(meta, title, onRowClick);
          },
          onNew: () => openEditSheet('character', null, () => refreshCachesThen(() => renderCharacterSheet(title, onRowClick))),
        });
      }

      function showCharacterActions(meta, sheetTitle, onRowClick) {
        const mask = document.createElement('div');
        mask.className = 'action-sheet-mask';
        const sheet = document.createElement('div');
        sheet.className = 'action-sheet';

        const mainGroup = document.createElement('div');
        mainGroup.className = 'action-sheet-group';
        const items = [
          { label: '编辑', action: 'edit' },
          { label: '克隆', action: 'clone' },
        ];
        items.forEach(it => {
          const row = document.createElement('div');
          row.className = 'action-sheet-item';
          row.textContent = it.label;
          row.addEventListener('click', () => {
            dismiss();
            if (it.action === 'edit') {
              openEditSheet('character', meta.key, () => refreshCachesThen(() => renderCharacterSheet(sheetTitle, onRowClick)));
            } else if (it.action === 'clone') {
              handleCloneCharacter(meta, sheetTitle, onRowClick);
            }
          });
          mainGroup.appendChild(row);
        });
        sheet.appendChild(mainGroup);

        if (meta.key !== 'default') {
          const delGroup = document.createElement('div');
          delGroup.className = 'action-sheet-group';
          const delRow = document.createElement('div');
          delRow.className = 'action-sheet-item destructive';
          delRow.textContent = '删除';
          delRow.addEventListener('click', async () => {
            dismiss();
            const r = await api('/api/prompts/delete', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ category: 'character', name: meta.key })
            });
            if (r.ok) { showToast('已删除'); refreshCachesThen(() => renderCharacterSheet(sheetTitle, onRowClick)); }
            else { showToast(r.error || '删除失败'); }
          });
          delGroup.appendChild(delRow);
          sheet.appendChild(delGroup);
        }

        const cancel = document.createElement('div');
        cancel.className = 'action-sheet-cancel';
        cancel.textContent = '取消';
        cancel.addEventListener('click', dismiss);
        sheet.appendChild(cancel);
        mask.addEventListener('click', dismiss);
        document.body.appendChild(mask);
        document.body.appendChild(sheet);
        requestAnimationFrame(() => { mask.classList.add('show'); sheet.classList.add('show'); });
        function dismiss() { sheet.classList.remove('show'); mask.classList.remove('show'); setTimeout(() => { sheet.remove(); mask.remove(); }, 300); }
      }

      async function handleCloneCharacter(meta, sheetTitle, onRowClick) {
        const src = await api('/api/prompts/get?category=character&name=' + encodeURIComponent(meta.key));
        if (!src.ok) { showToast('读取失败'); return; }
        const d = src.data;
        let cloneName = meta.key + '_copy';
        let n = 1;
        while (characterMeta.some(c => c.key === cloneName + n)) n++;
        cloneName = cloneName + n;
        const r = await api('/api/prompts/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: 'character', name: cloneName, content: d.content || '', avatar: d.avatar || '' })
        });
        if (r.ok) { showToast('已克隆'); refreshCachesThen(() => renderCharacterSheet(sheetTitle, onRowClick)); }
        else { showToast('克隆失败'); }
      }

      // ---------- 预设（引用 main/style/post 的命名包）----------
      // 选择预设：纯本地，零联网。只改 sessionBinding.preset，发消息时随 config 送出。
      function openPresetSelectSheet() {
        const cur = sessionBinding.preset;
        renderSheet('预设', presetList.map(n => ({ name: n, label: n, selected: n === cur })), {
          onSelect: async (name) => {
            sessionBinding.preset = name;
            renderBindingLabels();
            closeSheet('sheet-select', 'mask-sheet');
            // 修复：立即提交到后端保存
            await api(withSid('/api/prompts/use'), {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ preset: name })
            });
          },
          onKebab: (name) => showPresetActions(name),
          onNew: () => openPresetEditSheet(null),
        });
      }

      function showPresetActions(name) {
        const mask = document.createElement('div');
        mask.className = 'action-sheet-mask';
        const sheet = document.createElement('div');
        sheet.className = 'action-sheet';

        const mainGroup = document.createElement('div');
        mainGroup.className = 'action-sheet-group';
        const editRow = document.createElement('div');
        editRow.className = 'action-sheet-item';
        editRow.textContent = '编辑';
        editRow.addEventListener('click', () => { dismiss(); openPresetEditSheet(name); });
        mainGroup.appendChild(editRow);
        sheet.appendChild(mainGroup);

        if (name !== 'default') {
          const delGroup = document.createElement('div');
          delGroup.className = 'action-sheet-group';
          const delRow = document.createElement('div');
          delRow.className = 'action-sheet-item destructive';
          delRow.textContent = '删除';
          delRow.addEventListener('click', async () => {
            dismiss();
            const r = await api('/api/presets/delete', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name })
            });
            if (r.ok) { showToast('已删除'); refreshCachesThen(openPresetSelectSheet); }
            else { showToast(r.error || '删除失败'); }
          });
          delGroup.appendChild(delRow);
          sheet.appendChild(delGroup);
        }

        const cancel = document.createElement('div');
        cancel.className = 'action-sheet-cancel';
        cancel.textContent = '取消';
        cancel.addEventListener('click', dismiss);
        sheet.appendChild(cancel);
        mask.addEventListener('click', dismiss);
        document.body.appendChild(mask);
        document.body.appendChild(sheet);
        requestAnimationFrame(() => { mask.classList.add('show'); sheet.classList.add('show'); });
        function dismiss() { sheet.classList.remove('show'); mask.classList.remove('show'); setTimeout(() => { sheet.remove(); mask.remove(); }, 300); }
      }

      // 预设编辑器：预设是 main/style/post 三个文件的“引用包”。
      // 三个下拉从缓存的 promptTree 同步填充；每格旁的按钮可编辑选中项或新建一个。
      function fillPresetSelect(slot, selected) {
        const sel = $('preset-' + slot + '-sel');
        const names = promptTree[slot] || [];
        sel.innerHTML = names.map(n =>
          '<option value="' + escHtml(n) + '"' + (n === selected ? ' selected' : '') + '>' + escHtml(n) + '</option>'
        ).join('');
        if (!names.includes(selected) && names.length) sel.value = names[0];
      }

      function openPresetEditSheet(name) {
        $('preset-name').value = name || '';
        $('preset-name').disabled = !!name;

        let refs = { main: 'default', style: 'default', post: 'default' };
        if (name) {
          // 预设文件存的就是三个名字（引用），缓存里没有就现取一次（这是“读取内容”，允许联网）
          api('/api/presets/get?name=' + encodeURIComponent(name)).then(data => {
            if (data.ok && data.data) {
              refs = { main: data.data.main || 'default', style: data.data.style || 'default', post: data.data.post || 'default' };
            }
            ['main', 'style', 'post'].forEach(s => fillPresetSelect(s, refs[s]));
          });
        }
        ['main', 'style', 'post'].forEach(s => fillPresetSelect(s, refs[s]));

        // 每格的“编辑/新建”按钮：编辑当前选中的文件内容，或在选择里新建
        ['main', 'style', 'post'].forEach(slot => {
          $('sheet-preset-edit').querySelector('[data-slot="' + slot + '"]').onclick = () => {
            const chosen = $('preset-' + slot + '-sel').value;
            // 编辑这个 main/style/post 文件的内容；保存后刷新缓存并回填下拉
            openEditSheet(slot, chosen || null, () => refreshCachesThen(() => {
              fillPresetSelect(slot, chosen);
              openSheet('sheet-preset-edit', 'mask-preset-edit');
            }));
          };
        });

        $('preset-delete-btn').style.visibility = (name && name !== 'default') ? 'visible' : 'hidden';
        $('preset-delete-btn').onclick = async () => {
          if (!confirm('确认删除预设「' + name + '」？')) return;
          await api('/api/presets/delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
          });
          closeSheet('sheet-preset-edit', 'mask-preset-edit');
          refreshCachesThen(openPresetSelectSheet);
        };
        $('preset-save-btn').onclick = async () => {
          const finalName = name || $('preset-name').value.trim();
          if (!finalName) { showToast('请填写预设名称'); return; }
          const r = await api('/api/presets/save', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name: finalName,
              main: $('preset-main-sel').value || 'default',
              style: $('preset-style-sel').value || 'default',
              post: $('preset-post-sel').value || 'default',
            })
          });
          if (r.ok) {
            showToast('已保存');
            closeSheet('sheet-preset-edit', 'mask-preset-edit');
            refreshCachesThen(openPresetSelectSheet);
          } else { showToast(r.error || '保存失败'); }
        };
        openSheet('sheet-preset-edit', 'mask-preset-edit');
      }

      // ---------- 主题 ----------
      function openThemeSheet() {
        const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        renderSheet('主题', [
          { name: 'light', label: '浅色', selected: cur === 'light' },
          { name: 'dark', label: '深色', selected: cur === 'dark' },
        ], {
          onSelect: (k) => { setTheme(k); closeSheet('sheet-select', 'mask-sheet'); },
        });
      }

      // ---------- 初始化 ----------
      // ---------- 初始化 ----------
      loadSessions().then(() => refreshRightPanel(true));

      // ================= 全局配置交互与模型拉取逻辑 =================
      $('mask-global-config').addEventListener('click', () => closeSheet('sheet-global-config', 'mask-global-config'));
      $('cfg-cancel-btn').addEventListener('click', () => closeSheet('sheet-global-config', 'mask-global-config'));

      $('row-global-config').addEventListener('click', async () => {
        closeRight();
        showToast('正在读取后端配置...');
        const cfg = await api('/api/config');
        if (!cfg.ok && !cfg.mode) { showToast('读取配置失败'); return; }

        // 回填 API
        $('cfg-api-url').value = cfg.api?.base_url || '';
        $('cfg-api-key').value = cfg.api?.api_key || '';
        $('cfg-api-model').value = cfg.api?.model || '';
        //$('cfg-api-sys').value = cfg.api?.system_prompt || '';

        // 回填 Summary
        $('cfg-sum-url').value = cfg.summary_api?.base_url || '';
        $('cfg-sum-key').value = cfg.summary_api?.api_key || '';
        $('cfg-sum-model').value = cfg.summary_api?.model || '';

        // 回填 Embedding
        $('cfg-emb-enabled').checked = !!cfg.embedding?.enabled;
        $('cfg-emb-url').value = cfg.embedding?.base_url || '';
        $('cfg-emb-key').value = cfg.embedding?.api_key || '';
        $('cfg-emb-model').value = cfg.embedding?.model || '';

        // 回填 Memory 设定
        $('cfg-mem-recent').value = cfg.memory?.recent_rounds || 10;
        $('cfg-mem-every').value = cfg.memory?.summarize_every || 16;
        $('cfg-mem-recall').value = cfg.memory?.recall_n || 30;
        $('cfg-mem-topk').value = cfg.memory?.top_k || 5;

        openSheet('sheet-global-config', 'mask-global-config');
      });

      // 通用模型拉取生成器
      function bindModelFetcher(btnId, urlId, keyId, inputId, selId) {
        $(btnId).onclick = async () => {
          const baseUrl = $(urlId).value.trim();
          const apiKey = $(keyId).value.trim();
          if (!baseUrl) { showToast('请先填写 Base URL'); return; }

          $(btnId).disabled = true;
          $(btnId).textContent = '连接中...';
          try {
            const r = await fetch(API + '/api/test_models', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ base_url: baseUrl, api_key: apiKey })
            });
            const d = await r.json();
            if (d.ok && d.models?.length) {
              showToast(`连接成功！共拉取到 ${d.models.length} 个可用模型`);
              const sel = $(selId);
              sel.innerHTML = '<option value="">-- 点此选择拉取到的模型 --</option>' +
                d.models.map(m => `<option value="${escHtml(m)}">${escHtml(m)}</option>`).join('');
              sel.style.display = 'inline-block';
              sel.onchange = () => { if (sel.value) $(inputId).value = sel.value; };
            } else {
              showToast('拉取失败: ' + (d.error || '目标地址未返回标准模型列表'));
            }
          } catch (e) { showToast('无法连接后端测试代理'); }
          finally { $(btnId).disabled = false; $(btnId).textContent = '测试并拉取'; }
        };
      }

      bindModelFetcher('btn-test-api', 'cfg-api-url', 'cfg-api-key', 'cfg-api-model', 'cfg-api-model-sel');
      bindModelFetcher('btn-test-sum', 'cfg-sum-url', 'cfg-sum-key', 'cfg-sum-model', 'cfg-sum-model-sel');

      // 保存全局配置
      $('cfg-save-btn').onclick = async () => {
        const payload = {
          api: {
            base_url: $('cfg-api-url').value.trim(),
            api_key: $('cfg-api-key').value.trim(),
            model: $('cfg-api-model').value.trim(),
            //system_prompt: $('cfg-api-sys').value.trim()
          },
          summary_api: {
            base_url: $('cfg-sum-url').value.trim(),
            api_key: $('cfg-sum-key').value.trim(),
            model: $('cfg-sum-model').value.trim()
          },
          embedding: {
            enabled: $('cfg-emb-enabled').checked,
            base_url: $('cfg-emb-url').value.trim(),
            api_key: $('cfg-emb-key').value.trim(),
            model: $('cfg-emb-model').value.trim()
          },
          memory: {
            recent_rounds: parseInt($('cfg-mem-recent').value) || 10,
            summarize_every: parseInt($('cfg-mem-every').value) || 16,
            recall_n: parseInt($('cfg-mem-recall').value) || 30,
            top_k: parseInt($('cfg-mem-topk').value) || 5
          }
        };

        $('cfg-save-btn').textContent = '保存中...';
        const r = await fetch(API + '/api/config/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const d = await r.json();
        if (d.ok) {
          showToast('全局配置已成功保存并实时生效！');
          closeSheet('sheet-global-config', 'mask-global-config');
        } else { showToast('保存失败'); }
        $('cfg-save-btn').textContent = '保存全局配置';
      };

    })();
