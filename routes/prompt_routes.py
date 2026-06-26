# -*- coding: utf-8 -*-
"""提示词 / 预设 / 会话 路由：增删改查、绑定、克隆、置顶。"""
import json
import shutil

import memory_store
from core.net import _safe_decode, _safe_name
from core.paths import SESSIONS_DIR, PROMPTS_DIR, PRESETS_DIR
from core.config import config, config_lock, save_config as _save_config
from session.session import sessions_map, get_session
from prompts.prompts import PROMPT_CATEGORIES, PRESET_CATEGORIES
from routes.registry import post, get


@post("/api/prompts/save")
def _prompts_save(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    cat = data.get("category")
    name = _safe_name(data.get("name"))
    display_name = data.get("display_name", "") or name
    content = data.get("content", "")
    if cat in PROMPT_CATEGORIES and name:
        file_data = {"name": display_name, "content": content}
        fpath = PROMPTS_DIR / cat / f"{name}.json"
        # 头像：角色与用户都支持。未传 avatar（旧前端/克隆）时保留原图，避免被清空。
        if cat in ("character", "user"):
            if data.get("avatar") is not None:
                file_data["avatar"] = data.get("avatar", "")
            elif fpath.exists():
                try:
                    old_av = json.loads(fpath.read_text(encoding="utf-8")).get(
                        "avatar"
                    )
                    if old_av:
                        file_data["avatar"] = old_av
                except Exception:
                    pass
        if cat == "character":
            if isinstance(data.get("voice"), dict):
                file_data["voice"] = data.get("voice")
            elif fpath.exists():
                # 未传 voice（如克隆/旧前端）时保留原有音色，避免被覆盖丢失
                try:
                    old = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(old.get("voice"), dict):
                        file_data["voice"] = old["voice"]
                except Exception:
                    pass
        fpath.write_text(
            json.dumps(file_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        h._json({"ok": True})
    else:
        h._json({"ok": False, "error": "Invalid params"})


@post("/api/prompts/delete")
def _prompts_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    cat = data.get("category")
    name = _safe_name(data.get("name"))
    if name == "default":
        h._json({"ok": False, "error": "默认项不可删除"})
    elif cat in PROMPT_CATEGORIES and name:
        fpath = PROMPTS_DIR / cat / f"{name}.json"
        if fpath.exists():
            fpath.unlink()
        h._json({"ok": True})
    else:
        h._json({"ok": False, "error": "Invalid params"})


@post("/api/prompts/use")
def _prompts_use(h, query, session, session_id):
    # 选择对哪个会话生效（不传 session_id 时即当前 query 里的 session，默认 "default"）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    with session.lock:
        for k, v in data.items():
            # 修复：改为 SESSION_BINDING_KEYS，使得 preset, world, user 均可直接保存
            if k in SESSION_BINDING_KEYS:
                session.active_prompts[k] = _safe_name(v) or "default"
    session.save_active_prompts()
    h._json({"ok": True})


@post("/api/prompts/set_default_user")
def _prompts_set_default_user(h, query, session, session_id):
    # 设置全局默认用户角色（“我”页主名片、新会话默认带入）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    key = _safe_name(data.get("key")) or "default"
    with config_lock:
        config["default_user"] = key
    _save_config()
    h._json({"ok": True, "default_user": key})


@post("/api/sessions/create")
def _sessions_create(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    character = _safe_name(data.get("character")) or "default"
    char_label = character
    cfile = PROMPTS_DIR / "character" / f"{character}.json"
    if cfile.exists():
        try:
            char_label = json.loads(cfile.read_text(encoding="utf-8")).get(
                "name", character
            )
        except Exception:
            pass
    char_label = _safe_name(char_label) or character
    existing_ids = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
    n = 1
    while f"{char_label}_{n}" in existing_ids:
        n += 1
    new_id = f"{char_label}_{n}"
    new_session = get_session(new_id)
    new_session.active_prompts["character"] = character
    new_session.save_active_prompts()
    h._json({"ok": True, "session_id": new_id})


@post("/api/sessions/delete")
def _sessions_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    del_id = data.get("session_id")
    existing = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
    if not del_id or del_id not in existing:
        h._json({"ok": False, "error": "session not found"})
    elif len(existing) <= 1:
        h._json({"ok": False, "error": "至少保留一个会话"})
    else:
        sessions_map.pop(del_id, None)
        shutil.rmtree(SESSIONS_DIR / del_id, ignore_errors=True)
        memory_store.delete_scope(
            f"sess:{del_id}", del_id
        )  # 连带清掉该会话记忆
        h._json({"ok": True})


@post("/api/sessions/rename")
def _sessions_rename(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    old_id = data.get("session_id")
    new_name = data.get("name", "").strip()
    if not old_id or not new_name:
        h._json({"ok": False, "error": "缺少参数"})
    else:
        new_id = _safe_name(new_name) or old_id
        old_dir = SESSIONS_DIR / old_id
        new_dir = SESSIONS_DIR / new_id
        if not old_dir.exists():
            h._json({"ok": False, "error": "会话不存在"})
        elif new_dir.exists() and new_id != old_id:
            h._json({"ok": False, "error": "名称已被占用"})
        elif new_id == old_id:
            h._json({"ok": True, "session_id": old_id})
        else:
            s = sessions_map.pop(old_id, None)
            old_dir.rename(new_dir)
            if s:
                s.session_id = new_id
                s.dir = new_dir
                s.messages_file = new_dir / "messages.json"
                s.memory_file = new_dir / "memory.json"
                s.prompts_file = new_dir / "active_prompts.json"
                sessions_map[new_id] = s
            # 会话改名 → session_id 变了，记忆作用域随之迁移，避免记忆丢失
            memory_store.migrate_scope(
                f"sess:{old_id}", f"sess:{new_id}", old_id, new_id
            )
            h._json({"ok": True, "session_id": new_id})


@post("/api/sessions/clone")
def _sessions_clone(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    src_id = data.get("session_id")
    src_dir = SESSIONS_DIR / src_id if src_id else None
    if not src_id or not src_dir or not src_dir.exists():
        h._json({"ok": False, "error": "会话不存在"})
    else:
        existing_ids = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
        n = 1
        while f"{src_id}_copy{n}" in existing_ids:
            n += 1
        new_id = f"{src_id}_copy{n}"
        shutil.copytree(src_dir, SESSIONS_DIR / new_id)
        # 克隆=独立快照：把来源会话的记忆 fork 一份给克隆体，之后互不影响
        memory_store.fork_scope(
            f"sess:{src_id}", f"sess:{new_id}", src_id, new_id
        )
        h._json({"ok": True, "session_id": new_id})


@post("/api/sessions/pin")
def _sessions_pin(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    sid = data.get("session_id")
    s_dir = SESSIONS_DIR / sid if sid else None
    if not sid or not s_dir or not s_dir.exists():
        h._json({"ok": False, "error": "会话不存在"})
    else:
        meta_file = s_dir / "meta.json"
        meta = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        meta["pinned"] = not meta.get("pinned", False)
        meta_file.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        h._json({"ok": True, "pinned": meta["pinned"]})


@post("/api/presets/save")
def _presets_save(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    name = _safe_name(data.get("name"))
    if not name:
        h._json({"ok": False, "error": "Invalid params"})
    else:
        preset = {
            k: _safe_name(data.get(k)) or "default" for k in PRESET_CATEGORIES
        }
        (PRESETS_DIR / f"{name}.json").write_text(
            json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        h._json({"ok": True})


@post("/api/presets/delete")
def _presets_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    name = _safe_name(data.get("name"))
    if name == "default":
        h._json({"ok": False, "error": "默认预设不可删除"})
    elif name:
        fpath = PRESETS_DIR / f"{name}.json"
        if fpath.exists():
            fpath.unlink()
        h._json({"ok": True})
    else:
        h._json({"ok": False, "error": "Invalid params"})


@post("/api/presets/apply")
def _presets_apply(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    name = _safe_name(data.get("name"))
    fpath = PRESETS_DIR / f"{name}.json"
    if name and fpath.exists():
        preset = json.loads(fpath.read_text(encoding="utf-8"))
        for k in PRESET_CATEGORIES:
            session.active_prompts[k] = preset.get(k, "default")
        session.save_active_prompts()
        h._json({"ok": True})
    else:
        h._json({"ok": False, "error": "preset not found"})


@get("/api/sessions/list")
def _get_sessions_list(h, query, session, session_id):
    s_list = []
    char_cache = {}
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        s = get_session(d.name)
        char_name = s.active_prompts.get("character", "default")

        if char_name not in char_cache:
            cfile = PROMPTS_DIR / "character" / f"{_safe_name(char_name)}.json"
            if cfile.exists():
                try:
                    cdata = json.loads(cfile.read_text(encoding="utf-8"))
                except Exception:
                    cdata = {}
            else:
                cdata = {}
            char_cache[char_name] = {
                "name": cdata.get("name", char_name),
                "avatar": cdata.get("avatar", ""),
            }
        meta = char_cache[char_name]

        preview = ""
        if s.messages:
            last = s.messages[-1]
            preview = (
                "[图片]" if last.get("image") else last.get("text", "")[:30]
            )

        updated_at = (
            s.messages_file.stat().st_mtime if s.messages_file.exists() else 0
        )

        pinned = False
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                pinned = json.loads(meta_file.read_text(encoding="utf-8")).get(
                    "pinned", False
                )
            except Exception:
                pass

        s_list.append(
            {
                "id": d.name,
                "character": char_name,
                "character_name": meta["name"],
                "avatar": meta["avatar"],
                "preview": preview,
                "updated_at": updated_at,
                "pinned": pinned,
            }
        )
    s_list.sort(key=lambda x: (-x["pinned"], -x["updated_at"]))
    h._json({"sessions": s_list})
    return


@get("/api/presets/list")
def _get_presets_list(h, query, session, session_id):
    names = [f.stem for f in PRESETS_DIR.glob("*.json")]
    h._json({"presets": names})
    return


@get("/api/presets/get")
def _get_presets_get(h, query, session, session_id):
    name = _safe_name(query.get("name", [""])[0])
    fpath = PRESETS_DIR / f"{name}.json"
    if name and fpath.exists():
        h._json(
            {"ok": True, "data": json.loads(fpath.read_text(encoding="utf-8"))}
        )
    else:
        h._json({"ok": False})
    return


@get("/api/prompts/list")
def _get_prompts_list(h, query, session, session_id):
    with session.lock:
        session.load_active_prompts()  # 修复：每次拉取列表前，强制同步一次本地文件

    tree = {}
    for cat in PROMPT_CATEGORIES:
        cat_dir = PROMPTS_DIR / cat
        tree[cat] = [f.stem for f in cat_dir.glob("*.json")]

    # 补回被截断的角色解析与返回响应的逻辑
    char_list = []
    for f in (PROMPTS_DIR / "character").glob("*.json"):
        try:
            cdata = json.loads(f.read_text(encoding="utf-8"))
            char_list.append(
                {
                    "key": f.stem,
                    "name": cdata.get("name", f.stem),
                    "avatar": cdata.get("avatar", ""),
                }
            )
        except Exception:
            char_list.append({"key": f.stem, "name": f.stem, "avatar": ""})

    # 用户分身（“我”页主名片 + 平行分身）：解析 user 类别下的完整资料
    user_list = []
    for f in sorted((PROMPTS_DIR / "user").glob("*.json")):
        try:
            udata = json.loads(f.read_text(encoding="utf-8"))
            user_list.append(
                {
                    "key": f.stem,
                    "name": udata.get("name", f.stem),
                    "avatar": udata.get("avatar", ""),
                    "content": udata.get("content", ""),
                }
            )
        except Exception:
            user_list.append(
                {"key": f.stem, "name": f.stem, "avatar": "", "content": ""}
            )
    # 默认用户角色：优先 config.default_user，其次落到 'default'/首个
    default_user = config.get("default_user") or "default"
    user_keys = {u["key"] for u in user_list}
    if default_user not in user_keys:
        default_user = (
            "default"
            if "default" in user_keys
            else (user_list[0]["key"] if user_list else "default")
        )
    # 主身份排最前，其余按 key
    user_list.sort(key=lambda u: (u["key"] != default_user, u["key"]))

    h._json(
        {
            "tree": tree,
            "active": session.active_prompts,
            "characters": char_list,
            "users": user_list,
            "default_user": default_user,
        }
    )
    return


@get("/api/prompts/get")
def _get_prompts_get(h, query, session, session_id):
    cat = query.get("category", [""])[0]
    name = _safe_name(query.get("name", [""])[0])
    if cat not in PROMPT_CATEGORIES or not name:
        h._json({"ok": False})
        return
    fpath = PROMPTS_DIR / cat / f"{name}.json"
    if fpath.exists():
        data = json.loads(fpath.read_text(encoding="utf-8"))
        h._json({"ok": True, "data": data})
    else:
        h._json({"ok": False})
    return

