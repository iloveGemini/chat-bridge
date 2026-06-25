import { router } from '../router.js';
import { api } from '../api.js';
import { showToast, escHtml, ICONS } from '../utils.js';

class PluginManagerView {
  constructor() {
    this.sessionId = null;
  }

  async open(sid) {
    this.sessionId = sid;
    const container = document.getElementById('plugin-manager-content');
    container.innerHTML = `
      <div id="plugin-list"></div>
    `;
    router.pushView('plugin-manager-view');

    // 先用服务器端的会话授权状态校准本地开关（服务器为准，跨设备一致）
    try {
      const res = await api.toolsGet(sid);
      const cfg = res && res.tools ? res.tools : null;
      if (cfg) {
        ['outreach', 'web'].forEach(t => {
          if (t in cfg) localStorage.setItem(`tool_${t}_${sid}`, cfg[t] ? '1' : '0');
        });
      }
    } catch (e) { /* 拉取失败就用本地 localStorage 兜底 */ }

    this.render();
  }

  render() {
    const sid = this.sessionId;
    const list = document.getElementById('plugin-list');
    if (!list) return;

    const outreachEn = localStorage.getItem(`tool_outreach_${sid}`) !== '0';
    const webEn = localStorage.getItem(`tool_web_${sid}`) === '1';

    list.innerHTML = `
      <!-- ================= Tool 1: 主动联系 (Proactive Outreach) ================= -->
      <div class="ios-group" id="plugin-card-outreach">
        <div class="ios-item" style="border-bottom:${outreachEn ? '0.5px solid var(--border-color)' : 'none'}">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.voice}</span> 主动联系 (Outreach)</span>
          <label class="switch">
            <input type="checkbox" class="plugin-switch" data-tool="outreach" ${outreachEn ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        
        <!-- ⚠️ 补上了 overflow:hidden，确保缩回0px时内部按钮瞬间被剪切，不挂在外面漏馅 -->
        <div class="plugin-config-panel ${outreachEn ? 'show' : ''}" id="panel-outreach" style="max-height:${outreachEn ? '1500px' : '0px'}; overflow:hidden; transition:max-height 0.35s cubic-bezier(0.2, 0.8, 0.2, 1);">
          <div style="padding: 10px 16px 4px; font-size: 12px; color: var(--text-secondary); line-height:1.5;">
            角色会在记忆本上排定找你的计划。到点由后台静默推送到手机。
          </div>

          <!-- 1. 已有任务列表区 -->
          <div id="outreach-jobs-container"></div>

          <!-- 2. 行内创建表单 -->
          <div class="ios-sec-title" style="margin: 20px 16px 6px;">+ 新建联系计划</div>
          
          <div class="ios-item" style="padding-top:10px;">
            <span class="label" style="font-size:14px;">触发时机类型</span>
            <select class="ios-select" id="ot-kind" style="font-size:14px; width:auto; font-weight:500;">
              <option value="once">指定时刻一次 (once)</option>
              <option value="daily" selected>每天定点问候 (daily)</option>
              <option value="interval">间隔固定周期 (interval)</option>
              <option value="idle">沉寂空闲时 (idle)</option>
            </select>
          </div>

          <div class="ios-item">
            <span class="label" style="font-size:14px;">文风生成模式</span>
            <select class="ios-select" id="ot-mode" style="font-size:14px; width:auto; font-weight:500;">
              <option value="wake" selected>AI 现场拟写正文 (wake)</option>
              <option value="push">推送死板文案 (push)</option>
            </select>
          </div>

          <div class="ios-item" style="flex-direction:column; align-items:stretch; gap:6px; padding-top:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span class="label" style="font-size:14px;">时间规则设定</span>
              <input type="text" class="ios-input" id="ot-when" placeholder="如：09:30 / +30m / 180" style="width:50%; font-size:14px; font-weight:500;">
            </div>
            <div style="font-size:11px; color:var(--text-faint); text-align:right;">
              说明：once填+30m | daily填09:30 | interval/idle填分钟数
            </div>
          </div>

          <div class="ios-item" style="flex-direction:column; align-items:stretch; gap:8px; padding-bottom:16px; border-bottom:none;">
            <span class="label" style="font-size:14px;">交给 AI 的心意 / 拟写事由</span>
            <textarea id="ot-text" placeholder="wake填事由(例：问问他今天工作累不累)；push填死文案" style="width:100%; background:var(--input-bg); border:1px solid var(--border-color); border-radius:10px; padding:10px 12px; font-size:14px; color:var(--text); font-family:inherit; resize:none; outline:none; height:54px; box-sizing:border-box;"></textarea>
            
            <button class="btn-primary" id="ot-submit-btn" style="margin-top:8px; height:42px; font-size:15px; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.15);">添加至角色日程本</button>
          </div>
        </div>
      </div>

      <!-- ================= Tool 2: 联网检索 (Web Search) ================= -->
      <div class="ios-group" id="plugin-card-web">
        <div class="ios-item">
          <span class="label"><span style="color:var(--text-secondary);display:flex;">${ICONS.search}</span> 联网检索 (Web Search)</span>
          <label class="switch">
            <input type="checkbox" class="plugin-switch" data-tool="web" ${webEn ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
      </div>
    `;

    this.bindEvents();
    if (outreachEn) {
      this.fetchAndRenderJobs(sid);
    }
  }

  async fetchAndRenderJobs(sid) {
    const container = document.getElementById('outreach-jobs-container');
    if (!container) return;

    const KIND_MAP = { once: '一次性', daily: '每日定点', interval: '间隔循环', idle: '沉寂空闲' };
    const MODE_MAP = { wake: 'AI 现场组织', push: '推固定文案' };
    const fmtNext = ts => ts ? new Date(ts * 1000).toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '未算期';

    try {
      const res = await api.outreachList(sid);
      const jobs = res.jobs || [];

      if (jobs.length === 0) {
        container.innerHTML = '';
        return;
      }

      container.innerHTML = `<div class="ios-sec-title" style="margin: 16px 16px 6px;">当前生效日程 (${jobs.length})</div>` + 
      jobs.map(j => `
        <div class="outreach-job-cell" data-id="${j.id}" style="margin: 0 16px 8px; padding: 12px 14px; background: var(--input-bg); border: 0.5px solid var(--border-color); border-radius: 12px; transition: opacity 0.2s; ${j.enabled ? '' : 'opacity: 0.45;'}">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size:10px; font-weight:600; padding:2px 6px; border-radius:4px; background:var(--surface); border:0.5px solid var(--border-color); color:var(--text-secondary);">
                ${KIND_MAP[j.kind] || j.kind}
              </span>
              <span style="font-size:14px; font-weight:600; color:var(--text);">
                ${MODE_MAP[j.mode] || j.mode}
              </span>
            </div>
            
            <div style="display: flex; align-items: center; gap: 10px;">
              <label class="switch" style="transform: scale(0.75); margin: 0; transform-origin: right center;">
                <input type="checkbox" class="job-enable-chk" ${j.enabled ? 'checked' : ''}>
                <span class="slider"></span>
              </label>
              <span class="job-del-btn" style="color: var(--text-faint); font-size: 16px; font-weight: bold; cursor: pointer; padding: 0 4px;">×</span>
            </div>
          </div>

          <div style="font-size: 12px; color: var(--active-color); font-weight: 500; margin-bottom: 4px;">
            ⏰ 触发设定：${escHtml(j.when_spec)} <span style="font-size:11px; color:var(--text-secondary); font-weight:normal;">(${j.kind === 'idle' ? `空闲${Math.round(j.when_spec/60)}分后` : fmtNext(j.next_run)})</span>
          </div>

          ${j.intention || j.content ? `
            <div style="font-size: 12px; color: var(--text-secondary); line-height: 1.4; padding-top: 4px; border-top: 0.5px dashed var(--border-color);">
              「${escHtml(j.intention || j.content)}」
            </div>
          ` : ''}
        </div>
      `).join('');

      container.querySelectorAll('.outreach-job-cell').forEach(cell => {
        const jid = parseInt(cell.dataset.id);
        cell.querySelector('.job-enable-chk').onchange = async (e) => {
          await api.outreachToggle(jid, e.target.checked, sid);
          this.fetchAndRenderJobs(sid);
        };
        cell.querySelector('.job-del-btn').onclick = async () => {
          await api.outreachDelete(jid, sid);
          showToast('任务已注销');
          this.fetchAndRenderJobs(sid);
        };
      });

    } catch (e) {
      container.innerHTML = `<div style="padding:0 16px; color:var(--status-off); font-size:12px;">读取日程本失败</div>`;
    }
  }

  bindEvents() {
    const sid = this.sessionId;
    const list = document.getElementById('plugin-list');
    if (!list) return;

    // 核心修复区：JS 亲自下场接管 inline 样式的 maxHeight 降维打击
    list.querySelectorAll('.plugin-switch').forEach(sw => {
      sw.onchange = (e) => {
        const tool = sw.dataset.tool;
        const checked = e.target.checked;
        localStorage.setItem(`tool_${tool}_${sid}`, checked ? '1' : '0');
        // 回写服务器：该会话的工具授权即时生效（失败则回滚本地开关）
        api.toolsSet({ [tool]: checked }, sid).catch(() => {
          localStorage.setItem(`tool_${tool}_${sid}`, checked ? '0' : '1');
          e.target.checked = !checked;
          showToast('授权同步失败，请重试');
        });

        const group = sw.closest('.ios-group');
        const item = group.querySelector('.ios-item');
        const panel = group.querySelector('.plugin-config-panel');

        if (panel) {
          if (checked) {
            panel.classList.add('show');
            panel.style.maxHeight = '1500px'; // 👈 开启：命令行内高度撑开
            item.style.borderBottom = '0.5px solid var(--border-color)';
            if (tool === 'outreach') this.fetchAndRenderJobs(sid);
          } else {
            panel.classList.remove('show');
            panel.style.maxHeight = '0px';    // 👈 关闭：命令行内高度归零
            item.style.borderBottom = 'none';
          }
        }
      };
    });

    const submitBtn = list.querySelector('#ot-submit-btn');
    if (submitBtn) {
      submitBtn.onclick = async () => {
        const kind = list.querySelector('#ot-kind').value;
        const mode = list.querySelector('#ot-mode').value;
        const when = list.querySelector('#ot-when').value.trim();
        const text = list.querySelector('#ot-text').value.trim();

        if (!when) { showToast('请填写触发时间规则'); return; }

        submitBtn.disabled = true;
        submitBtn.textContent = '日程本刻录中...';

        try {
          const res = await api.outreachAdd({
            kind,
            mode,
            when,
            intention: mode === 'wake' ? text : '',
            content: mode === 'push' ? text : ''
          }, sid);

          if (res.ok) {
            showToast('已刻录进角色日程本！');
            list.querySelector('#ot-when').value = '';
            list.querySelector('#ot-text').value = '';
            this.fetchAndRenderJobs(sid);
          } else {
            showToast(res.error || '写入失败');
          }
        } catch (e) {
          showToast('网络开小差了');
        } finally {
          submitBtn.disabled = false;
          submitBtn.textContent = '添加至角色日程本';
        }
      };
    }
  }
}

export const pluginManagerView = new PluginManagerView();
