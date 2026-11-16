#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进度分析：把「笔记解析结果 + 掌握度档案 + 已确认范围」合成结论，每一项都注明依据与证据等级。

分桶（对应 SKILL.md 的范围确认规则）：
  usable      T1~T3：笔记里有小标题系统讲解 / 真实代码示例 / 正文明确提及 ≥3 次
              → 默认视为「学习中/待检验」，**可直接出题，不逐项询问**
  ambiguous   T4：只有泛别名命中、或命中都在预告句/目录列举/排版笔记里、或正文提及 <3 次
              → **合并成一张表一次性问**；确认后写进学习档案
  not_learned T5：词库里属于该语言的核心知识点但笔记完全没出现（或被你判定为未学）
              → 禁止作为解题必要条件

范围确认只做一次：`confirmed_scope.notes_fingerprint` 与当前笔记指纹相同 → 直接沿用，不再问；
笔记有变化 → 只问**新增**的歧义项。

用法：
  python analyze.py --report notes-report.json --records records.json --out analysis.json --print
  python analyze.py --report ... --records ... --cache-dir .plc/cache     # 笔记与档案都没变则跳过
  python analyze.py --report ... --records ... --scope-table              # 只打印范围/歧义两张表
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossary as G          # noqa: E402
import mastery as M           # noqa: E402

TIER_LABEL = {1: "T1 小标题系统讲解", 2: "T2 真实代码示例", 3: "T3 正文明确提及≥3次",
              4: "T4 弱证据（需确认）", 5: "T5 未出现"}
TIER_STATE = {1: "待检验", 2: "待检验", 3: "学习中"}

# 常见典型错误的静态识别规则（不猜测，只做确定的模式匹配）
C_RULES = [
    ("assign_in_condition", r"\bif\s*\(\s*[A-Za-z_]\w*\s*=(?!=)", "high",
     "if 条件里写了单个 = （赋值），很可能应该是 ==", "把 = 改成 ==，或加括号明确意图"),
    ("scanf_missing_amp", r"\bscanf\s*\(\s*\"[^\"]*%[dific](?![^\"]*%s)[^\"]*\"\s*,\s*(?!&)[A-Za-z_]\w*\s*\)", "high",
     "scanf 的变量前缺少取地址符 &", '写成 scanf("%d", &a);'),
    ("void_main", r"\bvoid\s+main\s*\(", "medium", "void main 不是标准写法", "改成 int main(void) 并 return 0;"),
    ("gets_used", r"\bgets\s*\(", "high", "gets 已被 C11 移除，会缓冲区溢出", "改用 fgets(buf, sizeof buf, stdin)"),
    ("printf_no_stdio", None, "medium", "用了 printf 但没有 #include <stdio.h>", "在文件开头加上 #include <stdio.h>"),
]
PY_RULES = [
    ("assign_in_condition", r"\bif\s+[A-Za-z_]\w*\s*=(?!=)", "high",
     "if 条件里写了单个 = （Python 会直接语法错误）", "改成 =="),
    ("py2_print", r"^\s*print\s+\"|^\s*print\s+'", "low", "Python 2 风格 print 语句", "Python 3 用 print(...)"),
]


def scan_typical_errors(code_blocks: list[dict]) -> list[dict]:
    out = []
    for b in code_blocks:
        lang = (b.get("lang") or "").lower()
        code = b.get("code", "")
        rules = C_RULES if lang in {"c", "cpp"} else (PY_RULES if lang in {"python", "py"} else [])
        for name, pat, sev, why, fix in rules:
            if pat is None:
                if name == "printf_no_stdio" and "printf" in code and "#include <stdio.h>" not in code:
                    first = code.strip().splitlines()[0][:120] if code.strip() else ""
                    out.append({"rule": name, "severity": sev, "lang": lang, "source": b.get("source"),
                                "line": b.get("line"), "reason": why, "fix": fix, "snippet": first})
                continue
            for m in re.finditer(pat, code, re.M):
                out.append({"rule": name, "severity": sev, "lang": lang, "source": b.get("source"),
                            "line": b.get("line") + code[:m.start()].count("\n") + 1,
                            "reason": why, "fix": fix, "snippet": m.group(0)[:120]})
    return out


def _basis(k: dict) -> list[str]:
    out = [f'依据等级：{TIER_LABEL.get(k.get("tier", 5), "?")} —— {k.get("tier_reason", "")}']
    for ev in k.get("evidence", [])[:3]:
        out.append(f'笔记：{Path(str(ev["source"])).name}:{ev["line"]}「{ev["snippet"][:60]}」')
    return out


def analyze(report: dict, records: dict | None = None, glossary_path: str | None = None,
            confirmed: dict | None = None) -> dict:
    records = records or {"version": 1, "cold_start": True, "records": {}}
    confirmed = confirmed if confirmed is not None else M.confirmed_decisions(records)
    entries = G.load(glossary_path)
    langs = report.get("languages", {})
    allowed = set(report.get("allowed_glossary_langs") or (G.allowed_langs(langs) or
                                                           {e["lang"] for e in entries}))
    knowledge = report.get("knowledge", [])
    fp = report.get("fingerprint") or (report.get("cache") or {}).get("fingerprint") or ""

    # 写进档案：T1~T3 直接可用；T4 只有被学习者确认「已学」才可用
    M.from_note_knowledge(records, knowledge, confirmed=confirmed)
    recs = records["records"]

    usable, excluded_weak, learned, mastered, partial, learning = [], [], [], [], [], []
    ambiguous = []                      # v3：不再有需要学习者确认的歧义项
    for k in knowledge:
        kp, tier = k["kp"], k.get("tier", 4)
        rec = recs.get(kp)
        decision = confirmed.get(kp)
        item = {"kp": kp, "tier": tier, "tier_label": TIER_LABEL.get(tier, "?"),
                "tier_reason": k.get("tier_reason", ""),
                "state": rec["state"] if rec else "未学习",
                "estimated": rec.get("estimated", True) if rec else True,
                "decision": decision, "basis": _basis(k)}
        learned.append(item)
        if tier >= 4:
            # 默认自动锁定：弱证据（T4）一律按「未学」排除，不进题目，也不询问学习者。
            # 只有学习者显式说「改为已学」（records.confirmed_scope 里 decision=learned）才纳入。
            if decision == "learned":
                pass                      # 已被学习者提升为已学 -> 继续走可用清单
            else:
                item["excluded_reason"] = ("弱证据（T4）默认按未学排除"
                                           if k.get("tier_reason") else "弱证据默认排除")
                excluded_weak.append(item)
                continue
        if rec is None:
            continue
        if rec["state_index"] >= M.IDX["基本掌握"]:
            mastered.append(item)
        elif rec.get("practice_evidence"):
            partial.append(item)
        else:
            learning.append(item)
        usable.append(item)

    decided_not_learned = [kp for kp, d in confirmed.items() if d == "not_learned"]
    core = {e["kp"] for e in entries if e.get("core") and e["lang"] in allowed}
    not_learned = []
    # 完全未学 = 词库核心知识点但笔记里没有；T4 弱证据单列在 excluded_weak，不重复计入
    for kp in sorted(core - {k["kp"] for k in knowledge}):
        why = f"笔记中未出现（词库核心知识点，lang∈{sorted(allowed)}）"
        not_learned.append({"kp": kp, "tier": 5, "state": "未学习", "estimated": False,
                            "basis": [f"{why}；不得作为解题必要条件"]})

    typical = scan_typical_errors(report.get("code_blocks", []))
    for p in report.get("pitfalls", [])[:20]:
        typical.append({"rule": "note_pitfall", "severity": "info", "lang": None,
                        "source": p["source"], "line": p["line"], "reason": "笔记中标注的注意点",
                        "fix": "", "snippet": p["text"][:160]})

    notes_unchanged = M.confirmed_scope_is_current(records, fp)
    scope_current = True               # 范围自动锁定，无需人工确认
    result = {
        "version": 2, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "notes_fingerprint": fp,
        "cold_start": records.get("cold_start", True),
        "languages": langs,
        "learned": learned, "mastered": mastered, "partial": partial,
        "learning": learning, "not_learned": not_learned,
        "usable": usable, "ambiguous": ambiguous, "excluded_weak": excluded_weak,
        "excluded_weak_kps": sorted(x["kp"] for x in excluded_weak),
        "usable_kps": sorted(x["kp"] for x in usable),
        "ambiguous_kps": sorted(x["kp"] for x in ambiguous),
        "tier_summary": {f"T{t}": sorted(x["kp"] for x in learned if x["tier"] == t) for t in (1, 2, 3, 4)},
        "scope_current": scope_current, "notes_unchanged": notes_unchanged,
        "needs_confirmation": [],       # v3：永不要求确认
        "scope_table": M.describe(records),
        "code_examples": [{"source": b["source"], "line": b["line"], "lang": b["lang"],
                           "preview": "\n".join(b["code"].splitlines()[:12])}
                          for b in report.get("code_blocks", [])[:15]],
        "code_examples_total": len(report.get("code_blocks", [])),
        "typical_errors": typical,
        "images_need_vision": [i["path"] for i in report.get("image_sources", [])],
        "degraded_inputs": report.get("degraded", []),
        "counts": {"learned": len(learned), "mastered": len(mastered), "partial": len(partial),
                   "learning": len(learning), "not_learned": len(not_learned),
                   "usable": len(usable), "ambiguous": 0,
                   "excluded_weak": len(excluded_weak)},
        "warnings": list(report.get("warnings", [])),
        "cache": report.get("cache"),
    }
    if result["cold_start"]:
        result["warnings"].append(
            "冷启动：没有历史练习记录，以上状态只是**初始估计**（笔记出现过≠已掌握）。"
            "第一次练习难度偏保守，掌握度以练习结果为准。")
    if not result["mastered"]:
        result["warnings"].append("「已掌握」为空是正常的：该结论只能由练习表现得出，笔记不能直接判定为已掌握。")
    if excluded_weak:
        result["warnings"].append(
            f'弱证据（T4）默认按未学排除，共 {len(excluded_weak)} 个；说「改为已学」可在下一轮纳入。')
    return result


def ambiguous_table(a: dict) -> str:
    """v3：不再询问；这里只给一行「按未学排除」的说明。"""
    kps = a.get("excluded_weak_kps") or []
    return ("以下按未学排除：" + "、".join(kps)) if kps else "（无弱证据项被排除）"


def summary_lines(a: dict) -> list:
    """出题文档开头的 ≤5 行摘要。"""
    c = a.get("counts", {})
    lines = [f'可用知识点（T1~T3）：{c.get("usable", 0)} 个；弱证据按未学排除：{c.get("excluded_weak", 0)} 个；完全未学：{c.get("not_learned", 0)} 个。',
             f'已掌握（练习确认）：{c.get("mastered", 0)} 个；未完全掌握：{c.get("partial", 0)} 个；正在学：{c.get("learning", 0)} 个。',
             ("冷启动：以上为初始估计，笔记出现过 ≠ 已掌握。" if a.get("cold_start")
              else "已有练习记录，状态以练习表现为准。"),
             (f'本次注释：{len(a.get("excluded_weak_kps") or [])} 个弱证据项按未学排除。'
              if a.get("excluded_weak_kps") else "本次没有弱证据项被排除。")]
    return lines[:5]


def human_summary(a: dict) -> str:
    lines = ["## 进度分析（每一项都附依据）", ""]
    lines.append(f'- 涉及语言：{", ".join(f"{k}({v})" for k, v in a["languages"].items()) or "未检测到"}')
    lines.append(f'- 是否冷启动：{"是（以下为初始估计）" if a["cold_start"] else "否（已有练习记录）"}')
    lines.append(f'- 范围确认状态：{"无需询问（沿用档案 / 无歧义项）" if a["scope_current"] else "需要一次性确认（见下方歧义表）"}'
                 f'；笔记指纹未变={a.get("notes_unchanged")}')
    t = a["tier_summary"]
    lines.append(f'- 证据分级：T1 {len(t.get("T1", []))} 个｜T2 {len(t.get("T2", []))} 个｜'
                 f'T3 {len(t.get("T3", []))} 个｜T4 {len(t.get("T4", []))} 个（歧义）｜T5 {a["counts"]["not_learned"]} 个（未学）')
    lines.append(f'- **可直接出题（T1~T3，共 {a["counts"]["usable"]} 个，不逐项询问）**：'
                 + "、".join(f'[[{x}]]' for x in a["usable_kps"]))
    for key, title in [("mastered", "已掌握（练习确认）"), ("partial", "未完全掌握"),
                       ("learning", "正在学"), ("not_learned", "未学（禁止出题）")]:
        items = a[key]
        lines += ["", f"### {title}：{len(items)} 个"]
        lines.append("、".join(f'[[{it["kp"]}]]' for it in items) or "- （无）")
    lines += ["", f"### 代码示例：{a['code_examples_total']} 段",
              f"### 典型错误 / 注意点：{len(a['typical_errors'])} 条"]
    for x in a["typical_errors"][:10]:
        lines.append(f'- [{x["severity"]}] {x["reason"]}（{Path(str(x["source"])).name}:{x["line"]}）')
    lines += ["", "### 需要你一次性确认的歧义项（T4）", ambiguous_table(a)]
    lines += ["", "### 知识点掌握表（出题范围以此为准）", a["scope_table"]]
    for w in a["warnings"]:
        lines.append(f"> ⚠️ {w}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 缓存
def _practice_signature(records: dict) -> dict:
    """只取「练习证据」部分：笔记派生的状态由 analyze 自己重算，不能进缓存键，
    否则第一次 analyze 写入档案后键就变了，缓存永远不命中。"""
    out = {}
    for kp, r in (records.get("records") or {}).items():
        sig = {"practice": r.get("practice_evidence") or [],
               "correct": r.get("correct_questions") or [],
               "wrong": r.get("wrong_questions") or [],
               "sessions": r.get("sessions") or [],
               "combined": bool(r.get("combined_correct"))}
        # 没有任何练习证据的知识点不参与键：analyze 自己会把这些字段写成空列表，
        # 如果计入「有没有 key」就会导致缓存永不命中
        if sig["practice"] or sig["correct"] or sig["wrong"] or sig["sessions"] or sig["combined"]:
            out[kp] = sig
    return out


def analysis_fingerprint(report: dict, records: dict, glossary_path: str | None = None) -> str:
    h = hashlib.sha256()
    h.update(str((report.get("cache") or {}).get("fingerprint") or report.get("fingerprint") or
                 json.dumps(report.get("knowledge", []), ensure_ascii=False, sort_keys=True)).encode())
    h.update(json.dumps(_practice_signature(records), ensure_ascii=False, sort_keys=True).encode())
    scope = records.get("confirmed_scope") or {}
    h.update(json.dumps({k: (v or {}).get("decision") if isinstance(v, dict) else v
                         for k, v in (scope.get("items") or {}).items()},
                        ensure_ascii=False, sort_keys=True).encode())
    h.update(str(scope.get("notes_fingerprint", "")).encode())
    g = Path(glossary_path) if glossary_path else G.DEFAULT_GLOSSARY
    if g.exists():
        h.update(g.read_bytes())
    return h.hexdigest()


def analyze_cached(report: dict, records: dict, glossary_path: str | None = None,
                   cache_dir: str | Path | None = None) -> dict:
    if not cache_dir:
        res = analyze(report, records, glossary_path)
        res["cache"] = {"hit": False, "path": None}
        return res
    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    fp = analysis_fingerprint(report, records, glossary_path)
    cfile = cdir / f"analysis-{fp[:16]}.json"
    if cfile.exists():
        try:
            cached = json.loads(cfile.read_text(encoding="utf-8"))
            cached["cache"] = {"hit": True, "path": str(cfile), "fingerprint": fp}
            return cached
        except (json.JSONDecodeError, OSError):
            pass
    res = analyze(report, records, glossary_path)
    payload = {k: v for k, v in res.items() if k != "cache"}
    cfile.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    res["cache"] = {"hit": False, "path": str(cfile), "fingerprint": fp}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 进度分析")
    ap.add_argument("--report", required=True)
    ap.add_argument("--records")
    ap.add_argument("--glossary")
    ap.add_argument("--answers", help="学习者对歧义表的判定 JSON：{知识点: learned|not_learned}")
    ap.add_argument("--cache-dir")
    ap.add_argument("--out")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--scope-table", action="store_true", help="只打印可直接出题清单 + 歧义表")
    a = ap.parse_args(argv)
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    records = M.load(a.records)
    confirmed = M.confirmed_decisions(records)
    if a.answers:
        ans = json.loads(Path(a.answers).read_text(encoding="utf-8"))
        ans = ans.get("decisions", ans) if isinstance(ans, dict) else {}
        confirmed = {**confirmed, **{k: v for k, v in ans.items() if v in ("learned", "not_learned")}}
        M.set_confirmed_scope(records, report.get("fingerprint") or
                              (report.get("cache") or {}).get("fingerprint") or "",
                              {k: {"decision": v} for k, v in confirmed.items()})
    t0 = time.perf_counter()
    res = analyze_cached(report, records, a.glossary, a.cache_dir)
    res["elapsed_seconds"] = round(time.perf_counter() - t0, 4)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
    if a.records:
        M.save(a.records, records)
    if a.scope_table:
        print(f'## 可直接出题（T1~T3，{len(res["usable_kps"])} 个，无需询问）')
        print("、".join(f'[[{x}]]' for x in res["usable_kps"]))
        print(f'\n## 需要一次性确认（T4，{len(res["ambiguous_kps"])} 个）')
        print(ambiguous_table(res))
        print(f'\n范围是否已确认且沿用：{res["scope_current"]}')
    elif a.do_print or not a.out:
        print(human_summary(res))
    else:
        print(json.dumps({"ok": True, "out": a.out, "counts": res["counts"],
                          "cold_start": res["cold_start"], "scope_current": res["scope_current"],
                          "needs_confirmation": res["needs_confirmation"],
                          "cache_hit": (res.get("cache") or {}).get("hit"),
                          "elapsed_seconds": res["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
