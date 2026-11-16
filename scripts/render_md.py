#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 生成：模板渲染 + YAML frontmatter（块状列表，Obsidian 风格）+ 完整性硬检查。

结构对齐范例《10月18日练习》《C语言精读10篇》：
  练习文档 = 标题 + 配套笔记 + 本次情况 + 练习前进度
             + N×(第X题｜主题 → 原题 / 我的答案 / 标准答案 / 注意事项及易错点 / 相关笔记)
             + 总结(知识点速查表 / 最需要改进的N点 / 做得好的地方 / 未掌握知识点与掌握度变化 / 下一步建议)
  精读文档 = 标题 + 选题依据 + 配套笔记 + 读法 + 目录表
             + N×(第X篇｜主题 → 为什么读 / 代码 / 输入 / 先预测 / 实际输出 / 验证状态 / 盯住几点 / 相关笔记)
             + 暂时先不要读的

硬规则：
* 原题（含待读代码）与「我的答案」必须逐字出现，缺一个就抛 IncompleteDocument。
* frontmatter 必须可解析，且含 date（带年份）/languages/score/tags/knowledge_points。
* 知识点写 [[双向链接]]；LaTeX 原样保留；模型没写的内容渲染成显式占位，不编造。

模板语法：{{ field }}  {{ a.b }}  {{#each items}}…{{/each}}（内含 {{index}} {{first}} {{last}}）
          {{#if x}}…{{else}}…{{/if}}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery as M        # noqa: E402
import questions as QS     # noqa: E402

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
REQUIRED_FRONTMATTER = ["date", "languages", "score", "tags", "knowledge_points"]
PLACEHOLDER = "（模型未提供，不编造）"
_TOKEN = re.compile(r"\{\{(.*?)\}\}", re.S)


class IncompleteDocument(RuntimeError):
    pass


# --------------------------------------------------------------------------- 模板引擎
def _truthy(v) -> bool:
    if v is None or v is False:
        return False
    if v is True:
        return True
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    if isinstance(v, (int, float)):
        return v != 0
    return True


def _lookup(ctx, path: str):
    path = path.strip()
    if path == "this":
        return ctx.get("this") if isinstance(ctx, dict) and "this" in ctx else ctx
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur


def _parse(nodes: list, i: int = 0, until: tuple = ()) -> tuple[list, int]:
    out = []
    while i < len(nodes):
        tok = nodes[i]
        if isinstance(tok, str):
            out.append(("text", tok)); i += 1; continue
        head = tok[1].strip()
        if head in until:
            return out, i
        if head.startswith("#each "):
            body, j = _parse(nodes, i + 1, until=("/each",))
            out.append(("each", head[6:].strip(), body)); i = j + 1; continue
        if head.startswith("#if "):
            body, j = _parse(nodes, i + 1, until=("else", "/if"))
            else_body = []
            if nodes[j][1].strip() == "else":
                else_body, j2 = _parse(nodes, j + 1, until=("/if",))
                j = j2
            out.append(("if", head[4:].strip(), body, else_body)); i = j + 1; continue
        out.append(("var", head)); i += 1
    return out, i


def _render_nodes(nodes: list, ctx) -> str:
    parts = []
    for n in nodes:
        kind = n[0]
        if kind == "text":
            parts.append(n[1])
        elif kind == "var":
            v = _lookup(ctx, n[1])
            parts.append("" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
        elif kind == "each":
            items = _lookup(ctx, n[1]) or []
            if not isinstance(items, list):
                items = [items]
            for idx, item in enumerate(items, 1):
                child = dict(item) if isinstance(item, dict) else {"this": item}
                child["index"] = idx
                child["first"] = idx == 1
                child["last"] = idx == len(items)
                parts.append(_render_nodes(n[2], child))
        elif kind == "if":
            parts.append(_render_nodes(n[2] if _truthy(_lookup(ctx, n[1])) else n[3], ctx))
    return "".join(parts)


def render(template: str, context: dict) -> str:
    raw = _TOKEN.split(template)
    nodes = [x if i % 2 == 0 else ("e", x) for i, x in enumerate(raw)]
    tree, _ = _parse(nodes)
    return _render_nodes(tree, context)


def load_template(name: str, template_dir: str | Path | None = None) -> str:
    p = Path(template_dir) if template_dir else TEMPLATE_DIR
    return (p / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- frontmatter
def yaml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return '""'
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or s.strip() != s or re.search(r'[:#\[\]{},&*!|>%@`"\']', s) or re.match(r"^[-?\s]", s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def make_frontmatter(data: dict) -> str:
    """列表用 Obsidian 常见的块状写法（tags: 换行  - C语言）。"""
    lines = []
    for k, v in data.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                lines += [f"  - {yaml_scalar(x)}" for x in v]
        elif isinstance(v, dict):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {yaml_scalar(v)}")
    return "\n".join(lines)


def _scalar(val: str):
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        return val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if val == "true":
        return True
    if val == "false":
        return False
    if re.match(r"^-?\d+$", val):
        return int(val)
    if re.match(r"^-?\d+\.\d+$", val):
        return float(val)
    return val


def parse_frontmatter(text: str) -> dict | None:
    """解析本文档生成的 YAML 子集：块状/内联列表、引号字符串、布尔、数字。"""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return None
    out: dict = {}
    cur_key = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item and cur_key and isinstance(out.get(cur_key), list):
            out[cur_key].append(_scalar(item.group(1).strip()))
            continue
        mm = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not mm:
            return None
        key, val = mm.group(1), mm.group(2).strip()
        cur_key = key
        if val == "":
            out[key] = []
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = []
            if inner:
                for piece in re.findall(r'"(?:[^"\\]|\\.)*"|[^,]+', inner):
                    items.append(_scalar(piece.strip()))
            out[key] = items
        else:
            out[key] = _scalar(val)
    return out


def validate_frontmatter(text: str, required: list[str] | None = None) -> list[str]:
    errs = []
    fm = parse_frontmatter(text)
    if fm is None:
        return ["frontmatter 缺失或格式非法（必须以 --- 开始并以 --- 结束）"]
    for k in (required or REQUIRED_FRONTMATTER):
        if k not in fm:
            errs.append(f"frontmatter 缺少 {k}")
    d = str(fm.get("date", ""))
    if not re.match(r"^\d{4}-\d{2}-\d{2}", d):
        errs.append(f"date 必须是 YYYY-MM-DD（年份写进 frontmatter），当前 {d!r}")
    for k in ("languages", "tags", "knowledge_points"):
        if k in fm and not isinstance(fm[k], list):
            errs.append(f"{k} 必须是列表")
    if "score" in fm and fm["score"] not in (None, "") and not isinstance(fm["score"], (int, float)):
        errs.append(f"score 必须是数字或空，当前 {fm['score']!r}")
    return errs


# --------------------------------------------------------------------------- 片段构造
def fence(code, lang: str = "") -> str:
    if code is None:
        return PLACEHOLDER
    body = str(code).rstrip("\n")
    return f"```{lang}\n{body}\n```" if lang else f"```\n{body}\n```"


def wikilinks(kps) -> str:
    return "、".join(f"[[{k}]]" for k in (kps or [])) or "—"


def related_notes_md(rn) -> str:
    out = []
    for item in rn or []:
        if isinstance(item, str):
            out.append(f"[[{item}]]")
        elif isinstance(item, dict) and item.get("target"):
            link = f'[[{item["target"]}|{item["alias"]}]]' if item.get("alias") else f'[[{item["target"]}]]'
            out.append(f'{link}（{item["note"]}）' if item.get("note") else link)
    return "、".join(out) or PLACEHOLDER


def bullets(items, placeholder=PLACEHOLDER) -> str:
    if isinstance(items, str):
        items = [x for x in items.splitlines() if x.strip()] or [items]
    items = [str(x).lstrip("- ").strip() for x in (items or []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in items) or placeholder


def numbered(items, placeholder=PLACEHOLDER) -> str:
    if isinstance(items, str):
        items = [x for x in items.splitlines() if x.strip()] or [items]
    items = [re.sub(r"^\d+[.、)]\s*", "", str(x)).strip() for x in (items or []) if str(x).strip()]
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)) or placeholder


def _cell(v, limit: int = 120) -> str:
    if v is None:
        return "—（缺行）"
    t = str(v).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return (t.replace("|", "\\|").replace("\n", "<br>")[:limit] or "—")


def requirements_block(q: dict) -> str:
    """按范例拼「原题」的要求区：编号小问 + 输入输出 + 示例 + 要求 + 边界 + 测试输入 + 提示 + 挑战 + 自检三问。"""
    parts = []
    reqs = q.get("requirements") or []
    if len(reqs) > 1:
        parts.append(numbered(reqs))
    elif reqs:
        parts.append(str(reqs[0]))
    io = []
    if q.get("input_format"):
        io.append(f'**输入：** {q["input_format"]}')
    if q.get("output_format"):
        io.append(f'**输出：** {q["output_format"]}')
    if io:
        parts.append("\n\n".join(io))
    ex = q.get("examples") or []
    if ex:
        lines = ["**示例：**"]
        for i, e in enumerate(ex, 1):
            lines.append(f'{i}. 输入 `{str(e.get("input","")).strip()}` → 输出 `{str(e.get("output","")).strip()}`')
        parts.append("\n".join(lines))
    if q.get("example_line"):
        parts.append(f'**示例：** {q["example_line"]}')
    cons = q.get("constraints")
    if isinstance(cons, list) and cons:
        parts.append("**要求：**\n" + "\n".join(f"- {c}" for c in cons))
    elif isinstance(cons, str) and cons.strip():
        parts.append(f"**要求：** {cons}")
    if q.get("edge_cases"):
        parts.append("**边界情况：** " + "；".join(str(x) for x in q["edge_cases"]))
    if q.get("test_input"):
        parts.append(f'**测试输入：** `{q["test_input"]}`')
    if q.get("function_signature"):
        parts.append(f'**函数签名：** `{q["function_signature"]}`')
    if q.get("starter_code"):
        parts.append("**起始代码：**\n\n" + fence(q["starter_code"], q.get("language") or "c"))
    if q.get("hint_line"):
        parts.append(f'**提示：** {q["hint_line"]}')
    if q.get("challenge"):
        parts.append(f'**挑战：** {q["challenge"]}')
    if q.get("self_check_questions"):
        parts.append("**自检三问（写完程序后回答）：**\n" + numbered(q["self_check_questions"]))
    return "\n\n".join(x for x in parts if x)


def result_block(r: dict) -> str:
    """判定区：文本题给逐行对照表，代码题给沙箱用例表；跑不了就写诚实声明。"""
    head = [f'**判定：** {r.get("correctness") or PLACEHOLDER}']
    if r.get("status") != "已提交":
        head.append(f'> {r.get("error_reason") or "未提交，不编造答案与成绩。"}')
        return "\n\n".join(head)
    if r.get("score") is not None:
        head.append(f'**得分：** {r["score"]} / 100'
                    + (f'（{r["passed"]}/{r["total"]}）' if r.get("total") else ""))
    head.append(f'**执行情况：** {r.get("execution_statement") or PLACEHOLDER}')
    out = ["\n\n".join(head)]
    td = r.get("text_diff") or {}
    if td.get("rows"):
        rows = ["| 行 | 我的答案 | 标准答案 | 判定 |", "| :-- | :-- | :-- | :-- |"]
        for x in td["rows"]:
            rows.append(f'| {x["line"]} | {_cell(x["actual"])} | {_cell(x["expected"])} | {x["verdict"]} |')
        rows += ["", f'**逐行对照：{td["matched"]}/{td["total"]} 行一致'
                     + (f'，出错行：{td["wrong_lines"]}' if td.get("wrong_lines") else "") + "**"]
        out.append("\n".join(rows))
    ex = r.get("execution") or {}
    cases = ex.get("cases") or []
    if cases:
        rows = ["| 用例 | 输入 | 期望输出 | 实际输出 | 结果 |", "| :-- | :-- | :-- | :-- | :-- |"]
        for c in cases:
            mark = "✅ 通过" if c.get("passed") else "❌ 未通过"
            mark += "（超时）" if c.get("timed_out") else ""
            mark += "（输出截断）" if c.get("output_truncated") else ""
            rows.append(f'| {_cell(c.get("name"), 60)} | {_cell(c.get("stdin"))} | '
                        f'{_cell(c.get("expected_stdout"))} | {_cell(c.get("actual_stdout"))} | {mark} |')
        comp = ex.get("compile") or {}
        if comp.get("warnings"):
            rows += ["", "**编译警告：**", "", "```", str(comp["warnings"])[:1500], "```"]
        out.append("\n".join(rows))
    elif r.get("answer_kind") == "code" and not ex.get("ran"):
        issues = (ex.get("static_review") or {}).get("issues") or []
        if issues:
            out.append("**静态审查发现：**\n" + "\n".join(
                f'- [{i.get("severity")}] {i.get("detail")}'
                + (f'（第 {i["line"]} 行 `{i.get("snippet","")}`）' if i.get("line") else "")
                for i in issues[:8]))
    pts = r.get("answer_points") or []
    if pts:
        hit = set(r.get("points_hit") or [])
        missed = set(r.get("points_missed") or [])
        lines = ["**评分要点" + ("（教练对照判定）：**" if (hit or missed) else "（用于自查）：**")]
        for i, point in enumerate(pts, 1):
            mark = ""
            if hit or missed:
                mark = "　✅ 已覆盖" if i in hit else ("　❌ 未覆盖" if i in missed else "　—")
            lines.append(f"{i}. {point}{mark}")
        out.append("\n".join(lines))
    scq = r.get("self_check_questions") or []
    if scq:
        ans = r.get("self_check_answers") or []
        lines = ["**自检三问的答案：**"]
        for i, question in enumerate(scq, 1):
            a = ans[i - 1] if isinstance(ans, list) and len(ans) >= i else PLACEHOLDER
            lines.append(f"{i}. {question}\n   - {a}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def pitfalls_block(r: dict, extra=None) -> str:
    items = []
    corr = str(r.get("correctness") or "")
    if r.get("status") != "已提交":
        items.append("**本题未完成**：不计入得分，也不编造你的答案；补做后请对照标准答案自查。")
    elif corr.startswith(("完全正确", "正确")):
        items.append("本题作答正确。下面是这类题通用的易错点。")
    else:
        loc = r.get("error_location") or ""
        why = r.get("error_reason") or ""
        if loc and loc != "—":
            items.append(f"**你这道题错在哪里：** {loc}" + (f" —— {why}" if why and why != "—" else ""))
        elif why:
            items.append(f"**你这道题错在哪里：** {why}")
    if r.get("suggestion") and r["suggestion"] != "—":
        items.append(f'**修改建议：** {r["suggestion"]}')
    for x in (extra or []):
        items.append(str(x))
    return bullets(items)


# --------------------------------------------------------------------------- 练习文档
def _kp_table(questions, meta) -> str:
    rows = ["| 知识点 | 要点 | 相关题号 |", "| :-- | :-- | :-- |"]
    provided = {x.get("kp"): x for x in (meta.get("kp_table") or []) if isinstance(x, dict)}
    qmap: dict[str, list] = {}
    for q in questions:
        for kp in q.get("knowledge_points") or []:
            qmap.setdefault(kp, []).append(str(q.get("id", "")).lstrip("Qq"))
    order = sorted({k for q in questions for k in (q.get("knowledge_points") or [])},
                   key=lambda k: (k not in provided, k))
    for kp in order:
        item = provided.get(kp) or {}
        point = item.get("point") or PLACEHOLDER
        qs_ = item.get("questions") or qmap.get(kp) or []
        rows.append(f"| [[{kp}]] | {point} | {'、'.join(str(x) for x in qs_) or '—'} |")
    return "\n".join(rows)


def _mastery_change(records: dict, sid: str) -> str:
    lines = []
    for kp, rec in sorted(records.get("records", {}).items()):
        hist = [h for h in rec.get("history", []) if h.get("session_id") == sid]
        if not hist:
            continue
        detail = "、".join((h.get("question_id") or "?") + ("答对" if h.get("correct") else "答错")
                           for h in hist)
        lines.append(f'- [[{kp}]]：{hist[0].get("from")} → **{rec["state"]}**（依据：{detail}）')
    if not lines:
        lines.append("- 本次没有可判定的作答（未提交或无法运行），掌握度不变；不编造表现。")
    unmastered = [f'- [[{kp}]]：{r["state"]}' + ("（初始估计，尚未经练习检验）" if r.get("estimated") else "")
                  for kp, r in sorted(records.get("records", {}).items()) if r["state_index"] < 3]
    lines += ["", "**仍未达到「基本掌握」的知识点：**"]
    lines += unmastered or ["- （无）"]
    return "\n".join(lines)


def _sandbox_note(grading: dict) -> str:
    """把沙箱与隔离情况汇总成一句，放在文档头部（避免每道代码题重复一遍）。"""
    warns, iso, tool = [], {}, None
    for r in grading.get("results", []):
        ex = r.get("execution") or {}
        if ex.get("ran"):
            iso = ex.get("isolation") or {}
            tool = tool or (ex.get("toolchain") or "")
            for w in ex.get("warnings") or []:
                if w not in warns:
                    warns.append(w)
    if not iso:
        return ""
    parts = [f'临时目录 {iso.get("tmpdir_path", "?")}（用后已清理：{iso.get("cleaned")}）',
             f'超时 {iso.get("timeout_s")} s', f'输出上限 {iso.get("max_output_bytes")} B',
             f'不继承环境变量（白名单 {len(iso.get("env_allowlist") or [])} 项）',
             f'网络隔离：{iso.get("network_method")}'
             + ("" if iso.get("network_blocked") else "（best-effort，未真正断网，如实标注 network_blocked=false）")]
    if warns:
        parts.append("说明：" + warns[0][:120])
    return "；".join(parts)


def _progress_md(analysis: dict) -> str:
    """出题文档开头的摘要：**最多 5 行**（不展开整张分析表）。"""
    if not analysis:
        return PLACEHOLDER
    c = analysis.get("counts", {})
    lines = [
        f'- 可用知识点（T1~T3）：{c.get("usable", 0)} 个｜弱证据按未学排除：{c.get("excluded_weak", 0)} 个｜完全未学：{c.get("not_learned", 0)} 个',
        f'- 已掌握（练习确认）：{c.get("mastered", 0)} 个｜未完全掌握：{c.get("partial", 0)} 个｜正在学：{c.get("learning", 0)} 个',
        ("- 冷启动：以上为初始估计，笔记出现过 ≠ 已掌握。" if analysis.get("cold_start")
         else "- 已有练习记录，状态以练习表现为准。"),
    ]
    excl = analysis.get("excluded_weak_kps") or []
    if excl:
        lines.append("- 本次按未学排除：" + "、".join(f"[[{k}]]" for k in excl[:8])
                     + ("…" if len(excl) > 8 else ""))
    return "\n".join(lines[:5])


def build_practice_context(*, grading: dict, questions: list[dict], submissions: dict[str, str],
                           records: dict, analysis: dict | None = None, meta: dict | None = None) -> dict:
    meta = meta or {}
    analysis = analysis or {}
    ov = grading.get("overall", {})
    results = {r["id"]: r for r in grading.get("results", [])}
    date = meta.get("date") or time.strftime("%Y-%m-%d")
    langs = sorted({(q.get("language") or meta.get("language") or "c") for q in questions}) or ["c"]
    kps = sorted({k for q in questions for k in (q.get("knowledge_points") or [])})
    mm, dd = date.split("-")[1], date.split("-")[2]
    score = ov.get("score_submitted_only")
    fm = {"tags": meta.get("tags") or ["C语言", "练习记录", "programming-learning-coach"],
          "date": date, "languages": langs,
          "score": (score if score is not None else ""),
          "knowledge_points": kps,
          "session_id": grading.get("session_id", ""),
          "question_count": len(questions), "submitted": ov.get("submitted", 0),
          "unfinished": ov.get("unsubmitted", 0),
          "cold_start": bool(analysis.get("cold_start", records.get("cold_start", True))),
          "executed": bool(grading.get("sandbox_available")),
          "coach": "programming-learning-coach"}
    extra_pit = meta.get("extra_pitfalls") or {}
    qctx = []
    for i, q in enumerate(questions, 1):
        r = dict(results.get(q["id"], {}))
        kind = r.get("answer_kind") or QS.answer_kind(q)
        r["answer_kind"] = kind
        r.setdefault("self_check_questions", q.get("self_check_questions", []))
        code_lang = q.get("language") or langs[0]
        sub = submissions.get(q["id"])
        qctx.append({
            "index": i, "id": q["id"], "title": q.get("title", ""),
            "type_cn": r.get("type_cn") or QS.QUESTION_TYPES.get(
                q.get("question_type", ""), {}).get("cn", "未标注题型"),
            "difficulty": q.get("difficulty"), "est_minutes": q.get("est_minutes"),
            "kp_links": wikilinks(q.get("knowledge_points")),
            "stem": QS.stem_of(q) or PLACEHOLDER,
            "code_block": (fence(q["code_to_read"], code_lang) if q.get("code_to_read") else ""),
            "requirements_block": requirements_block(q),
            "my_answer_block": (fence(sub, "text" if kind == "text" else code_lang) if sub is not None
                                else "（未提交 —— 标记为「未完成」，不计入得分，也不编造你的答案）"),
            "reference_block": fence(r.get("reference_solution"),
                                     "text" if r.get("reference_kind") == "text" else code_lang),
            "explanation": r.get("step_by_step") or PLACEHOLDER,
            "result_block": result_block(r),
            "pitfalls": pitfalls_block(r, extra_pit.get(q["id"]) if isinstance(extra_pit, dict) else None),
            "related_notes": related_notes_md(q.get("related_notes")),
        })
    improvements = meta.get("improvements") or []
    return {
        "frontmatter": make_frontmatter(fm),
        "title": meta.get("title") or f"{int(mm)}月{int(dd)}日练习",
        "date": date,
        "companion_note": meta.get("companion_note") or PLACEHOLDER,
        "question_count": len(questions),
        "submitted_count": ov.get("submitted", 0), "unsubmitted_count": ov.get("unsubmitted", 0),
        "score_text": (f'{score} / 100（{ov.get("score_note", "")}）' if score is not None
                       else "（无可评分的提交）"),
        "execution_text": ("代码题已在沙箱中编译运行" if grading.get("sandbox_available")
                           else "代码题本次未运行，仅静态审查；文本题按逐行对照判定"),
        "progress_analysis": meta.get("progress_analysis") or _progress_md(analysis),
        "sandbox_note": _sandbox_note(grading),
        "key_points": meta.get("key_points") or PLACEHOLDER,
        "questions": qctx,
        "kp_table": _kp_table(questions, meta),
        "topic_links": meta.get("topic_links") or PLACEHOLDER,
        "improve_count": (len(improvements) if improvements else "N"),
        "improvements": numbered(improvements),
        "good_points": bullets(meta.get("good_points")),
        "mastery_change": _mastery_change(records, grading.get("session_id", "")),
        "next_steps": numbered(meta.get("next_steps")),
        "pending_model_input": "；".join(f'{r["id"]} 缺 {"、".join(r["pending_model_input"])}'
                                        for r in grading.get("results", []) if r.get("pending_model_input")),
        "unfinished_ids": "、".join(ov.get("unsubmitted_ids", [])),
    }


def assert_complete(doc: str, questions: list[dict], submissions: dict[str, str]) -> list[str]:
    """硬性完整性检查：原题（含待读代码）与我的答案一个都不能少，结构小节必须齐全。"""
    missing = []
    flat = re.sub(r"[ \t]+", "", doc)
    for q in questions:
        qid = str(q.get("id", ""))
        title = q.get("title") or ""
        if title and title not in doc:
            missing.append(f'题目 {qid} 缺少标题「{title}」')
        stem = re.sub(r"[ \t]+", "", (QS.stem_of(q) or "").strip())
        if stem and stem[:200] not in flat:
            missing.append(f"题目 {qid} 的题面描述不完整")
        if q.get("code_to_read"):
            body = re.sub(r"[ \t]+", "", str(q["code_to_read"]).strip())
            if body[:150] not in flat:
                missing.append(f"题目 {qid} 的待读代码不完整")
        if q.get("requirements"):
            for rq in q["requirements"][:3]:
                if re.sub(r"[ \t]+", "", str(rq).strip())[:60] not in flat:
                    missing.append(f"题目 {qid} 的要求条目缺失：{str(rq)[:40]}")
    for qid, code in submissions.items():
        body = (code or "").rstrip("\n")
        if body and body not in doc:
            missing.append(f"缺少 {qid} 的原始答案（必须逐字保留）")
    for head in ("### 原题", "### 我的答案", "### 标准答案", "### 注意事项及易错点",
                 "**相关笔记：**", "# 总结", "## 一、知识点速查表", "## 五、下一步建议"):
        if head not in doc:
            missing.append(f"文档结构缺少 {head}")
    if missing:
        raise IncompleteDocument("练习文档不完整：" + "；".join(missing))
    return missing


def render_practice(*, grading, questions, submissions, records, analysis=None, meta=None,
                    template: str | None = None, template_dir=None) -> str:
    tpl = template if template is not None else load_template("practice.md", template_dir)
    ctx = build_practice_context(grading=grading, questions=questions, submissions=submissions,
                                 records=records, analysis=analysis, meta=meta)
    doc = render(tpl, ctx)
    errs = validate_frontmatter(doc)
    if errs:
        raise IncompleteDocument("frontmatter 非法：" + "；".join(errs))
    assert_complete(doc, questions, submissions)
    return doc


# --------------------------------------------------------------------------- 精读文档（自编程序模式，默认）
ADVANCED_KPS = ("指针基础", "指针与数组", "指针与函数", "结构体", "联合体与枚举",
                "内存区域与动态分配", "预处理与多文件", "文件读写", "递归")


def not_yet_md(records: dict) -> str:
    """自动生成『暂时先不要读的』。

    判据不只是「未学习」：笔记里提到过、但**从未经练习检验**（仍是初始估计）的进阶知识点，
    同样说明还不能读大型开源项目 —— 笔记里出现过 ≠ 已掌握。
    """
    recs = (records or {}).get("records", {})
    if not recs:
        return PLACEHOLDER
    blocked, why = [], []
    for k in ADVANCED_KPS:
        r = recs.get(k)
        if r is None:
            continue
        if r["state_index"] == 0:
            blocked.append(k); why.append("未学习")
        elif r.get("estimated") and r["state_index"] <= M.IDX["待检验"]:
            blocked.append(k); why.append("笔记里提到过，但还没有练习检验过")
    if not blocked:
        return ("你已经掌握了指针、结构体等前置内容（都经过练习检验），可以开始读小型单文件的开源 C 库"
                "（先读头文件，再读函数实现）；大型项目仍建议按模块逐片读，"
                "届时改用开源片段模式（`render reading-opensource`），我会联网核验仓库、路径与许可证。")
    kps = "、".join("[[" + a + "]]" for a in blocked)
    reason = "都还没学" if set(why) == {"未学习"} else "要么还没学，要么只是笔记里提到过、还没有经过练习检验"
    return "\n".join([
        f"大型开源项目的源码（如 Linux 内核、Redis、SQLite）现在读不下去，因为它们大量使用{kps}等内容，"
        f"而这些{reason}。",
        "",
        "等学完这些之后，可以按下面的顺序过渡：",
        "",
        "1. 《C 程序设计语言》（K&R）第 1~2 章的例程：短小、经典，适合学完循环和数组后精读。",
        "2. 自己把本文的程序改写一遍：换输入、换条件、故意制造一个 bug 再修好，比只看更有效。",
        "3. 学完指针和结构体后，挑一个小型、单文件的开源 C 库（如 JSON 解析库、简单的字符串或链表库）："
        "先读头文件，再读函数实现。",
        "",
        "学到那一步时，可以再来找我，我会按你当时的进度重新选读物（届时改用开源片段模式，"
        "推荐真实开源项目并联网核验仓库、文件路径与许可证）。"])


def build_reading_context(items: list[dict], meta: dict | None = None, records: dict | None = None) -> dict:
    meta = meta or {}
    date = meta.get("date") or time.strftime("%Y-%m-%d")
    langs = sorted({i.get("language", "c") for i in items}) or ["c"]
    mm, dd = date.split("-")[1], date.split("-")[2]
    n = len(items)
    fm = {"tags": meta.get("tags") or ["C语言", "代码精读", "programming-learning-coach"],
          "date": date, "languages": langs, "score": meta.get("score", ""),
          "knowledge_points": sorted({k for i in items for k in (i.get("knowledge_points") or [])}),
          "items": n, "preview_items": sum(1 for i in items if i.get("preview")),
          "verified": sum(1 for i in items if i.get("compile_ok")),
          "coach": "programming-learning-coach"}
    ctx_items = []
    for idx, it in enumerate(items, 1):
        code_lang = it.get("language") or "c"
        ctx_items.append({
            **it, "index": idx,
            "topic_cell": ("【预习】" if it.get("preview") else "") + (it.get("title") or ""),
            "related_exercises": it.get("related_exercises") or "—",
            "why": it.get("why") or PLACEHOLDER,
            "code_block": fence(it.get("code"), code_lang),
            "input_spec": it.get("input_spec") or "无输入",
            "predict_block": numbered(it.get("predict_questions")),
            "output_block": fence(it.get("actual_output"), "text"),
            "verify_line": it.get("verify_line") or PLACEHOLDER,
            "watch_points": bullets(it.get("watch_points")),
            "related_notes": related_notes_md(it.get("related_notes")),
            "kp_links": wikilinks(it.get("knowledge_points")),
        })
    return {"frontmatter": make_frontmatter(fm),
            "title": meta.get("title") or f"{int(mm)}月{int(dd)}日代码精读{n}篇",
            "selection_basis": meta.get("selection_basis") or PLACEHOLDER,
            "companion_note": meta.get("companion_note") or PLACEHOLDER,
            "how_to_read": meta.get("how_to_read") or PLACEHOLDER,
            "items": ctx_items, "count": n,
            "not_yet": meta.get("not_yet") or not_yet_md(records or {})}


def render_reading(items: list[dict], meta: dict | None = None, records: dict | None = None,
                   template: str | None = None, template_dir=None) -> str:
    tpl = template if template is not None else load_template("code_reading.md", template_dir)
    doc = render(tpl, build_reading_context(items, meta, records))
    errs = validate_frontmatter(doc)
    if errs:
        raise IncompleteDocument("frontmatter 非法：" + "；".join(errs))
    for i, it in enumerate(items, 1):
        if it.get("title") and it["title"] not in doc:
            raise IncompleteDocument(f"精读文档缺少第 {i} 篇：{it['title']}")
        if it.get("code") and str(it["code"]).strip()[:80] not in doc:
            raise IncompleteDocument(f"精读文档第 {i} 篇代码不完整")
        if not it.get("preview") and not it.get("actual_output"):
            raise IncompleteDocument(f"精读文档第 {i} 篇缺少实际输出（必须先编译运行，不得凭空编写）")
    return doc


# --------------------------------------------------------------------------- 精读文档（开源片段模式，进阶）
def build_reading_context_opensource(recs: list[dict], meta: dict | None = None) -> dict:
    meta = meta or {}
    date = meta.get("date") or time.strftime("%Y-%m-%d")
    mm, dd = date.split("-")[1], date.split("-")[2]
    langs = sorted({r.get("language", "") for r in recs if r.get("language")}) or ["c"]
    items = []
    for i, r in enumerate(recs, 1):
        items.append({**r, "index": i, "kp_links": wikilinks(r.get("knowledge_points")),
                      "link_line": (f'<{r["url"]}>' if r.get("url") else "（离线，未提供链接 —— 不编造）"),
                      "path_line": (r.get("path") or "（离线，未提供路径 —— 不编造）"),
                      "license_line": (r.get("license") or "待验证"),
                      "tasks_md": numbered(r.get("tasks"))})
    fm = {"tags": meta.get("tags") or ["代码精读", "开源", "programming-learning-coach"],
          "date": date, "languages": langs, "score": meta.get("score", ""),
          "knowledge_points": sorted({k for r in recs for k in (r.get("knowledge_points") or [])}),
          "items": len(recs),
          "verified": sum(1 for r in recs if (r.get("verification") or {}).get("status") == "已验证"),
          "unverified": sum(1 for r in recs if (r.get("verification") or {}).get("status") != "已验证"),
          "coach": "programming-learning-coach"}
    return {"frontmatter": make_frontmatter(fm),
            "title": meta.get("title") or f"{int(mm)}月{int(dd)}日代码精读", "date": date,
            "items": items, "count": len(recs), "network_note": meta.get("network_note") or "",
            "weak_points": wikilinks(meta.get("weak_points") or []),
            "how_to_read": meta.get("how_to_read") or PLACEHOLDER}


def render_reading_opensource(recs: list[dict], meta=None, template=None, template_dir=None) -> str:
    tpl = template if template is not None else load_template("code_reading_opensource.md", template_dir)
    doc = render(tpl, build_reading_context_opensource(recs, meta))
    errs = validate_frontmatter(doc)
    if errs:
        raise IncompleteDocument("frontmatter 非法：" + "；".join(errs))
    for i, r in enumerate(recs, 1):
        if r.get("name") and r["name"] not in doc:
            raise IncompleteDocument(f"精读文档缺少第 {i} 项：{r['name']}")
    return doc


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC Markdown 生成")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("practice")
    s.add_argument("--grading", required=True); s.add_argument("--questions", required=True)
    s.add_argument("--records", required=True); s.add_argument("--analysis"); s.add_argument("--meta")
    s.add_argument("--submissions-dir"); s.add_argument("--template-dir"); s.add_argument("--out", required=True)
    s = sub.add_parser("reading"); s.add_argument("--items", required=True); s.add_argument("--meta")
    s.add_argument("--records"); s.add_argument("--template-dir"); s.add_argument("--out", required=True)
    s = sub.add_parser("reading-opensource"); s.add_argument("--recommendations", required=True)
    s.add_argument("--meta"); s.add_argument("--template-dir"); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def L(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def dump(doc, out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(doc, encoding="utf-8", newline="\n")
        print(json.dumps({"ok": True, "out": out, "bytes": len(doc.encode("utf-8")),
                          "frontmatter_errors": validate_frontmatter(doc)}, ensure_ascii=False, indent=2))

    if a.cmd == "practice":
        grading = L(a.grading)
        questions = L(a.questions)
        questions = questions.get("questions", questions) if isinstance(questions, dict) else questions
        records = L(a.records)
        analysis = L(a.analysis) if a.analysis else {}
        meta = L(a.meta) if a.meta else {}
        subs = {}
        if a.submissions_dir:
            for f in sorted(Path(a.submissions_dir).iterdir()):
                if f.is_file() and f.name != "index.json":
                    subs[f.stem] = f.read_text(encoding="utf-8")
        for r in grading.get("results", []):
            if r.get("my_answer") is not None:
                subs.setdefault(r["id"], r["my_answer"])
        dump(render_practice(grading=grading, questions=questions, submissions=subs, records=records,
                             analysis=analysis, meta=meta, template_dir=a.template_dir), a.out)
    elif a.cmd == "reading":
        data = L(a.items)
        items = data.get("items", data) if isinstance(data, dict) else data
        dump(render_reading(items, L(a.meta) if a.meta else {}, L(a.records) if a.records else {},
                            template_dir=a.template_dir), a.out)
    else:
        recs = L(a.recommendations)
        recs = recs.get("recommendations", recs) if isinstance(recs, dict) else recs
        dump(render_reading_opensource(recs, L(a.meta) if a.meta else {}, template_dir=a.template_dir), a.out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except IncompleteDocument as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(6)
