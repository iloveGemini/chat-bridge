# -*- coding: utf-8 -*-
"""统一记忆系统（存储 + 召回 + 增量总结）。

从 server.py 原样抽出，依赖：core(config/net/paths) + session.session + memory_store(底层库)。
无回环：session.session 不依赖本模块。
"""
import json
import time
import urllib.request

from core.config import config, load_config as _load_config
from core.net import log_print, _http_post_json, _extract_json
from core.model_params import build_sampling
from core.paths import SESSIONS_DIR
from session.session import (
    _session_scope, _resolve_session_worldbooks, GENESIS_SCENE, get_session,
)
import memory_store


def _embed_cfg():
    return config.get("embedding", {}) or {}


def _memory_cfg():
    return config.get("memory", {}) or {}


def embed_texts(texts):
    """线上 embedding（OpenAI 兼容 /embeddings）。未配置/失败返回 None → 触发关键词降级。"""
    cfg = _embed_cfg()
    if not cfg.get("enabled") or not cfg.get("api_key") or not cfg.get("base_url"):
        return None
    url = cfg["base_url"].rstrip("/") + "/embeddings"
    payload = {"model": cfg.get("model", "BAAI/bge-m3"), "input": texts}
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.get('api_key')}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [item["embedding"] for item in data["data"]]
    except Exception as e:
        log_print(f"🧠 [embed 失败，降级关键词]: {e}")
        return None


def embed_query(text):
    if not text:
        return None
    vecs = embed_texts([text])
    return vecs[0] if vecs else None


def _lore_embedding(title, content):
    """世界书条目向量：标题+正文一起嵌入，供语义召回用。未配置 embedding 时返回 None。"""
    text = f"{title or ''} {content or ''}".strip()
    return embed_query(text) if text else None


def build_injected_memory(session, query_text):
    """组装注入 system prompt 的记忆块（facts+arc+近况+召回事件/切片），两模式共用。"""
    scope = _session_scope(session)
    qv = embed_query(query_text) if query_text else None
    mcfg = _memory_cfg()
    diag = {}
    # 世界书扫描文本：当前消息 + 当前场景的地点/时间（天然触发源）
    cur_t = getattr(session, "current_time", GENESIS_SCENE["time"])
    cur_p = getattr(session, "current_place", GENESIS_SCENE["place"])
    lore_scan = f"{query_text or ''} {cur_p} {cur_t}"
    lore_scopes, _ = _resolve_session_worldbooks(session)
    result = memory_store.build_memory_context(
        scope,
        session.session_id,
        query_vec=qv,
        query_text=query_text or "",
        top_k=mcfg.get("top_k", 5),
        recall_n=mcfg.get("recall_n", 30),
        lore_scan=lore_scan,
        lore_sem_topk=mcfg.get("lore_sem_topk", 3),
        lore_sem_threshold=mcfg.get("lore_sem_threshold", 0.40),
        lore_warm_rounds=mcfg.get("lore_warm_rounds", 2),
        lore_scopes=lore_scopes,
        diag=diag,
    )
    # 主动召回（场景切换触发）：到了新场景，挑一条长期沉底的相关设定/回忆，
    # 以「邀请」而非「背景」的方式注入，让 AI 主动讲述（如童年往事），而不是等用户问。
    if mcfg.get("lore_spontaneous", True):
        last_scene = getattr(session, "_last_recall_scene_id", None)
        cur_scene = getattr(session, "current_scene_id", None)
        if last_scene is None:
            session._last_recall_scene_id = cur_scene  # 首次见到，仅初始化不触发
        elif cur_scene != last_scene:
            session._last_recall_scene_id = cur_scene
            try:
                ent = memory_store.pick_spontaneous_lore(
                    lore_scopes,
                    scene_scan=f"{cur_p} {cur_t}",
                    query_vec=qv,
                    min_priority=mcfg.get("lore_spont_min_priority", 1),
                    cooldown_sec=mcfg.get("lore_spont_cooldown_sec", 86400),
                )
                if ent:
                    memory_store.mark_lore_surfaced(ent["id"])
                    spont = (
                        "<spontaneous_recall>\n刚转入新场景，此刻很适合你主动、自然地提起下面这段"
                        "（觉得不贴合当下就跳过，别硬塞，也别每句都扯）：\n"
                        f"【{ent['title']}】{ent['content']}\n</spontaneous_recall>"
                    )
                    result = (result + "\n\n" + spont) if result else spont
                    log_print(f"💭 [主动召回·场景] 注入「{ent['title']}」@ {cur_p}")
            except Exception as e:
                log_print(f"💭 [主动召回失败]: {e}")

    # P0 召回可观测：每次召回打印一行（含模式/分数/命中/去重），方便肉眼判断准不准
    if mcfg.get("recall_log", True):
        try:
            log_print(memory_store.format_recall_log(diag))
        except Exception as e:
            log_print(f"🧠 [recall_log 失败]: {e}")
    return result


SUMMARY_SYSTEM_PROMPT = """你是记忆分析师。对比【已有记忆状态】与【新对话】，只提取新对话里【新增】的、能改变未来互动方式的信息；严格增量，绝不重复已有内容。只输出一个 JSON 对象，不要任何解释或代码围栏。

# 核心要求
- 增量：只记新对话里的新东西，已有记忆已覆盖的绝不重复。
- 面向召回：event.summary 是"高召回的回忆卡片"，不是剧情概括；要让未来用户用一句口语提起这段也能命中。
- 保留原词：人名、原文的称呼/昵称/别称、地点、关键物件/道具、具体动作、情绪态度、关系变化、约定/承诺/交换条件、秘密、暧昧或冲突的钩子——一律保留原文用词，不要改写成抽象同义词。
- 写具体：写清"谁，在什么时间/地点，用什么方式，对谁，做了什么，出现了什么道具，结果如何"。禁止"两人发生冲突""关系升温""气氛暧昧""揭示了一个秘密"这类空话。
- 长度：优先 1 句；信息确实过多再写 2 句，不要拆成空泛铺垫+具体补充。
- 笔触：朴实、白描、有烟火气，避免比喻和文学化修饰。

## summary 反例 → 正例
反例：二人在酒馆发生冲突，关系恶化。
正例：苏晚在黑鹭酒馆当众把欠条拍到顾衡胸口，骂他拿她母亲旧宅做赌注，顾衡想抓她手腕被甩开，赌客起哄，两人彻底撕破脸。
反例：她揭示了一个秘密，对方受到打击。
正例：周柠在浴室门口盯着林雨锁骨上的咬痕逼问昨晚和谁在一起，林雨一边整理湿透的白衬衫一边嘴硬否认，最后答应明晚还去旧码头见她。

# 事件分类
type：相遇=初次接触 / 冲突=对抗误解边界试探情绪顶撞 / 揭示=隐瞒被打破主动坦白无意泄露 / 抉择=站队承诺拒绝回避留白 / 羁绊=关系加深或破裂 / 转变=角色或局势改变 / 收束=问题解决和解 / 日常=共同生活照顾习惯性互动低强度陪伴
weight 与 importance 对应：核心=5(删掉关系理解就断裂) 主线=4(推动当前关系或情绪线) 转折=3(改变关系走向/信任/边界) 点睛=2(有细节不影响主线) 氛围=1(纯氛围)

# 事件时空与因果字段
- scene_id / timeLabel / placeLabel：直接承袭该事件所在【剧本切片】头部标注的 scene 编号与时空标签，原样照抄，不要自己编。
- caused_by：在【过往事件简表】里找引发本次事件的 0~2 个前置事件的整数 ID（如 [11] 或 [11,8]）；无直接因果就写 []。只能填简表里真实出现过的 evt 编号。

# factUpdates（SPO 硬事实增量）
- 只记硬性事实：身份、边界、承诺、偏好、禁忌、物品归属、位置、关系状态等；情绪和剧情不要写成 fact。
- 字段：s=主体 p=谓词 o=值；以 s+p 为键覆盖旧值，只输出新增或变化的条目。
- 关系类谓词用 "对X的看法"（X 为对象人名）。谓词复用已有的，别发明同义词，保持少、硬、稳定。
- isState：若该事实是不可改变的底层设定/生理禁忌/世界观公理/底层称呼（如对芒果过敏、患胃病、称呼对方为宝宝），设 true（容量清理时免疫删除）；寻常偏好/近期小习惯设 false。
- retracted：若剧本中某条旧事实明确失效或被推翻（伤口痊愈、误会解除、约定取消），输出 {"s":"张三","p":"腿伤","retracted":true} 通知删除该条，o 可省略。

# arc（关系弧）
严禁写成散文叙事。格式锁定为：[当前关系终态坐标描述] (进度: X%)。例：处于试探彼此底线、偶尔嘴硬但行动偏袒的暧昧初升期 (进度: 35%)。无变化则空串。

# 输出格式（只输出这一个 JSON）
{"events":[{"scene_id":"scene_12","timeLabel":"Day 2·中午","placeLabel":"食堂二楼","type":"相遇|冲突|揭示|抉择|羁绊|转变|收束|日常","weight":"核心|主线|转折|点睛|氛围","summary":"按上面要求写的高召回回忆卡片","importance":5,"caused_by":[11]}],"factUpdates":[{"s":"主体","p":"谓词","o":"值","isState":false,"retracted":false}],"arc":"[关系终态坐标] (进度: X%)，覆盖式，无变化则空串","session_summary":"当前对话进展的简短滚动摘要，覆盖式，可空"}
没有新东西的字段返回空数组或空字符串；字符串内部避免英文双引号。"""


def run_summary(session, chat_history):
    """增量总结：把一段对话蒸馏成 events/facts/arc/session_summary 落库，并把原文切片入 chunks。"""
    _load_config()
    # 总结可用独立 API（summary_api）；未配 base_url 时回退到聊天用的 api
    sa = config.get("summary_api") or {}
    api_cfg = sa if sa.get("base_url") else config.get("api", {})
    base = api_cfg.get("base_url", "").rstrip("/")
    if not base:
        return (False, "未配置总结 API")
    url = f"{base}/chat/completions"
    scope = _session_scope(session)
    sid = session.session_id

    # 已有记忆状态
    facts = memory_store.get_facts(scope)
    arc = memory_store.get_summary(f"arc:{scope}") or ""
    sess_sum = memory_store.get_summary(f"session:{sid}") or ""
    state_lines = []
    if arc:
        state_lines.append(f"关系: {arc}")
    if sess_sum:
        state_lines.append(f"近况: {sess_sum}")
    for f in facts[:50]:
        state_lines.append(f"事实: {f['subject']} {f['predicate']} {f['object']}")
    state_text = "\n".join(state_lines) or "（暂无）"

    if not any((m.get("text") or "").strip() for m in chat_history):
        return (False, "无新对话")

    # === 前情挂历：双轨召回历史事件（语义远期 Top5 + 时间近期 Top15）合并去重 ===
    tail_text = " ".join((m.get("text") or "") for m in chat_history[-8:]).strip()
    query_vec = embed_query(tail_text) if tail_text else None
    semantic_events = (
        memory_store.recall_events(scope, query_vec=query_vec, k=5) if query_vec else []
    )
    recent_events = memory_store.recall_events(
        scope, query_vec=None, query_text="", k=15
    )

    merged_map = {}
    for e in semantic_events:
        if (e.get("score") or 0) >= 0.50:  # 纪委卡死线：低于 0.5 的远期召回不要
            e["_is_distant"] = True
            merged_map[e["id"]] = e
    for e in recent_events:  # 近期连续性覆盖远期标记
        e["_is_distant"] = False
        merged_map[e["id"]] = e
    sorted_events = sorted(merged_map.values(), key=lambda x: x["id"])

    catalog_lines = []
    for e in sorted_events:
        s_id = e.get("scene_id") or "scene_0"
        t_lbl = e.get("time_label") or "早前"
        p_lbl = e.get("place_label") or "未知地点"
        snippet = (e.get("summary") or "").strip()[:45]
        star = " 🌟[远期伏笔]" if e.get("_is_distant") else ""
        catalog_lines.append(
            f"[evt-{e['id']}] [{s_id}] ({t_lbl}·{p_lbl}){star} -> {snippet}..."
        )
    catalog_str = "\n".join(catalog_lines) if catalog_lines else "（暂无前置事件记录）"

    # === 待总结剧本：按 scene_id 物理分块渲染 ===
    script_lines = []
    _last_scene = object()
    for m in chat_history:
        t = (m.get("text") or "").strip()
        if not t:
            continue
        s_id = m.get("scene_id") or "scene_0"
        if s_id != _last_scene:
            t_lbl = m.get("time") or "未知时间"
            p_lbl = m.get("place") or "未知地点"
            script_lines.append(f"\n【场景切片：{s_id} ({t_lbl} @ {p_lbl})】")
            _last_scene = s_id
        script_lines.append(f"{m.get('role')}: {t}")
    script_text = "\n".join(script_lines).strip()

    user_content = (
        f"【已有记忆状态】\n{state_text}\n\n"
        f"【过往事件简表（用于 caused_by 因果回溯）】\n{catalog_str}\n\n"
        f"【新对话剧本（已按场景切片）】\n{script_text}"
    )

    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        **build_sampling(api_cfg, 0.2),
    }
    try:
        _res = _http_post_json(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_cfg.get('api_key')}",
            },
            timeout=90,
            tag="总结",
        )
        content = _res["choices"][0]["message"]["content"]
    except Exception as e:
        log_print(f"🧠 [总结失败]: {e}")
        return (False, str(e))

    parsed = _extract_json(content)
    if not parsed:
        log_print("🧠 [总结失败]: 无法解析 JSON")
        return (False, "无法解析 LLM 返回的 JSON")

    def _clean_caused_by(raw):
        """只保留简表里能出现的整数 ID（容忍字符串数字），最多 2 个。"""
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw:
            if isinstance(x, bool):
                continue
            if isinstance(x, (int, float)):
                out.append(int(x))
            elif isinstance(x, str) and x.strip().lstrip("-").isdigit():
                out.append(int(x.strip()))
        return out[:2]

    # events（批量 embed 后落库，带场景时空 + 因果）
    ev_pairs = [
        (ev, (ev.get("summary") or "").strip()) for ev in (parsed.get("events") or [])
    ]
    ev_pairs = [(ev, s) for ev, s in ev_pairs if s]
    ev_vecs = embed_texts([s for _, s in ev_pairs]) if ev_pairs else None
    for i, (ev, s) in enumerate(ev_pairs):
        memory_store.upsert_event(
            scope,
            s,
            session_id=sid,
            type=ev.get("type"),
            weight=ev.get("weight"),
            importance=int(ev.get("importance", 3) or 3),
            caused_by=_clean_caused_by(ev.get("caused_by")),
            scene_id=ev.get("scene_id"),
            time_label=ev.get("timeLabel"),
            place_label=ev.get("placeLabel"),
            embedding=(ev_vecs[i] if ev_vecs else None),
        )

    # factUpdates（KV 覆盖 / 状态钢印 / 静默抹杀）；兼容旧字段名 facts
    fact_updates = parsed.get("factUpdates")
    if fact_updates is None:
        fact_updates = parsed.get("facts") or []
    for f in fact_updates:
        s = f.get("s") or f.get("subject")
        p = f.get("p") or f.get("predicate")
        o = f.get("o") or f.get("object") or ""
        if not (s and p):
            continue
        if f.get("retracted"):
            memory_store.upsert_fact(scope, str(s), str(p), retracted=True)
        else:
            memory_store.upsert_fact(
                scope, str(s), str(p), str(o), is_state=bool(f.get("isState"))
            )
    # 容量卫士：超限时斩首最古老的非核心事实，核心 is_state 免疫
    memory_store.prune_facts(scope)

    # arc / session_summary（覆盖，空则不动）
    if (parsed.get("arc") or "").strip():
        memory_store.upsert_summary(f"arc:{scope}", parsed["arc"].strip())
    if (parsed.get("session_summary") or "").strip():
        memory_store.upsert_summary(f"session:{sid}", parsed["session_summary"].strip())

    # 原文切片入库（细节召回；批量 embed）。保留说话人标签：AI 侧用 RP 角色名，用户侧用用户设定名
    char_label = (session.active_prompts.get("character") or "").strip()
    if char_label in ("", "default"):
        char_label = "AI"
    user_label = (session.active_prompts.get("user") or "").strip()
    if user_label in ("", "default", "默认"):
        user_label = "用户"

    chunk_pairs = []  # (speaker, text)
    for m in chat_history:
        t = (m.get("text") or "").strip()
        if len(t) < 8:
            continue
        spk = user_label if m.get("role") == "user" else char_label
        chunk_pairs.append((spk, t))

    ch_vecs = embed_texts([t for _, t in chunk_pairs]) if chunk_pairs else None
    for i, (spk, t) in enumerate(chunk_pairs):
        memory_store.add_chunk(
            scope,
            t,
            session_id=sid,
            speaker=spk,
            embedding=(ch_vecs[i] if ch_vecs else None),
        )

    log_print(
        f"🧠 [记忆更新] scope={scope} +{len(ev_pairs)}事件 / +{len(chunk_pairs)}切片"
    )
    return (True, None)


def _migrate_legacy_memory():
    """一次性幂等迁移：旧 data/sessions/*/memory.json 的 profile → summaries 的 arc 行。"""
    try:
        for d in SESSIONS_DIR.iterdir():
            if not d.is_dir():
                continue
            mf = d / "memory.json"
            if not mf.exists():
                continue
            try:
                profile = (
                    json.loads(mf.read_text(encoding="utf-8"))
                    .get("profile", "")
                    .strip()
                )
            except Exception:
                continue
            if not profile or profile.startswith("暂时没有"):
                continue
            s = get_session(d.name)
            scope = _session_scope(s)
            if not memory_store.get_summary(f"arc:{scope}"):
                memory_store.upsert_summary(f"arc:{scope}", profile)
                log_print(f"🧠 [迁移] {d.name} profile → arc:{scope}")
    except Exception as e:
        log_print(f"🧠 [迁移失败]: {e}")


def _summ_batch():
    """每批总结的消息数（可配，默认 16）。"""
    return _memory_cfg().get("summarize_every", 16)


def _summ_meta(sid):
    return memory_store.get_meta(f"summ:{sid}") or {"boundary": 0}


def _set_summ_meta(sid, **kw):
    m = _summ_meta(sid)
    m.update(kw)
    memory_store.set_meta(f"summ:{sid}", m)


def _needs_summary(session):
    """未总结的消息是否已攒够一批。"""
    b = _summ_meta(session.session_id).get("boundary", 0)
    return (len(session.messages) - b) >= _summ_batch()


def summarize_session(session, full=False):
    """从 boundary 推进总结。full=True 把积压全部补完（分批，带状态）。"""
    sid = session.session_id
    batch = _summ_batch()

    def _now():
        return time.strftime("%Y-%m-%d %H:%M:%S")

    _set_summ_meta(sid, state="running", last_error="")
    try:
        while True:
            with session.lock:
                total = len(session.messages)
                b = _summ_meta(sid).get("boundary", 0)
                if b > total:
                    b = 0  # 消息被清空/缩短 → 重置边界
                window = [
                    m
                    for m in session.messages[b : b + batch]
                    if m.get("type") not in ("reasoning", "tool_call", "tool_result")
                ]
            # 非 full 需攒够一批；full 时剩余 >=2 条也总结
            if (len(window) < 2) if full else (len(window) < batch):
                break
            ok, err = run_summary(session, window)
            if not ok:
                _set_summ_meta(
                    sid,
                    state="idle",
                    last_status="failed",
                    last_time=_now(),
                    last_error=err or "未知错误",
                )
                return
            b += len(window)
            _set_summ_meta(
                sid, boundary=b, last_status="success", last_time=_now(), last_error=""
            )
            if not full or b >= total:
                break
            time.sleep(2)  # 缓一拍，避开免费档限速
    finally:
        if _summ_meta(sid).get("state") == "running":
            _set_summ_meta(sid, state="idle")
