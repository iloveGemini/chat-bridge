    let messages = [];
    let lastCount = 0;
    let sending = false;
    let connected = false;
    let pendingImageBase64 = null;
    let pageVisible = true;
    let originalTitle = 'Chat Engine';
    let titleFlashTimer = null;
    let wakeLock = null;
    let typingFromServer = false;
    let pendingFromServer = false;
    let currentSessionId = localStorage.getItem('session_id') || 'default';
    let maxDisplayCount = 40;
    function sid() { return 'session_id=' + encodeURIComponent(currentSessionId); }
    function withSid(url) { return url + (url.includes('?') ? '&' : '?') + sid(); }

    let isGenerating = false;
    const iconSend = '<svg viewBox="0 0 24 24"><path d="M3.4 20.4l17.45-7.48a1 1 0 000-1.84L3.4 3.6a.993.993 0 00-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/></svg>';
    const iconStop = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="3"/></svg>';

    function setGeneratingState(gen) {
      isGenerating = gen;
      const btn = $('btn-send');
      const input = $('input');
      if (gen) {
        btn.innerHTML = iconStop;
        btn.style.background = '';
        input.disabled = true;
        input.placeholder = "等待回复中...";
        startPolling(); // 开始轮询
      } else {
        btn.innerHTML = iconSend;
        btn.style.background = '';
        input.disabled = false;
        input.placeholder = "Message...";
        $('typing').classList.remove('show');
        stopPolling(); // 彻底掐断轮询
      }
    }

    // 增加移动端识别
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

    const $ = id => document.getElementById(id);

    function escHtml(s) {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }

    function renderMarkdown(text) {
      let html = escHtml(text);
      html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, function (m, lang, code) { return '\x00PRE\x00' + code.trim() + '\x00/PRE\x00'; });
      html = html.replace(/`([^`]+)`/g, '\x00CODE\x00$1\x00/CODE\x00');
      html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
      html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      html = html.replace(/(?<!href="|">)(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
      html = html.replace(/^([-*]){3,}\s*$/gm, '<hr>');
      html = html.replace(/\x00PRE\x00([\s\S]*?)\x00\/PRE\x00/g, '<pre><code>$1</code></pre>');
      html = html.replace(/\x00CODE\x00([\s\S]*?)\x00\/CODE\x00/g, '<code>$1</code>');

      const codeBlockRe = /(<pre><code>[\s\S]*?<\/code><\/pre>)/g;
      const segments = html.split(codeBlockRe);
      let out = '';
      for (let s = 0; s < segments.length; s++) {
        if (segments[s].startsWith('<pre><code>')) { out += segments[s]; continue; }
        const lines = segments[s].split('\n');
        let buf = '', inUl = false, inOl = false, inBq = false;
        for (let li = 0; li < lines.length; li++) {
          const line = lines[li];
          const ulMatch = line.match(/^[\-\*]\s+(.+)/);
          const olMatch = line.match(/^\d+\.\s+(.+)/);
          const bqMatch = line.match(/^&gt;\s?(.*)/);
          const isBlank = line === '';
          if (!ulMatch && !isBlank && inUl) { buf += '</ul>'; inUl = false; }
          if (!olMatch && !isBlank && inOl) { buf += '</ol>'; inOl = false; }
          if (!bqMatch && !isBlank && inBq) { buf += '</blockquote>'; inBq = false; }
          if (ulMatch) {
            if (!inUl) { buf += '<ul>'; inUl = true; }
            buf += '<li>' + ulMatch[1] + '</li>';
          } else if (olMatch) {
            if (!inOl) { buf += '<ol>'; inOl = true; }
            buf += '<li>' + olMatch[1] + '</li>';
          } else if (bqMatch) {
            if (!inBq) { buf += '<blockquote>'; inBq = true; }
            buf += bqMatch[1] + '<br>';
          } else if (isBlank) {
            if (!inUl && !inOl && !inBq) buf += '<br>';
          } else {
            buf += line + '<br>';
          }
        }
        if (inUl) buf += '</ul>';
        if (inOl) buf += '</ol>';
        if (inBq) buf += '</blockquote>';
        out += buf;
      }
      out = out.replace(/<br>(<\/?(?:ul|ol|li|blockquote|pre|hr))/g, '$1');
      return out;
    }

    let typewriterActive = null;
    function typewriterFinish() {
      if (!typewriterActive) return;
      clearInterval(typewriterActive.timer);
      typewriterActive.el.innerHTML = typewriterActive.fullHtml;
      const cb = typewriterActive.onDone;
      typewriterActive = null;
      if (cb) cb();
    }

    function typewriterReveal(bubbleEl, fullHtml, onDone) {
      if (typewriterActive) typewriterFinish();
      const tokens = [];
      const re = /(<[^>]+>)|([^<])/g;
      let m;
      while ((m = re.exec(fullHtml)) !== null) {
        if (m[1]) tokens.push({ tag: true, v: m[1] });
        else tokens.push({ tag: false, v: m[2] });
      }
      bubbleEl.innerHTML = '';
      let idx = 0;
      const state = {
        el: bubbleEl, fullHtml, onDone,
        timer: setInterval(() => {
          if (idx >= tokens.length) { typewriterFinish(); return; }
          let added = 0;
          while (idx < tokens.length && added < 3) {
            const t = tokens[idx++];
            if (t.tag) {
              bubbleEl.innerHTML = fullHtml.substring(0, getHtmlOffset(tokens, idx));
            } else {
              added++;
              bubbleEl.innerHTML = fullHtml.substring(0, getHtmlOffset(tokens, idx));
            }
          }
          scrollToBottom(false);
        }, 18)
      };
      typewriterActive = state;
    }

    function getHtmlOffset(tokens, upTo) {
      let len = 0;
      for (let i = 0; i < upTo && i < tokens.length; i++) len += tokens[i].v.length;
      return len;
    }

    function formatTime(ts) {
      if (!ts) return '';
      try {
        const d = new Date(ts);
        const now = new Date();
        const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        if (d.toDateString() !== now.toDateString()) {
          return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' + time;
        }
        return time;
      } catch (e) { return ''; }
    }

    function scrollToBottom(force) {
      requestAnimationFrame(() => {
        const el = $('chat-scroll');
        const dist = el.scrollHeight - (el.scrollTop + el.clientHeight);
        if (force || dist < 300) {
          el.scrollTop = el.scrollHeight;
        }
      });
    }

    // ====== 气泡模式 ======
    let bubbleMode = localStorage.getItem('chat-bubble') === '1';

    // 把一段文本按行拆成多个气泡段；代码块(```)整体保留为一段
    function splitBubbleSegments(text) {
      const lines = (text || '').split('\n');
      const segs = [];
      let fence = null;
      for (const ln of lines) {
        if (/^\s*```/.test(ln)) {
          if (fence === null) { fence = [ln]; }
          else { fence.push(ln); segs.push(fence.join('\n')); fence = null; }
          continue;
        }
        if (fence !== null) { fence.push(ln); continue; }
        const t = ln.trim();
        if (t) segs.push(t);
      }
      if (fence !== null) segs.push(fence.join('\n'));
      return segs.length ? segs : [(text || '').trim()];
    }

    // 生成一条消息内部的气泡 HTML（不含时间）。AI + 气泡模式时拆成多气泡
    function paintBubbles(div, m) {
      const isUser = m.role === 'user';
      const imgHtml = m.image ? '<img src="' + m.image + '" class="msg-img">' : '';

      if (!isUser && bubbleMode) {
        div.classList.add('msg-multi');
        return imgHtml + splitBubbleSegments(m.text)
          .map(s => {
            let textContent = s.trim();
            let isNarration = false;

            // 判断是否被全角或半角的括号完全包裹
            if ((textContent.startsWith('（') && textContent.endsWith('）')) ||
              (textContent.startsWith('(') && textContent.endsWith(')'))) {
              isNarration = true;
              // 物理剥离首尾括号
              textContent = textContent.substring(1, textContent.length - 1).trim();
            }

            const bubbleClass = isNarration ? 'msg-bubble msg-narration' : 'msg-bubble';
            return '<div class="' + bubbleClass + '">' + renderMarkdown(textContent) + '</div>';
          }).join('');
      }

      div.classList.remove('msg-multi');
      const body = isUser ? escHtml(m.text) : renderMarkdown(m.text);
      return '<div class="msg-bubble">' + imgHtml + body + '</div>';
    }

    function renderMessages(preserveScroll = false) {
      const chat = $('chat');
      const welcome = $('welcome');

      // 1. 极其干净的清场逻辑：只要数据为空，把气泡、加载条全部连根拔起
      if (messages.length === 0) {
        welcome.style.display = '';
        chat.querySelectorAll('.msg, .load-more-bar').forEach(el => el.remove());
        $('typing').classList.remove('show');
        return;
      }
      welcome.style.display = 'none';

      const total = messages.length;
      const displayCount = Math.min(total, maxDisplayCount);
      const startIndex = total - displayCount;
      const visibleList = messages.slice(startIndex);

      const scrollEl = $('chat-scroll');
      const oldScrollHeight = scrollEl.scrollHeight;
      const oldScrollTop = scrollEl.scrollTop;

      // 2. 维护顶部的“加载更多”条
      let loadMoreEl = chat.querySelector('.load-more-bar');
      if (startIndex > 0) {
        if (!loadMoreEl) {
          loadMoreEl = document.createElement('div');
          loadMoreEl.className = 'load-more-bar';
          loadMoreEl.onclick = () => {
            maxDisplayCount += 50;
            renderMessages(true);
          };
          chat.insertBefore(loadMoreEl, chat.firstChild);
        }
        loadMoreEl.textContent = `↑ 点击加载更早的消息 (还隐藏了 ${startIndex} 条)`;
      } else if (loadMoreEl) {
        loadMoreEl.remove();
      }

      chat.querySelectorAll('.msg').forEach(el => el.remove());

      // ⭐️ 逻辑解耦点 A：【打字机触发条件】 -> 必须是非初次进场，且总数变多
      const isLiveNewMsg = (lastCount > 0) && (total > lastCount);

      visibleList.forEach((m, idxInSlice) => {
        const actualIdx = startIndex + idxInSlice;
        const div = document.createElement('div');
        const isUser = m.role === 'user';
        div.className = 'msg msg-' + (isUser ? 'user' : 'ai');
        div.dataset.msgIndex = actualIdx;
        if (m.proactive) div.classList.add('msg-proactive');

        let contentHtml = '';
        if (m.image) contentHtml += '<img src="' + m.image + '" class="msg-img">';
        contentHtml += isUser ? escHtml(m.text) : renderMarkdown(m.text);

        // 语音控件：每条 AI 消息都挂一个播放按钮，点了才按需合成（关着自动读也能单点听）
        const audioHtml = (!isUser)
          ? '<div class="tts-row"><button class="tts-btn" aria-label="播放语音">' +
            '<span class="tts-ico">🔊</span><span class="tts-label">播放语音</span></button></div>'
          : '';

        // 只有真正现场收到的新消息，才播打字机（气泡模式下不走打字机，直接铺多气泡）
        const needTypewriter = !isUser && !bubbleMode && actualIdx === total - 1 && isLiveNewMsg;
        const isNewestLive = !isUser && actualIdx === total - 1 && isLiveNewMsg;

        if (needTypewriter) {
          div.innerHTML =
            '<div class="msg-bubble"></div>' + audioHtml +
            '<div class="msg-time">' + formatTime(m.ts) + '</div>';
          chat.appendChild(div);
          typewriterReveal(div.querySelector('.msg-bubble'), contentHtml, null);
        } else {
          div.innerHTML =
            paintBubbles(div, m) + audioHtml +
            '<div class="msg-time">' + formatTime(m.ts) + '</div>';
          chat.appendChild(div);
        }

        // 自动读：仅当「语音自动读」开关打开时，新到的 AI 消息才按需合成并自动播放
        if (isNewestLive && ttsAutoOn()) {
          playMessageTTS(actualIdx, div.querySelector('.tts-btn'));
        }
      });

      // ⭐️ 逻辑解耦点 B：【滚动条沉底条件】
      if (preserveScroll) {
        // 如果是点击了“加载更多”，让滚动条死死锁在老高度
        requestAnimationFrame(() => {
          scrollEl.scrollTop = oldScrollTop + (scrollEl.scrollHeight - oldScrollHeight);
        });
      } else {
        // 只要不是在往上翻看历史，无论是初次灌入80条还是新来1条，一律给我沉底！
        scrollToBottom(true);
      }

      lastCount = total;
    }

    let _fetchingMessages = false;
    async function fetchMessages() {
      if (_fetchingMessages) return;
      _fetchingMessages = true;
      try {
        const r = await fetch(withSid(API + '/api/messages'));
        if (!r.ok) { setStatus(false); return; }
        setStatus(true);
        const data = await r.json();
        if (JSON.stringify(data) !== JSON.stringify(messages)) {
          messages = data;
          renderMessages();
        }
      } catch (e) { setStatus(false); }
      finally { _fetchingMessages = false; }
    }

    function setStatus(ok) {
      connected = ok;
      $('status-dot').classList.toggle('offline', !ok);
      $('status-text').textContent = ok ? 'online' : 'offline';
    }

    async function doSubmit() {
      // 1. 如果当前正在生成，点击按钮就执行【中断】逻辑
      if (isGenerating) {
        try { await fetch(withSid(API + '/api/interrupt'), { method: 'POST' }); } catch (e) { }
        setGeneratingState(false);
        return;
      }

      // 2. 正常的发送逻辑
      const input = $('input');
      const text = input.value.trim();
      if ((!text && !pendingImageBase64) || sending) return;

      sending = true;
      setGeneratingState(true); // 进入锁死等待状态
      $('typing').classList.add('show');

      const payload = { text: text };
      if (pendingImageBase64) payload.image = pendingImageBase64;
      if (window.getSessionBinding) payload.config = window.getSessionBinding();

      input.value = '';
      input.style.height = 'auto';
      pendingImageBase64 = null;
      $('preview-box').style.display = 'none';
      $('preview-img').src = '';

      try {
        const r = await fetch(withSid(API + '/api/submit'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await r.json();
        if (data.ok) {
          await fetchMessages();
        } else {
          showToast('Failed: ' + (data.error || ''));
          setGeneratingState(false); // 失败直接恢复
        }
      } catch (e) {
        showToast('Cannot connect');
        setGeneratingState(false);   // 断网直接恢复
      }

      sending = false;
    }

    function showToast(msg) {
      const t = $('toast');
      t.textContent = msg;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2500);
    }

    // Input auto-resize
    $('input').addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, Math.round(window.innerHeight * 0.4)) + 'px';
    });

    // ====== ⭐️ 完美解决换行交互的核心改动 ⭐️ ======
    $('input').addEventListener('keydown', function (e) {
      // 如果按下了回车，且没有按Shift，且不是在手机等移动设备上
      if (e.key === 'Enter' && !e.shiftKey && !isMobile) {
        e.preventDefault(); // 阻止原生的换行
        doSubmit();         // 电脑端直接发送
      }
      // 如果是手机端（isMobile=true），代码什么都不拦截，原生的换行行为会自然留在那！
    });

    $('btn-send').addEventListener('click', doSubmit);

    $('btn-clear').addEventListener('click', async function () {
      if (!confirm('Clear all messages?')) return;
      try {
        await fetch(withSid(API + '/api/clear'), { method: 'POST' });
        messages = [];
        lastCount = 0;
        renderMessages();
        $('welcome').style.display = '';
        $('typing').classList.remove('show');
      } catch (e) { showToast('Failed'); }
    });

    const THEME_LABELS = { light: '浅色', dark: '深色', midnight: '午夜来电', paper: '纸质墨色'  };
    function setTheme(mode) {
      if (!THEME_LABELS[mode]) mode = 'light';
      if (mode === 'light') {
        document.documentElement.removeAttribute('data-theme');
      } else {
        document.documentElement.setAttribute('data-theme', mode);
      }
      localStorage.setItem('chat-theme', mode);
      const v = $('val-theme'); if (v) v.textContent = THEME_LABELS[mode];
    }
    (function () {
      const saved = localStorage.getItem('chat-theme');
      if (saved && saved !== 'light' && THEME_LABELS[saved]) {
        document.documentElement.setAttribute('data-theme', saved);
      }
    })();

    async function requestWakeLock() {
      try {
        if ('wakeLock' in navigator) {
          wakeLock = await navigator.wakeLock.request('screen');
          wakeLock.addEventListener('release', () => { wakeLock = null; });
        }
      } catch (e) { }
    }
    requestWakeLock();
    document.addEventListener('visibilitychange', () => {
      pageVisible = !document.hidden;
      if (pageVisible) {
        requestWakeLock();
        if (titleFlashTimer) { clearInterval(titleFlashTimer); titleFlashTimer = null; }
        document.title = originalTitle;
        $('notify-banner').style.display = 'none';
        syncOnce();
      } else {
        stopPolling();
      }
    });

    // ===== 语音播放（按需合成 + 单实例播放） =====
    function ttsAutoOn() { return localStorage.getItem('tts_auto') === '1'; }
    const ttsUrlCache = {};          // text -> 已合成的音频 url，避免重复请求（server 也有文件缓存）
    let ttsPlayer = null;            // 全局单个 Audio 实例
    let ttsPlayingIdx = -1;
    function stopCurrentTTS() {
      if (ttsPlayer) { try { ttsPlayer.pause(); } catch (e) { } }
      document.querySelectorAll('.tts-btn.playing, .tts-btn.loading')
        .forEach(b => b.classList.remove('playing', 'loading'));
      ttsPlayingIdx = -1;
    }
    async function playMessageTTS(idx, btn) {
      const m = messages[idx];
      if (!m || m.role === 'user') return;
      // 再点正在播放的那条 = 停止
      if (ttsPlayingIdx === idx && ttsPlayer && !ttsPlayer.paused) { stopCurrentTTS(); return; }
      stopCurrentTTS();

      // 合成用文本优先取带停顿标记的 voice_text；情绪取该条 emotion
      const speakText = m.voice_text || m.text;
      const cacheKey = speakText + '|' + (m.emotion || '');
      let url = ttsUrlCache[cacheKey];
      if (!url) {
        if (btn) btn.classList.add('loading');
        try {
          const r = await fetch(withSid(API + '/api/tts'), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: speakText, emotion: m.emotion || '' })
          });
          const d = await r.json();
          url = (d && d.ok) ? d.audio : null;
          if (!url && d && d.error === 'tts_disabled' && window.showToast) showToast('语音未开启（config.tts.enabled）');
          else if (!url && window.showToast) showToast('语音合成失败');
        } catch (e) { url = null; }
        if (btn) btn.classList.remove('loading');
        if (!url) return;
        ttsUrlCache[cacheKey] = url;
      }
      ttsPlayer = new Audio(url);
      ttsPlayingIdx = idx;
      if (btn) btn.classList.add('playing');
      ttsPlayer.onended = stopCurrentTTS;
      ttsPlayer.onerror = stopCurrentTTS;
      ttsPlayer.play().catch(stopCurrentTTS);
    }
    $('chat').addEventListener('click', (e) => {
      const btn = e.target.closest('.tts-btn');
      if (!btn) return;
      const msgEl = btn.closest('.msg');
      const idx = msgEl ? parseInt(msgEl.dataset.msgIndex) : NaN;
      if (!isNaN(idx)) playMessageTTS(idx, btn);
    });
    // 语音「自动读」开关
    (function initTTSToggle() {
      const btn = $('btn-tts');
      if (!btn) return;
      const sync = () => btn.classList.toggle('active', ttsAutoOn());
      sync();
      btn.addEventListener('click', () => {
        localStorage.setItem('tts_auto', ttsAutoOn() ? '0' : '1');
        sync();
        if (window.showToast) showToast(ttsAutoOn() ? '已开启：每条自动读' : '已关闭自动读（仍可单点播放）');
      });
    })();
    // 「只读台词」开关（服务端配置 tts.skip_narration）
    (function initNarrationToggle() {
      const btn = $('btn-tts-narration');
      if (!btn) return;
      let on = false;
      const sync = () => btn.classList.toggle('active', on);
      fetch(API + '/api/config').then(r => r.json())
        .then(c => { on = !!(c && c.tts && c.tts.skip_narration); sync(); })
        .catch(() => { });
      btn.addEventListener('click', async () => {
        try {
          const r = await fetch(API + '/api/tts/option', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'skip_narration', value: !on })
          });
          const d = await r.json();
          if (d && d.ok) {
            on = !!d.value; sync();
            for (const k in ttsUrlCache) delete ttsUrlCache[k];  // 改了读法，清掉旧音频缓存
            stopCurrentTTS();
            if (window.showToast) showToast(on ? '只读台词：开（跳过旁白）' : '只读台词：关（整段都读）');
          } else if (window.showToast) showToast('设置失败');
        } catch (e) { if (window.showToast) showToast('设置失败'); }
      });
    })();

    (function () {
      let audioCtx = null;
      function initAudio() {
        if (audioCtx) return;
        try {
          audioCtx = new (window.AudioContext || window.webkitAudioContext)();
          const osc = audioCtx.createOscillator();
          const gain = audioCtx.createGain();
          gain.gain.value = 0.001;
          osc.connect(gain);
          gain.connect(audioCtx.destination);
          osc.start();
        } catch (e) { }
      }
      ['touchstart', 'click'].forEach(evt => {
        document.addEventListener(evt, initAudio, { once: false, passive: true });
      });
    })();

    function playNotifSound() {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.3);
      } catch (e) { }
    }

    function flashTitle(text) {
      if (titleFlashTimer) return;
      let on = true;
      titleFlashTimer = setInterval(() => {
        document.title = on ? text : originalTitle;
        on = !on;
      }, 1000);
    }

    let prevMsgCount = 0;
    let prevLastRole = '';

    function checkNewMessage() {
      if (messages.length <= prevMsgCount) return;
      const latest = messages[messages.length - 1];
      if (latest.role === 'assistant' && prevLastRole !== 'assistant') {
        playNotifSound();
        if (!pageVisible) flashTitle('💬 New message');

        const el = $('chat-scroll');
        const dist = el.scrollHeight - (el.scrollTop + el.clientHeight);
        // 如果新消息来时用户正停留在上方看历史，让右下角浮标变红警告
        if (dist > 350) {
          const jBtn = $('btn-jump-bottom');
          if (jBtn) {
            jBtn.classList.add('show', 'unread');
            $('jump-btn-text').textContent = '新消息 ↓';
          }
        }
      }
      prevMsgCount = messages.length;
      prevLastRole = latest.role;
    }

    // 实时滚动监听：控制右下角回到底部浮标的显隐
    $('chat-scroll').addEventListener('scroll', function () {
      const dist = this.scrollHeight - (this.scrollTop + this.clientHeight);
      const btn = $('btn-jump-bottom');
      if (!btn) return;

      if (dist > 350) {
        btn.classList.add('show');
      } else {
        // 一旦滚回了底部，自动解除未读红点状态
        btn.classList.remove('show', 'unread');
        $('jump-btn-text').textContent = '回到底部';
      }
    });

    // 删掉原有的 const _origRender = renderMessages; 包装段落，
    // 因为我们在 pollTick / fetchMessages 里拉到新数据本身就会调 renderMessages。

    let _fetchingTyping = false;
    async function fetchTypingStatus() {
      if (_fetchingTyping) return;
      _fetchingTyping = true;
      try {
        const r = await fetch(withSid(API + '/api/typing_status'));
        const data = await r.json();
        typingFromServer = data.typing;
        pendingFromServer = data.pending;
        // 输入中动画跟随 pending：从用户发出消息那一刻起就显示，直到真正有回复/中断/清空，
        // 不再依赖 claude_mode 是否额外调用了 /api/typing
        if (data.pending) {
          $('typing').classList.add('show');
        } else {
          $('typing').classList.remove('show');
        }
      } catch (e) { }
      finally { _fetchingTyping = false; }
    }

    // ====== 按需同步：只在初始化/切换会话/收发消息时拉取，等待回复期间短暂轮询 ======
    let pollTimer = null;
    async function pollTick() {
      await fetchMessages();
      await fetchTypingStatus();

      // 核心修复：锁定状态只认 pending（从用户发送到真正有回复/中断/清空之前一直为 true），
      // 不再依赖 is_typing —— 后者在 claude_mode 下有 120s 自动超时，会导致等待态提前消失。
      if (!pendingFromServer) {
        setGeneratingState(false);
      }
    }
    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(pollTick, 1500);
    }
    function stopPolling() {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }
    async function syncOnce() {
      await fetchMessages();
      await fetchTypingStatus();
      const last = messages[messages.length - 1];
      // 修复：如果服务端 pending 还没解除，或者最后一条消息依然是 user（说明 AI 还没回复），就保持轮询
      if (pendingFromServer || (last && last.role === 'user')) {
        startPolling();
      }
    }

    // ====== 图片上传 ======
    $('btn-image').addEventListener('click', () => $('file-upload').click());
    $('file-upload').addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        const img = new Image();
        img.onload = () => {
          const canvas = document.createElement('canvas');
          let w = img.width, h = img.height, max = 1024;
          if (w > max || h > max) {
            if (w > h) { h = Math.round(h * (max / w)); w = max; }
            else { w = Math.round(w * (max / h)); h = max; }
          }
          canvas.width = w; canvas.height = h;
          canvas.getContext('2d').drawImage(img, 0, 0, w, h);
          pendingImageBase64 = canvas.toDataURL('image/jpeg', 0.8);
          $('preview-img').src = pendingImageBase64;
          $('preview-box').style.display = 'block';
        };
        img.src = ev.target.result;
      };
      reader.readAsDataURL(file);
      e.target.value = '';
    });
    $('btn-rm-img').addEventListener('click', () => {
      pendingImageBase64 = null;
      $('preview-box').style.display = 'none';
      $('preview-img').src = '';
    });

    // ====== 模式切换 ======
    function updateModeIcon(mode) {
      const isApi = (mode || '').toLowerCase() === 'api';
      $('mode-icon-api').style.display = isApi ? '' : 'none';
      $('mode-icon-cli').style.display = isApi ? 'none' : '';
    }
    $('btn-mode').addEventListener('click', async () => {
      try {
        const r = await fetch(API + '/api/toggle_mode', { method: 'POST' });
        const data = await r.json();
        if (data.ok) {
          updateModeIcon(data.mode);
          showToast('已切换为 ' + data.mode.toUpperCase() + ' 模式');
        }
      } catch (e) { showToast('无法切换模式'); }
    });
    (async function initMode() {
      try {
        const r = await fetch(withSid(API + '/api/status'));
        const d = await r.json();
        if (d.mode) updateModeIcon(d.mode);
      } catch (e) { }
    })();

    syncOnce();

    // ====== 键盘收起时修正滚动位置 ======
    if (window.visualViewport) {
      let prevHeight = window.visualViewport.height;
      window.visualViewport.addEventListener('resize', () => {
        const newHeight = window.visualViewport.height;
        if (newHeight > prevHeight) {
          scrollToBottom(true);
        }
        prevHeight = newHeight;
      });
    }

