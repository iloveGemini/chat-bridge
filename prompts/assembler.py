# -*- coding: utf-8 -*-
"""PromptAssembler —— 按固定槽位骨架组装提示词（messages 数组）。

骨架（空槽自动跳过）：
  System 头(单个 system 消息):  Main → World_Env → Role → User → Memory_before
  History:                      真实多轮消息数组
  Tail(贴最后一条 user 正文, 包 <system_guidance>):
                                Status → Memory_after → Tone_Style → Post → Reasoning_Scaffold → Output_Format
  末轮:                         Last_User_Input（tail 贴在它身上）

第一刀目标：把现有 RP 拼装（build_header_prompt / build_tail_anchor）平移进来，**输出逐字节等价**。
其中 World_Env / Memory_before / Reasoning_Scaffold / Output_Format 为预留空槽(no-op)；
Status 槽现状仅由 scene 填充（未来按 category="status" 的状态项统一回填，见 ARCHITECTURE.md §2）。
"""
from core.config import config, load_config as _load_config
from prompts.prompts import _read_prompt_content, _resolve_preset, _apply_macros, PRESET_CATEGORIES
from chat.scene import build_scene_block

SYSTEM_SLOTS = ["main", "world_env", "role", "user", "memory_before"]
TAIL_SLOTS = ["status", "memory_after", "tone_style", "post", "reasoning_scaffold", "output_format"]


class PromptAssembler:
    def __init__(self, session):
        self.session = session
        self.active = session.active_prompts
        # 预设展开（与 build_header/tail 完全一致的条件）；预设是纯文本槽的引用包。
        if "preset" in self.active and self.active.get("preset") not in ("", "default"):
            refs = _resolve_preset(self.active.get("preset"))
        else:
            refs = {c: self.active.get(c, "default") for c in PRESET_CATEGORIES}
        self.main_name = refs["main"]
        self.style_name = refs["style"]
        self.post_name = refs["post"]
        self.world_name = refs["world"]
        self.reasoning_name = refs["reasoning"]

    # ---------------- System 头 槽位 ----------------
    def slot_main(self):
        _load_config()
        global_sys = config.get("api", {}).get("system_prompt", "").strip()
        main_content = _read_prompt_content("main", self.main_name)
        if (not main_content or self.main_name == "default") and global_sys:
            main_content = global_sys
        return f"<role_definition>\n{main_content}\n</role_definition>" if main_content else ""

    def slot_world_env(self):
        w = _read_prompt_content("world", self.world_name)
        return f"<world_setting>\n{w}\n</world_setting>" if w else ""

    def slot_role(self):
        c = _read_prompt_content("character", self.active.get("character", "default"))
        return f"<persona>\n{c}\n</persona>" if c else ""

    def slot_user(self):
        u = _read_prompt_content("user", self.active.get("user", "default"))
        return f"<user_profile>\n{u}\n</user_profile>" if u else ""

    def slot_memory_before(self, memory_before=""):
        mb = (memory_before or "").strip()
        return f"<persistent_memory>\n{mb}\n</persistent_memory>" if mb else ""

    # ---------------- Tail 槽位 ----------------
    def slot_status(self):
        return build_scene_block(self.session)  # 现状仅 scene；未来 status 桶统一回填

    def slot_memory_after(self, memory_str):
        return f"<recalled_memory>\n{memory_str.strip()}\n</recalled_memory>" if memory_str.strip() else ""

    def slot_tone_style(self):
        s = _read_prompt_content("style", self.style_name)
        return f"<dialogue_style>\n{s}\n</dialogue_style>" if s else ""

    def slot_post(self):
        p = _read_prompt_content("post", self.post_name)
        return f"<output_rules>\n{p}\n</output_rules>" if p else ""

    def slot_reasoning_scaffold(self):
        r = _read_prompt_content("reasoning", self.reasoning_name)
        return f"<reasoning_guidance>\n{r}\n</reasoning_guidance>" if r else ""

    def slot_output_format(self):
        from prompts.output_formats import build_output_format_block
        block = build_output_format_block(self.session, self.active.get("preset"))
        return f"<output_format>\n{block}\n</output_format>" if block else ""

    # ---------------- 组装 ----------------
    def build_system_head(self, char_name, user_name, memory_before=""):
        parts = [self.slot_main(), self.slot_world_env(), self.slot_role(),
                 self.slot_user(), self.slot_memory_before(memory_before)]
        head = "\n\n".join(p for p in parts if p)
        return _apply_macros(head, char_name, user_name)

    def build_tail(self, char_name, user_name, memory_str=""):
        parts = [self.slot_status(), self.slot_memory_after(memory_str), self.slot_tone_style(),
                 self.slot_post(), self.slot_reasoning_scaffold(), self.slot_output_format()]
        compiled = "\n\n".join(p for p in parts if p)
        tail = f"\n\n\n<system_guidance>\n{compiled}\n</system_guidance>" if compiled else ""
        return _apply_macros(tail, char_name, user_name)
