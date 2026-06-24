// 对话执行预设管理（二级页，ios 风格）：列表 + 新建 + 编辑 + 删除。
// 编辑复用 modals.openPresetEditor（其推入 generic-editor-view，z-index 已按栈深叠加）。
import { api } from '../api.js';
import { router } from '../router.js';
import { escHtml, showToast, ICONS } from '../utils.js';

class PresetsView {
  constructor() {
    this.presets = [];
    this.tree = {};
  }

  async open() {
    const c = document.getElementById('presets-content');
    if (c) c.innerHTML = '<div style="text-align:center;padding:50px;color:var(--text-secondary);">加载预设...</div>';
    router.pushView('presets-view');
    await this.reload();
  }

  async reload() {
    try {
      const [p, prompts] = await Promise.all([api.fetchPresets(), api.fetchPrompts()]);
      this.presets = p.presets || [];
      this.tree = prompts.tree || {};
    } catch (e) { this.presets = []; }
    this.render();
  }

  render() {
    const c = document.getElementById('presets-content');
    if (!c) return;
    let html = `
      <div style="padding:14px 16px 4px;font-size:12px;color:var(--text-secondary);line-height:1.6;">
        预设把「主提示词 / 文风 / 后续指令」打包成一套，绑定到会话即整套生效。</div>
      <div class="list-item" id="ps-new" style="background:var(--surface);cursor:pointer;">
        <div class="avatar" style="background:var(--active-color);color:#fff;">${ICONS.plus}</div>
        <div class="info"><div class="name" style="font-weight:bold;color:var(--active-color);">新建预设</div></div>
      </div>
      <div class="contact-group-title" style="margin-top:12px;">全部预设 (${this.presets.length})</div>`;

    this.presets.forEach(name => {
      const isDefault = name === 'default';
      html += `
        <div class="list-item ps-row" data-name="${escHtml(name)}" style="background:var(--surface);">
          <div class="avatar" style="background:var(--surface);color:var(--text-secondary);border:0.5px solid var(--border-color);">${ICONS.branch}</div>
          <div class="info">
            <div class="name">${escHtml(name)}${isDefault ? '<span style="font-size:11px;color:var(--text-secondary);margin-left:6px;">默认</span>' : ''}</div>
          </div>
          <span style="color:var(--active-color);font-size:14px;">编辑 〉</span>
        </div>`;
    });
    c.innerHTML = html;

    c.querySelector('#ps-new').onclick = () => this.edit(null);
    c.querySelectorAll('.ps-row').forEach(el => el.onclick = () => this.edit(el.dataset.name));
  }

  edit(name) {
    import('../modals.js').then(m => m.openPresetEditor(name, this.tree, () => this.reload()));
  }
}

export const presetsView = new PresetsView();
