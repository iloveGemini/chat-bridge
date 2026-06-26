# -*- coding: utf-8 -*-
from pathlib import Path
import re

def get_schema():
    return {
        "type": "function", "function": {
            "name": "smart_file_insight",
            "description": "在【已知的某个文件】里深挖的利器：一次拿到该文件总行数 + 多个关键词/正则的全部命中(带行号，可带前后 context 上下文，可 offset 翻页)，等价于对这一个文件跑 wc -l 加多个 grep -n -C。适用于：已经定位到文件、要在里面找若干符号/用法并看上下文。注意：① 必须先给 filepath、只看单个文件——想在整个仓库里找『哪些文件含 X』请用 grep_files(跨文件发现)；② 它只回命中行+上下文，要通读整段/整文件请用 read_file_with_lines。",
            "parameters": {"type": "object", "properties": {
                "filepath": {"type": "string", "description": "要洞察的文件路径"},
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要同时搜索的多个关键字或正则表达式列表。例如 ['思考|reasoning', 'timestamp|time']"
                },
                "context": {
                    "type": "integer",
                    "description": "每个命中行额外显示的上下文行数(前后各 N 行，类似 grep -C)，默认 0，最大 10。命中行以 '>' 标记。"
                },
                "offset": {
                    "type": "integer",
                    "description": "翻页用：跳过每个模式的前 N 个命中再开始显示，默认 0。配合返回的提示翻到下一页。"
                },
                "max_matches": {
                    "type": "integer",
                    "description": "每个模式最多显示多少个命中，默认 30，最大 200。"
                }
            }, "required": ["filepath", "patterns"]}
        }
    }

def execute(args, context=None):
    filepath = args.get("filepath")
    patterns = args.get("patterns", [])

    if not isinstance(patterns, list):
        patterns = [patterns]

    # ---- 参数清洗与边界钳制 ----
    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    ctx = max(0, min(_int(args.get("context"), 0), 10))         # 上下文行数 0..10
    offset = max(0, _int(args.get("offset"), 0))                # 翻页偏移 >=0
    max_matches = max(1, min(_int(args.get("max_matches"), 30), 200))  # 每模式上限 1..200

    target_file = Path(filepath).resolve()
    # 防目录穿越可以靠项目根目录限定，这里简单实现

    if not target_file.exists() or not target_file.is_file():
        return {"error": "文件不存在"}

    try:
        lines = target_file.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return {"error": "该文件无法以文本(UTF-8)格式读取"}

    total_lines = len(lines)
    out = [f"=== {target_file.name} 概览 ==="]
    out.append(f"总行数: {total_lines}")
    if ctx or offset:
        out.append(f"(context={ctx}  offset={offset}  max_matches={max_matches})")

    def _fmt(idx, is_match):
        # 保留缩进、去尾空白、超长截断，命中行加 '>' 标记
        safe_line = lines[idx].rstrip()[:200]
        marker = ">" if is_match else " "
        return f"{marker}{str(idx + 1).rjust(5)} | {safe_line}"

    for pat in patterns[:5]:  # 防止 AI 发癫传太多词，最多并发搜 5 个
        try:
            regex = re.compile(pat, re.IGNORECASE)
        except Exception:
            out.append(f"\n=== 搜索 /{pat}/ ===")
            out.append("  [无效的正则表达式]")
            continue

        hits = [i for i, line in enumerate(lines) if regex.search(line)]
        total_hits = len(hits)

        if total_hits == 0:
            out.append(f"\n=== 搜索 /{pat}/  (命中 0 行) ===")
            out.append("  [未找到匹配项]")
            continue

        page = hits[offset:offset + max_matches]
        if not page:
            out.append(
                f"\n=== 搜索 /{pat}/  (命中 {total_hits} 行) ==="
            )
            out.append(f"  [offset={offset} 已超出命中总数 {total_hits}，无可显示]")
            continue

        shown_from = offset + 1
        shown_to = offset + len(page)
        out.append(
            f"\n=== 搜索 /{pat}/  (命中 {total_hits} 行，显示第 {shown_from}-{shown_to} 个) ==="
        )

        match_set = set(page)
        # 把每个命中扩成 [lo,hi] 上下文区间，相邻/重叠的合并，避免重复打印
        ranges = []
        for i in page:
            lo = max(0, i - ctx)
            hi = min(total_lines - 1, i + ctx)
            if ranges and lo <= ranges[-1][1] + 1:
                ranges[-1][1] = max(ranges[-1][1], hi)
            else:
                ranges.append([lo, hi])

        for bi, (lo, hi) in enumerate(ranges):
            if bi > 0:
                out.append("  --")  # 非连续块之间的分隔符(类似 grep)
            for n in range(lo, hi + 1):
                out.append(_fmt(n, n in match_set))

        remaining = total_hits - shown_to
        if remaining > 0:
            out.append(
                f"  ... 还有 {remaining} 处未显示，用 offset={shown_to} 翻下一页"
            )

    return {"result": "\n".join(out)}
