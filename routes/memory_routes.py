# -*- coding: utf-8 -*-
"""记忆 / 世界书(worldbooks) / 世界书条目(lore) 路由：增删改查 + 召回 + 总结。"""
import json
import threading

import memory_store
from core.net import _safe_decode
from memory.memory import (
    embed_query, _lore_embedding, build_injected_memory, summarize_session, _session_scope,
)
from routes.registry import post, get


@post("/api/memory/summarize")
def _memory_summarize(h, query, session, session_id):
    threading.Thread(
        target=summarize_session, args=(session,), kwargs={"full": True}
    ).start()
    h._json({"ok": True})


@post("/api/memory/edit")
def _memory_edit(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    table, rid = data.get("table"), data.get("id")
    ok = False
    if table == "events":
        emb = embed_query(data["summary"]) if data.get("summary") else None
        ok = memory_store.update_event(
            rid,
            summary=data.get("summary"),
            type=data.get("type"),
            weight=data.get("weight"),
            importance=data.get("importance"),
            embedding=emb,
        )
    elif table == "facts":
        ok = memory_store.update_fact(
            rid,
            subject=data.get("subject"),
            predicate=data.get("predicate"),
            obj=data.get("object"),
            is_state=data.get("is_state"),
        )
    elif table == "summaries" and data.get("key"):
        memory_store.upsert_summary(data["key"], data.get("text", ""))
        ok = True
    elif table == "memories":
        # 简单支持 memories 的更新（如果后续有 update_memory 函数）
        pass
    h._json({"ok": ok})


@post("/api/memory/forget")
def _memory_forget(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    table, rid = data.get("table"), data.get("id")
    if table not in ("events", "chunks", "facts", "summaries", "memories"):
        h._json({"ok": False, "error": "invalid table"})
    else:
        h._json({"ok": memory_store.forget(table, rid)})


@post("/api/lore")
def _lore_create(h, query, session, session_id):
    # 新建世界书条目（挂到指定世界书 book_id 下）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    book_id = data.get("book_id")
    if book_id is None:
        h._json({"ok": False, "error": "book_id 必填"})
    elif not title or not content:
        h._json({"ok": False, "error": "title/content 必填"})
    else:
        rid = memory_store.add_lore(
            memory_store.worldbook_scope(book_id),
            title,
            content,
            keys=data.get("keys") or [],
            priority=data.get("priority", 0),
            always_on=bool(data.get("always_on")),
            position=(data.get("position") or "after"),
            embedding=_lore_embedding(title, content),
        )
        h._json({"ok": True, "id": rid})


@post("/api/worldbooks/create")
def _worldbooks_create(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    name = (data.get("name") or "").strip() or "未命名世界书"
    bind_type = data.get("bind_type", "none")
    bind_target = data.get("bind_target", "") or ""
    bid = memory_store.create_worldbook(name, bind_type, bind_target)
    h._json({"ok": True, "id": bid})


@post("/api/worldbooks/update")
def _worldbooks_update(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    bid = data.get("id")
    if bid is None:
        h._json({"ok": False, "error": "id 必填"})
    else:
        ok = memory_store.update_worldbook(
            bid,
            name=data.get("name"),
            bind_type=data.get("bind_type"),
            bind_target=data.get("bind_target"),
        )
        h._json({"ok": ok})


@post("/api/worldbooks/delete")
def _worldbooks_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    bid = data.get("id")
    h._json(
        {"ok": memory_store.delete_worldbook(bid) if bid is not None else False}
    )


@post("/api/worldbooks/session/set")
def _worldbooks_session_set(h, query, session, session_id):
    # 设置本会话手动挂载的世界书 id 列表（角色/用户绑定的不在此列，会自动并入）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    ids = data.get("ids")
    if not isinstance(ids, list):
        h._json({"ok": False, "error": "ids 须为数组"})
    else:
        with session.lock:
            session.active_worldbooks = [int(i) for i in ids]
        session.save_worldbooks()
        h._json({"ok": True})


@post("/api/lore/update")
def _lore_update(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    rid = data.get("id")
    if not rid:
        h._json({"ok": False, "error": "id 必填"})
    else:
        emb = (
            _lore_embedding(data.get("title"), data.get("content"))
            if (
                data.get("title") is not None or data.get("content") is not None
            )
            else None
        )
        ok = memory_store.update_lore(
            rid,
            title=data.get("title"),
            content=data.get("content"),
            keys=data.get("keys"),
            priority=data.get("priority"),
            always_on=data.get("always_on"),
            position=data.get("position"),
            embedding=emb,
        )
        h._json({"ok": ok})


@post("/api/lore/delete")
def _lore_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    rid = data.get("id")
    h._json({"ok": memory_store.delete_lore(rid) if rid else False})


@post("/api/lore/reindex")
def _lore_reindex(h, query, session, session_id):
    # 给指定世界书下「还没有向量」的存量条目补 embedding（语义召回上线后回填用）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length))) if length else {}
    book_id = data.get("book_id")
    scope = (
        memory_store.worldbook_scope(book_id)
        if book_id is not None
        else _session_scope(session)
    )
    done, skipped = 0, 0
    for e in memory_store.list_lore(scope):
        if e.get("has_embedding"):
            skipped += 1
            continue
        emb = _lore_embedding(e.get("title"), e.get("content"))
        if emb is None:
            skipped += 1
            continue
        memory_store.update_lore(e["id"], embedding=emb)
        done += 1
    h._json({"ok": True, "reindexed": done, "skipped": skipped})


@get("/api/memory/context")
def _get_memory_context(h, query, session, session_id):
    q = query.get("q", [""])[0]
    _m = build_injected_memory(session, q)
    h._json({"context": "\n\n".join(x for x in (_m["before"], _m["after"]) if x)})
    return


@get("/api/lore")
def _get_lore_list(h, query, session, session_id):
    # 列出某本世界书（book_id）下的全部条目
    book_id = query.get("book_id", [None])[0]
    if book_id is None:
        h._json({"lore": [], "error": "book_id 必填"})
    else:
        h._json(
            {
                "lore": memory_store.list_lore(
                    memory_store.worldbook_scope(book_id)
                )
            }
        )
    return


@get("/api/worldbooks/list")
def _get_worldbooks_list(h, query, session, session_id):
    # 全局：列出所有世界书（含条目数）
    h._json({"worldbooks": memory_store.list_worldbooks()})
    return


@get("/api/worldbooks/session")
def _get_worldbooks_session(h, query, session, session_id):
    # 本会话视角：自动并入（角色/用户绑定）的 + 手动挂载的 + 全部可选书
    char = session.active_prompts.get("character") or "default"
    user = session.active_prompts.get("user") or "default"
    manual = set(getattr(session, "active_worldbooks", []) or [])
    books = memory_store.list_worldbooks()
    auto, others = [], []
    for wb in books:
        bt, tgt = wb.get("bind_type"), wb.get("bind_target")
        if bt == "character" and tgt == char:
            wb = {**wb, "auto_reason": "character"}
            auto.append(wb)
        elif bt == "user" and tgt == user:
            wb = {**wb, "auto_reason": "user"}
            auto.append(wb)
        else:
            others.append(wb)
    h._json(
        {
            "character": char,
            "user": user,
            "auto": auto,
            "others": others,
            "manual_ids": sorted(manual),
        }
    )
    return


@get("/api/memory/search")
def _get_memory_search(h, query, session, session_id):
    q = query.get("q", [""])[0]
    try:
        k = int(query.get("k", ["5"])[0])
    except ValueError:
        k = 5
    scope = _session_scope(session)
    qv = embed_query(q) if q else None
    h._json(
        {
            "events": memory_store.recall_events(
                scope, query_vec=qv, query_text=q, k=k
            ),
            "chunks": memory_store.recall_chunks(
                scope, query_vec=qv, query_text=q, k=k
            ),
        }
    )
    return


@get("/api/memory/list")
def _get_memory_list(h, query, session, session_id):
    scope = _session_scope(session)
    kind = query.get("kind", [None])[0]
    h._json({"items": memory_store.list_memories(scope, kind)})
    return


@get("/api/memory/overview")
def _get_memory_overview(h, query, session, session_id):
    scope = _session_scope(session)
    sid = session.session_id
    items = memory_store.list_memories(scope)
    events = [i for i in items if i.get("__table__") == "events"]
    facts = [i for i in items if i.get("__table__") == "facts"]
    chunks = [i for i in items if i.get("__table__") == "chunks"]
    meta = memory_store.get_meta(f"summ:{sid}") or {"boundary": 0}
    with session.lock:
        total = len(session.messages)
    h._json(
        {
            "scope": scope,
            "counts": {
                "events": len(events),
                "facts": len(facts),
                "chunks": len(chunks),
            },
            "arc": memory_store.get_summary(f"arc:{scope}"),
            "arc_key": f"arc:{scope}",
            "session_summary": memory_store.get_summary(f"session:{sid}"),
            "session_key": f"session:{sid}",
            "events": events,
            "facts": facts,
            "meta": {
                "boundary": meta.get("boundary", 0),
                "total_messages": total,
                "state": meta.get("state", "idle"),
                "last_status": meta.get("last_status"),
                "last_time": meta.get("last_time"),
                "last_error": meta.get("last_error"),
            },
        }
    )
    return

