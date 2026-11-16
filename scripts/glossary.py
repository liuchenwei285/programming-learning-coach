#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识点词库加载、匹配与**证据分级**（T1~T5）。

分级规则（与 references/note-parsing.md 一致）：
  T1 系统讲解：强别名命中 Markdown 小标题            → 直接可用，不问
  T2 代码示例：强别名命中代码块内                    → 直接可用，不问
  T3 正文明确提及：强别名在正文命中 ≥3 次            → 直接可用，不问
  T4 弱证据：只有泛别名命中 / 命中都在预告句、目录式列举、排版规范文件里 / 正文命中 ≤2 次
                                                      → 歧义项，合并成一张表一次性问
  T5 无命中                                          → 未学习，禁止出题（由 analyze 计算）

三条防误报规则（沿用 v1，仍然有效）：
  1) 语言过滤：C 笔记不匹配 Python 词条；
  2) 符号不单独成立：& * % << 这类符号别名必须与同词条的词名共现；
  3) 泛别名（weak_names）不能支撑 T1~T3。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_GLOSSARY = Path(__file__).resolve().parent.parent / "references" / "knowledge-glossary.json"
_ASCII = re.compile(r"^[\x00-\x7f]+$")
_SYMBOL_ONLY = re.compile(r"^[^0-9A-Za-z\u4e00-\u9fff]+$")
CJK = re.compile(r"[\u4e00-\u9fff]")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s")   # group(1) = 井号个数，即标题级别
BULLET_RE = re.compile(r"^\s*[-*+]\s+")
# 预告句：说明这个知识点「以后才学」，不能作为已学证据
PREVIEW_RE = re.compile(r"(后面(学到|会|再)|以后|将来|之后(会|再|学|讲)|下一(章|节|部分)|"
                        r"后期会|暂未|还没(学|讲|用|到)|待学|预习|先不|尚未)")
# 排版/风格类笔记：里面的代码片段是为了讲排版，不是讲语法
STYLE_FILE_RE = re.compile(r"(空格|风格|规范|排版|format|style|lint)", re.I)
GENERIC_NAMES = {"变量", "常量", "运算符", "循环", "函数", "分支", "数组", "字符串", "指针",
                 "调试", "算法", "输入", "输出", "编译", "对象", "模块", "异常", "字典", "列表"}
MAX_EVIDENCE_ALL = 60


def load(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else DEFAULT_GLOSSARY
    if not p.exists():
        raise FileNotFoundError(f"词库不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data["entries"] if isinstance(data, dict) else data
    out = []
    for e in entries:
        e = dict(e)
        e.setdefault("core", False)
        e.setdefault("prereq", [])
        e.setdefault("tags", [])
        e.setdefault("lang", "*")
        e.setdefault("weak_names", [])
        e.setdefault("allow_style_file", e["id"] == "gen_code_style")
        out.append(e)
    return out


def is_symbol(name: str) -> bool:
    return bool(_SYMBOL_ONLY.match(name))


def name_pattern(name: str) -> re.Pattern:
    """ASCII 名用词边界匹配（避免 int 命中 print），中文名用子串匹配。"""
    esc = re.escape(name)
    if _ASCII.match(name):
        return re.compile(r"(?<![0-9A-Za-z_])" + esc + r"(?![0-9A-Za-z_])", re.IGNORECASE)
    return re.compile(esc)


def allowed_langs(languages: dict, min_weight: float = 0.05):
    """由检测到的语言权重算出允许匹配的词条语言集合。

    检测不到任何具体语言时返回 None（= 不过滤）：纯文字笔记没有代码块，如果这时只允许 "*"，
    会把该语言的所有知识点都丢掉，造成「笔记明明讲过却被判未学」的假阴性。
    """
    concrete = {k: v for k, v in (languages or {}).items()
                if k not in {"text", "mermaid", "plaintext", ""}}
    if not concrete:
        return None                       # 由调用方用 --language 兜底
    total = sum(v for v in languages.values()) or 1
    allowed = {"*"}
    for lang, w in sorted(languages.items(), key=lambda kv: -kv[1]):
        if lang in {"text", "mermaid", "plaintext", ""}:
            continue
        if w / total >= min_weight or not (allowed - {"*"}):
            allowed.add("python" if lang in {"py", "python3"} else lang)
    return allowed


# 只有 1~4 级标题算「章节级系统讲解」；5~6 级标题通常是大章节下的子条目
# （例如 `###### 3.函数调用语句` 属于「语句分类」，不能证明系统讲过「函数」）
TOPIC_HEADING_MAX_LEVEL = 4


def line_flags(line: str, source: str, code_lines: set | None = None, lineno: int = 0) -> dict:
    """一行的上下文：标题级别、是否代码、是否预告句/目录列举、是否排版规范文件。"""
    stripped = line.strip()
    m = HEADING_RE.match(line)
    level = len(m.group(1)) if m else 0
    is_item = bool(BULLET_RE.match(line)) or bool(re.match(r"^\s*\d+[.、)]\s+", line))
    dun = stripped.count("、")
    # 目录式列举：条目里带破折号注解、或带顿号枚举、或极短的裸条目
    listing = (is_item and ("——" in stripped or dun >= 1)) or dun >= 3 or (is_item and len(stripped) <= 28)
    in_code = lineno in (code_lines or set())
    # 代码块里的注释行：命中它只能算「正文提及」，不能算「代码示例证据」
    code_comment = in_code and ("//" in line or "/*" in line or stripped.startswith("*"))
    return {"heading": level > 0, "heading_level": level,
            "topic_heading": 0 < level <= TOPIC_HEADING_MAX_LEVEL,
            "code": in_code, "code_comment": code_comment,
            "preview": bool(PREVIEW_RE.search(line)),
            "list": bool(listing),
            "style_file": bool(STYLE_FILE_RE.search(str(source)))}


def match_lines(entries: list[dict], lines: list[str], source: str,
                langs: set | None = None, max_evidence: int = 3,
                code_lines: set | None = None) -> list[dict]:
    """在一个文件的行序列上匹配知识点，返回带依据、命中类型与行级上下文的结果。"""
    found: dict[str, dict] = {}
    symbol_hits: dict[str, int] = {}
    compiled = []
    for e in entries:
        if langs is not None and e["lang"] not in langs:
            continue
        strong = [(n, name_pattern(n)) for n in e.get("names", [])
                  if n and not is_symbol(n)]
        strong_sym = [(n, name_pattern(n)) for n in e.get("names", []) if n and is_symbol(n)]
        weak = [(n, name_pattern(n)) for n in e.get("weak_names", []) if n]
        compiled.append((e, strong, strong_sym, weak))

    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        flags = None
        for e, strong, strong_sym, weak in compiled:
            hit, kind = None, None
            for name, pat in strong:
                if pat.search(line):
                    hit, kind = name, "strong"
                    break
            if hit is None:
                for name, pat in weak:
                    if pat.search(line):
                        hit, kind = name, "weak"
                        break
            if hit is None:
                # 符号别名：必须与同词条的词名在本文件共现，否则不算（v1 规则）
                for name, pat in strong_sym:
                    if pat.search(line):
                        symbol_hits[e["id"]] = symbol_hits.get(e["id"], 0) + 1
                        break
                continue
            if flags is None:
                flags = line_flags(line, source, code_lines, lineno)
            rec = found.setdefault(e["id"], {
                "id": e["id"], "kp": e["kp"], "lang": e["lang"], "core": e["core"],
                "prereq": list(e["prereq"]), "tags": list(e["tags"]),
                "allow_style_file": bool(e.get("allow_style_file")),
                "matched_names": [], "weak_matched_names": [], "count": 0, "strong_count": 0,
                "evidence": [], "evidence_all": [], "in_heading": False, "has_code": False,
                "strong_names": [], "confidence": "weak", "symbol_only_hits": 0,
            })
            rec["count"] += 1
            if kind == "strong":
                rec["strong_count"] += 1
                if hit not in rec["matched_names"]:
                    rec["matched_names"].append(hit)
                generic = hit in GENERIC_NAMES or (CJK.search(hit) is None and len(hit) <= 2)
                if not generic and hit not in rec["strong_names"]:
                    rec["strong_names"].append(hit)
                rec["confidence"] = "strong" if rec["strong_names"] else rec["confidence"]
            else:
                if hit not in rec["weak_matched_names"]:
                    rec["weak_matched_names"].append(hit)
            if flags["heading"]:
                rec["in_heading"] = True
            if flags["code"]:
                rec["has_code"] = True
            item = {"source": source, "line": lineno, "hit": hit, "kind": kind,
                    "snippet": line.strip()[:140], **flags}
            if len(rec["evidence_all"]) < MAX_EVIDENCE_ALL:
                rec["evidence_all"].append(item)
            if len(rec["evidence"]) < max_evidence and kind == "strong":
                rec["evidence"].append({"source": source, "line": lineno,
                                        "snippet": item["snippet"] + ("…" if len(line.strip()) > 140 else "")})
    for rec in found.values():
        if not rec["evidence"]:                       # 只有弱命中时也要给出依据
            for it in rec["evidence_all"][:3]:
                rec["evidence"].append({"source": it["source"], "line": it["line"],
                                        "snippet": it["snippet"][:140]})
        rec["symbol_only_hits"] = symbol_hits.get(rec["id"], 0)
        tier, reason = compute_tier(rec)
        rec["tier"], rec["tier_reason"] = tier, reason
    return list(found.values())


def compute_tier(rec: dict) -> tuple[int, str]:
    """把命中记录换算成 T1~T4（T5 = 完全没命中，由上层判定）。"""
    hits = rec.get("evidence_all") or []
    strong = [h for h in hits if h["kind"] == "strong"]
    allow_style = rec.get("allow_style_file", False)
    clean = [h for h in strong
             if not h["preview"] and not h["list"] and (allow_style or not h["style_file"])]
    topic_heads = [h for h in clean if h.get("topic_heading")]
    if topic_heads:
        head = topic_heads[0]
        return 1, (f'强别名「{head["hit"]}」命中 {head["heading_level"]} 级小标题'
                   f'（{Path(str(head["source"])).name}:{head["line"]}），属系统讲解')
    real_code = [h for h in clean if h["code"] and not h.get("code_comment")]
    if real_code:
        return 2, f"强别名在真实代码示例中命中 {len(real_code)} 次（非注释行）"
    prose = [h for h in clean if not h["code"]]
    if len(prose) >= 3:
        return 3, f"强别名在正文中明确提及 {len(prose)} 次"
    reasons = []
    if not strong:
        names = "、".join(rec.get("weak_matched_names", [])[:4])
        reasons.append(f"只有泛别名命中（{names}），没有专业词")
    elif not clean:
        why = []
        if any(h["preview"] for h in strong):
            why.append("预告句（『后面学到…』）")
        if any(h["list"] for h in strong):
            why.append("目录式列举")
        if any(h["style_file"] for h in strong) and not allow_style:
            why.append("排版规范类笔记")
        if any(h["heading"] and not h.get("topic_heading") for h in strong):
            why.append("只是大章节下的子条目（5~6 级标题）")
        reasons.append("强别名命中全部出现在 " + "、".join(why or ["不可靠位置"]))
    else:
        reasons.append(f"正文明确提及只有 {len(prose)} 次（<3），真实代码示例 0 次")
    return 4, "；".join(reasons)


def merge(all_found: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for chunk in all_found:
        for r in chunk:
            m = merged.get(r["id"])
            if m is None:
                merged[r["id"]] = dict(r)
                continue
            m["count"] += r["count"]
            m["strong_count"] += r["strong_count"]
            m["symbol_only_hits"] += r.get("symbol_only_hits", 0)
            m["in_heading"] = m["in_heading"] or r["in_heading"]
            m["has_code"] = m["has_code"] or r.get("has_code", False)
            m["allow_style_file"] = m["allow_style_file"] or r.get("allow_style_file", False)
            for key in ("matched_names", "weak_matched_names", "strong_names"):
                for n in r.get(key, []):
                    if n not in m[key]:
                        m[key].append(n)
            if m["strong_names"]:
                m["confidence"] = "strong"
            for ev in r["evidence"]:
                if len(m["evidence"]) < 5:
                    m["evidence"].append(ev)
            room = MAX_EVIDENCE_ALL - len(m["evidence_all"])
            if room > 0:
                m["evidence_all"].extend(r["evidence_all"][:room])
    for m in merged.values():
        tier, reason = compute_tier(m)
        m["tier"], m["tier_reason"] = tier, reason      # evidence_all 保留：analyze 要复核依据
    return sorted(merged.values(), key=lambda x: (x["tier"], -x["strong_count"], x["kp"]))


if __name__ == "__main__":
    ents = load()
    print(json.dumps({"entries": len(ents),
                      "with_weak_names": sum(1 for e in ents if e["weak_names"]),
                      "by_lang": {l: sum(1 for e in ents if e["lang"] == l)
                                  for l in sorted({e["lang"] for e in ents})}},
                     ensure_ascii=False, indent=2))
    sys.exit(0)
