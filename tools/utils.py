# -*- coding: utf-8 -*-
import re

_KW_SKIP = {"if", "for", "while", "switch", "catch", "return", "function", "class", "else"}

def safe_name(name):
    return "".join(c for c in (name or "") if c.isalnum() or c in "-_")

def py_outline(src):
    import ast
    tree = ast.parse(src)
    out = []
    def doc1(node):
        d = ast.get_docstring(node)
        return (d or "").strip().split("\n")[0][:80]
    def walk(body, cls=None):
        for ch in body:
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nm = f"{cls}.{ch.name}" if cls else ch.name
                a = ", ".join(x.arg for x in ch.args.args)
                out.append({
                    "type": "method" if cls else "function",
                    "name": nm, "signature": f"{ch.name}({a})",
                    "start_line": ch.lineno,
                    "end_line": getattr(ch, "end_lineno", ch.lineno),
                    "doc": doc1(ch),
                })
            elif isinstance(ch, ast.ClassDef):
                out.append({
                    "type": "class", "name": ch.name,
                    "signature": f"class {ch.name}",
                    "start_line": ch.lineno,
                    "end_line": getattr(ch, "end_lineno", ch.lineno),
                    "doc": doc1(ch),
                })
                walk(ch.body, ch.name)
    walk(tree.body)
    return out

def py_locate(src, want):
    import ast
    tree = ast.parse(src)
    cls_part, _, fn_part = want.partition(".")
    def search(body, cls=None):
        for ch in body:
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if fn_part:
                    if cls == cls_part and ch.name == fn_part:
                        return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
                elif ch.name == want:
                    return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
            elif isinstance(ch, ast.ClassDef):
                if not fn_part and ch.name == want:
                    return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
                r = search(ch.body, ch.name)
                if r:
                    return r
        return None
    return search(tree.body)

def regex_outline(src):
    out = []
    pats = [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\("), "function"),
        (re.compile(r"^\s*(\w+)\s*\([^)]*\)\s*\{"), "method"),
    ]
    for i, ln in enumerate(src.splitlines(), 1):
        for pat, kind in pats:
            m = pat.match(ln)
            if m and m.group(1) not in _KW_SKIP:
                out.append({
                    "type": kind, "name": m.group(1),
                    "signature": ln.strip()[:80],
                    "start_line": i, "end_line": i, "doc": "",
                })
                break
    return out

def regex_locate(lines, want):
    name_pat = re.compile(r"\b" + re.escape(want) + r"\b")
    for i, ln in enumerate(lines):
        if name_pat.search(ln) and ("(" in ln or "class" in ln or "=>" in ln):
            start = i + 1
            head = "".join(lines[i : i + 3])
            if "{" in head:
                depth = 0
                started = False
                for j in range(i, len(lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        started = True
                    if started and depth <= 0:
                        return (start, j + 1)
            return (start, min(len(lines), start + 60))
    return None

def read_target_session(args):
    sid = "".join(c for c in (args.get("target_session_id") or "") if c.isalnum() or c in "-_")
    if not sid:
        raise ValueError("缺少 target_session_id")
    return sid, f"sess:{sid}"
