    const API = location.origin;

    // ===== 局域网访问口令：全局 fetch 注入 token，401 弹登录浮层 =====
    const _origFetch = window.fetch.bind(window);
    function _authUrl(input) { return (typeof input === 'string') ? input : (input && input.url) || ''; }
    window.fetch = async function (input, init) {
      init = init || {};
      const url = _authUrl(input);
      const isApi = url.indexOf('/api/') !== -1 && url.indexOf('/api/login') === -1;
      if (isApi) {
        const tok = localStorage.getItem('auth_token');
        if (tok) init.headers = Object.assign({}, init.headers, { 'X-Auth-Token': tok });
      }
      const resp = await _origFetch(input, init);
      if (isApi && resp.status === 401) { showLoginOverlay(); }
      return resp;
    };
    function showLoginOverlay() {
      const ov = document.getElementById('login-overlay');
      if (ov) ov.style.display = 'flex';
    }
    function hideLoginOverlay() {
      const ov = document.getElementById('login-overlay');
      if (ov) ov.style.display = 'none';
    }
    let _loggingIn = false;
    async function doLogin() {
      if (_loggingIn) return;                       // 防移动端双触发（Enter + 点按钮）卡死
      const pw = document.getElementById('login-pw').value;
      const err = document.getElementById('login-err');
      const btn = document.getElementById('login-btn');
      err.textContent = '';
      _loggingIn = true;
      if (btn) { btn.disabled = true; btn.textContent = '登录中…'; }
      try {
        const r = await _origFetch(API + '/api/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pw })
        });
        const d = await r.json();
        if (d.ok) {
          localStorage.setItem('auth_token', d.token || '');
          // 整页重载以带 token 重新初始化；replace 比 reload 在移动端更稳定，不进 bfcache
          window.location.replace(window.location.pathname + window.location.search);
          return;                                   // 跳转中，保持按钮禁用，避免二次提交
        }
        err.textContent = '口令错误';
      } catch (e) {
        err.textContent = '连接失败';
      }
      _loggingIn = false;
      if (btn) { btn.disabled = false; btn.textContent = '进入'; }
    }

