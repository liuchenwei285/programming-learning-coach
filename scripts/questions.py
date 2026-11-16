#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出题：题型系统 + 组卷配比 + 不超纲检查 + 难度排序 + 覆盖率 + 薄弱点优先 + 查重 + 答案隔离。

题型（对齐范例《10月18日练习》）：
  output_prediction 输出预测题 / trace 程序追踪题 / fix_bug 改错题 /
  code_analysis 代码分析题 / concept 概念辨析题 / programming 编程题 / comprehensive 综合挑战题

硬规则（违规非零退出）：
* 只能用「学习中」及以上的知识点；未学习知识点不得出现在 knowledge_points / required_knowledge_points。
* 每题必填字段随题型变化（读程序题必须有 code_to_read；编程题必须有输入输出规格与测试用例）。
* 标准答案 / 期望答案 / 测试用例写入 .private/<sid>/answers.private.json；公开文件不含任何答案。
* 默认按难度**递增**排列，综合挑战题放最后（与范例一致）；需要难度递减时用 --order desc。
* 出题后阶段停在 awaiting_submission，等学习者提交。

用法：
  python questions.py build --questions q.json --records rec.json --root .plc --sid s1 [--order asc]
  python questions.py check --questions q.json --records rec.json      # 只校验，不写文件
  python questions.py types                                          # 打印题型规格表
  python questions.py show   --root .plc --sid s1                      # 只打印公开题目
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery as M            # noqa: E402
import session_store as S      # noqa: E402

# --------------------------------------------------------------------------- 题型规格
QUESTION_TYPES = {
    "output_prediction": {"cn": "输出预测题", "answer_kind": "text", "sandbox": False, "auto_judge": True,
                          "need_public": ["code_to_read"], "need_private": ["expected_answer"],
                          "desc": "给一段程序，不运行，逐行写出输出并解释原因"},
    "trace": {"cn": "程序追踪题", "answer_kind": "text", "sandbox": False, "auto_judge": True,
              "need_public": ["code_to_read"], "need_private": ["expected_answer"],
              "desc": "逐步追踪变量取值（自增自减、循环轮次），列出每一步的值"},
    "fix_bug": {"cn": "改错题", "answer_kind": "code", "sandbox": True, "auto_judge": True,
                "need_public": ["code_to_read"], "need_private": ["reference_solution", "tests"],
                "desc": "给出有错误的程序，找出所有问题并提交修正后的完整程序"},
    "code_analysis": {"cn": "代码分析题", "answer_kind": "text", "sandbox": False, "auto_judge": False,
                      "need_public": ["code_to_read", "requirements"],
                      "need_private": ["expected_answer"],
                      "desc": "判断语句是否合法、定位编译错误、分类语句结构、解释概念关系"},
    "concept": {"cn": "概念辨析题", "answer_kind": "text", "sandbox": False, "auto_judge": False,
                "need_public": ["requirements"], "need_private": ["expected_answer"],
                "desc": "辨析易混概念（如 sizeof 与 strlen、'0' 与 0 与 '\\0'）"},
    "programming": {"cn": "编程题", "answer_kind": "code", "sandbox": True, "auto_judge": True,
                    "need_public": ["input_format", "output_format", "examples"],
                    "need_private": ["reference_solution", "tests"],
                    "desc": "按输入输出规格写完整程序，可用沙箱跑测试用例"},
    "comprehensive": {"cn": "综合挑战题", "answer_kind": "code", "sandbox": True, "auto_judge": True,
                      "need_public": ["input_format", "output_format", "examples"],
                      "need_private": ["reference_solution", "tests"],
                      "desc": "多功能综合程序 + 自检三问，放在最后一题"},
}
# 10 题卷的推荐配比（min, max）
PAPER_RECIPE = {
    "output_prediction": (1, 3), "trace": (0, 2), "fix_bug": (0, 2),
    "code_analysis": (0, 2), "concept": (0, 2), "programming": (2, 5),
    "comprehensive": (1, 1),
}
DEFAULT_PAPER_SIZE = 10          # 题量固定：不等于 10 直接报错，不许自行增减
WEAK_RATIO_TARGET = 0.4          # 有薄弱点时，至少 40% 的题目要覆盖到薄弱点
SIMILARITY_LIMIT = 0.80          # 题干相似度（字符 3-gram Jaccard）超过此值视为机械重复
ANGLE_COLLISION_LIMIT = 3        # 同一「知识点组合 + 出题角度」最多重复几次

COMMON_REQUIRED = ["id", "title", "question_type", "difficulty", "est_minutes",
                   "knowledge_points", "required_knowledge_points"]
OPTIONAL_FIELDS = ["language", "tags", "is_combined", "weak_point_focus", "function_signature",
                   "starter_code", "hints", "better_approach_kps", "requirements", "constraints",
                   "test_input", "example_line", "hint_line", "challenge", "related_notes",
                   "self_check_questions", "edge_cases", "answer_kind", "angle"]
HINT_LEVELS = {"L1", "L2", "L3"}


class QuestionError(RuntimeError):
    pass


def fingerprint(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE).lower()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]


def question_fingerprint(q: dict) -> str:
    """查重只看题面（含待读代码）与输入输出规格，不看标题 —— 换标题的同一道题仍算重复。"""
    return fingerprint(q.get("stem", q.get("description", "")) + q.get("code_to_read", "")
                       + q.get("input_format", "") + q.get("output_format", ""))


def _shingles(text: str, n: int = 3) -> set:
    s = _norm_code(text)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def similarity(a: str, b: str) -> float:
    """字符 3-gram 的 Jaccard 相似度：抓「换了几个数字/换了个说法」的机械重复。"""
    A, B = _shingles(a), _shingles(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def question_text(q: dict) -> str:
    return (stem_of(q) + "\n" + (q.get("code_to_read") or "") + "\n"
            + (q.get("input_format") or "") + "\n" + (q.get("output_format") or ""))


def similar_pairs(qs: list[dict], limit: float = SIMILARITY_LIMIT) -> list[dict]:
    out = []
    for i in range(len(qs)):
        for j in range(i + 1, len(qs)):
            sim = similarity(question_text(qs[i]), question_text(qs[j]))
            if sim >= limit:
                out.append({"a": qs[i].get("id"), "b": qs[j].get("id"), "similarity": round(sim, 3)})
    return out


def similar_to_history(qs: list[dict], history: list[dict], limit: float = SIMILARITY_LIMIT) -> list[dict]:
    out = []
    for q in qs:
        for h in history:
            sim = similarity(question_text(q), question_text(h))
            if sim >= limit:
                out.append({"id": q.get("id"), "history": h.get("id") or h.get("title"),
                            "similarity": round(sim, 3)})
    return out


def answer_kind(q: dict) -> str:
    t = QUESTION_TYPES.get(q.get("question_type", ""))
    return q.get("answer_kind") or (t["answer_kind"] if t else "code")


def judge_mode(q: dict) -> str:
    """auto = 机器可判定（沙箱跑代码 / 输出逐行对照）；manual = 教练对照评分要点判定。

    概念辨析题、代码分析题的答案是散文，逐行精确比对会把正确的表述判错，
    所以这两种题型默认 manual，由教练对照 answer_points 给分。
    """
    if q.get("answer_check") in {"auto", "manual"}:
        return q["answer_check"]
    t = QUESTION_TYPES.get(q.get("question_type", ""))
    if t is None:
        return "auto"
    return "auto" if t.get("auto_judge", True) else "manual"


def stem_of(q: dict) -> str:
    return q.get("stem") or q.get("description") or ""


# --------------------------------------------------------------------------- 校验
def scope_violations(q: dict, records: dict) -> list[str]:
    out = []
    for kp in q.get("required_knowledge_points", []) or []:
        if M.state_of(records, kp) == "未学习":
            out.append(f'超纲：{q.get("id")} 的必需知识点「{kp}」状态为「未学习」，不能作为解题必要条件')
    for kp in q.get("knowledge_points", []) or []:
        if M.state_of(records, kp) == "未学习":
            out.append(f'超纲：{q.get("id")} 涉及知识点「{kp}」状态为「未学习」（连涉及都不允许）')
    return out


def validate_question(q: dict, records: dict, ids: set | None = None) -> list[str]:
    errs = []
    qid = q.get("id", "?")
    for f in COMMON_REQUIRED:
        if f not in q or q[f] in (None, "", []):
            errs.append(f"{qid}：缺少必填字段 {f}")
    if not stem_of(q):
        errs.append(f"{qid}：缺少题干 stem（或 description）")
    qtype = q.get("question_type")
    spec = QUESTION_TYPES.get(qtype or "")
    if not spec:
        errs.append(f'{qid}：question_type 未知（{qtype!r}），可选：{", ".join(QUESTION_TYPES)}')
        spec = {"cn": "?", "answer_kind": "code", "need_public": [], "need_private": [], "sandbox": True}
    else:
        if q.get("answer_kind") and q["answer_kind"] != spec["answer_kind"]:
            errs.append(f'{qid}：answer_kind 与题型不符（{qtype} 应为 {spec["answer_kind"]}）')
        for f in spec["need_public"]:
            if not q.get(f):
                errs.append(f"{qid}：{spec['cn']} 必须提供 {f}")
        for f in spec["need_private"]:
            if not q.get(f):
                errs.append(f"{qid}：{spec['cn']} 必须提供私有字段 {f}（标准答案/期望答案）")
    d = q.get("difficulty")
    if not isinstance(d, int) or not 1 <= d <= 5:
        errs.append(f"{qid}：difficulty 必须是 1..5 的整数，当前 {d!r}")
    for i, e in enumerate(q.get("examples") or [], 1):
        if not isinstance(e, dict) or "input" not in e or "output" not in e:
            errs.append(f"{qid}：第 {i} 个示例必须同时含 input 与 output")
    if spec.get("sandbox") and not (q.get("examples") or []):
        errs.append(f"{qid}：{spec['cn']} 至少要有一个示例（含输入与输出）")
    if spec.get("sandbox") and not (q.get("tests") or []):
        errs.append(f"{qid}：{spec['cn']} 必须有测试用例 tests（用于沙箱判定）")
    if q.get("answer_check") is not None and q.get("answer_check") not in {"auto", "manual"}:
        errs.append(f'{qid}：answer_check 只能是 "auto" 或 "manual"')
    if spec.get("answer_kind") == "text" and not spec.get("auto_judge", True) \
            and not (q.get("answer_points") or []):
        errs.append(f"{qid}：{spec['cn']} 由教练对照评分要点判定，必须提供 answer_points（评分要点）")
    if qtype == "comprehensive" and not (q.get("self_check_questions") or []):
        errs.append(f"{qid}：综合挑战题必须给 self_check_questions（自检三问）")
    rn = q.get("related_notes")
    if rn is not None:
        if not isinstance(rn, list) or not rn:
            errs.append(f"{qid}：related_notes 必须是非空列表")
        else:
            for item in rn:
                if isinstance(item, dict) and not item.get("target"):
                    errs.append(f"{qid}：related_notes 每项需要 target（笔记名）")
    hints = q.get("hints")
    if hints is not None:
        if not isinstance(hints, dict) or not set(hints) <= HINT_LEVELS:
            errs.append(f"{qid}：hints 只能是 L1/L2/L3，且 L3 只能给伪代码")
        l3 = str(hints.get("L3", ""))
        if l3 and re.search(r"(#include|def\s+\w+\(|int\s+main|;\s*$)", l3, re.M) and len(l3) > 400:
            errs.append(f"{qid}：L3 提示过长且像完整代码，违反「永远不给完整答案」")
    if ids is not None and qid in ids:
        errs.append(f"题号重复：{qid}")
    if not q.get("required_knowledge_points"):
        errs.append(f"{qid}：required_knowledge_points 不能为空（无法做不超纲检查）")
    for kp in q.get("better_approach_kps", []) or []:
        if M.state_of(records, kp) == "未学习":
            errs.append(f'{qid}：「更好写法」用到未学知识点「{kp}」，超纲，必须换掉')
    errs += scope_violations(q, records)
    return errs


def sort_questions(qs: list[dict], order: str = "asc") -> list[dict]:
    """asc：难度递增（范例顺序，综合挑战在最后）；desc：难度从高到低。"""
    key = lambda q: (int(q.get("difficulty", 0)), int(q.get("est_minutes", 0)), str(q.get("id")))
    return sorted(qs, key=key, reverse=(order == "desc"))


def check_paper(qs: list[dict], records: dict, *, size: int = DEFAULT_PAPER_SIZE,
                order: str = "asc", history: list[dict] | None = None) -> dict:
    """组卷检查：题量=10、题型配比、难度单调、综合挑战收尾、薄弱点比例、近似重复、知识点预算。"""
    history = history or []
    errors, warnings = [], []
    counts: dict[str, int] = {}
    for q in qs:
        counts[q.get("question_type", "?")] = counts.get(q.get("question_type", "?"), 0) + 1
    if len(qs) != size:
        errors.append(f"题量必须正好 {size} 题（当前 {len(qs)} 题）。"
                      f"不得因为覆盖率或范围小自行增减；知识点不够时换角度出题，并提前告知学习者。")
    for t, (lo, hi) in PAPER_RECIPE.items():
        c = counts.get(t, 0)
        if c > hi:
            errors.append(f"题型配比超限：{QUESTION_TYPES[t]['cn']} {c} 题（建议 {lo}~{hi}）")
        elif c < lo:
            warnings.append(f"题型配比不足：{QUESTION_TYPES[t]['cn']} {c} 题（建议 {lo}~{hi}）")
    for t in counts:
        if t not in QUESTION_TYPES:
            errors.append(f"未知题型：{t}")
    diffs = [int(q.get("difficulty", 0)) for q in qs]
    if order == "asc" and diffs != sorted(diffs):
        errors.append(f"难度必须递增（第 1 题最易 → 最后 1 题最难）：{diffs}")
    if order == "desc" and diffs != sorted(diffs, reverse=True):
        errors.append(f"难度必须递减：{diffs}")
    comp_idx = [i for i, q in enumerate(qs) if q.get("question_type") == "comprehensive"]
    if comp_idx and len(qs) >= 5 and comp_idx != [len(qs) - 1]:
        errors.append(f"综合挑战题必须放在最后（当前在第 {[i+1 for i in comp_idx]} 题）")
    if len(qs) >= 5 and counts.get("output_prediction", 0) + counts.get("trace", 0) == 0:
        warnings.append("整卷没有「读程序写输出 / 追踪」类题目：这类题最能暴露概念误解，建议至少 2 题")
    if not any(answer_kind(q) == "text" for q in qs):
        warnings.append("整卷都是编程题：建议混入文本作答题（输出预测/追踪/分析/辨析），降低书写负担")
    missing_notes = [q["id"] for q in qs if not q.get("related_notes")]
    if missing_notes:
        warnings.append(f"这些题没有 related_notes（应指向精读篇目或笔记）：{missing_notes}")
    weak = [w["kp"] for w in M.weak_points(records)]
    used = {kp for q in qs for kp in (q.get("knowledge_points") or [])}
    miss_weak = [w for w in weak if w not in used]
    weak_questions = [q["id"] for q in qs
                      if set(q.get("knowledge_points") or []) & set(weak)]
    weak_ratio = round(len(weak_questions) / len(qs), 3) if qs else 0.0
    if weak:
        if not weak_questions:
            errors.append(f"历史错题涉及的薄弱点一个都没安排：{weak}（必须适当增加比例）")
        elif weak_ratio < WEAK_RATIO_TARGET:
            warnings.append(f"薄弱点覆盖比例 {weak_ratio:.0%} 低于目标 {WEAK_RATIO_TARGET:.0%}"
                            f"（{len(weak_questions)}/{len(qs)} 题）；未覆盖的薄弱点：{miss_weak}")
    # 近似重复：同一份卷子里 + 与历史题目
    dups = similar_pairs(qs)
    if dups:
        errors.append(f"题目机械重复（题干相似度 ≥ {SIMILARITY_LIMIT}）：{dups}；请换场景/换边界/换输入输出方式")
    hist_dups = similar_to_history(qs, history)
    if hist_dups:
        errors.append(f"与历史题目机械重复（相似度 ≥ {SIMILARITY_LIMIT}）：{hist_dups}")
    # 知识点预算：可用知识点太少时，必须在出题前告知学习者「会有角度重叠」
    in_scope = [kp for kp, r in records["records"].items() if r["state_index"] >= M.SCOPED_MIN_INDEX]
    angles: dict[tuple, list] = {}
    for q in qs:
        key = (tuple(sorted(q.get("knowledge_points") or [])), q.get("angle") or "")
        angles.setdefault(key, []).append(q["id"])
    collisions = {f'{"+".join(k[0]) or "无知识点"}｜角度={k[1] or "未标注"}': v
                  for k, v in angles.items() if len(v) >= ANGLE_COLLISION_LIMIT}
    scope_scarce = len(in_scope) < len(qs)
    if scope_scarce:
        warnings.append(f"可用知识点偏少（{len(in_scope)} 个 < {len(qs)} 题）："
                        f"10 题会有角度上的重叠。**出题前必须把这句话告诉学习者**，"
                        f"不得靠降低标准或超纲来凑题量。")
    if collisions:
        warnings.append(f"同一「知识点组合 + 角度」重复 {ANGLE_COLLISION_LIMIT} 次以上：{collisions}；"
                        f"建议换场景 / 换边界条件 / 换输入输出方式")
    if len(qs) > 1 and not any(q.get("angle") for q in qs):
        warnings.append("题目没有标注 angle（出题角度）：无法检测角度重叠，建议每题写一句场景说明")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "type_counts": counts,
            "recipe": PAPER_RECIPE, "size": len(qs), "order": order,
            "answer_kinds": {q["id"]: answer_kind(q) for q in qs},
            "weak_points": weak, "weak_covered": sorted(set(weak) & used), "weak_missing": miss_weak,
            "weak_question_ids": weak_questions, "weak_ratio": weak_ratio,
            "weak_ratio_target": WEAK_RATIO_TARGET,
            "in_scope_kp_count": len(in_scope), "scope_scarce": scope_scarce,
            "angle_collisions": collisions,
            "similar_pairs": dups, "similar_to_history": hist_dups}


def coverage(qs: list[dict], records: dict, reasons: dict | None = None) -> dict:
    in_scope = [kp for kp, r in records["records"].items() if r["state_index"] >= M.SCOPED_MIN_INDEX]
    used = {kp for q in qs for kp in (q.get("knowledge_points") or [])}
    covered = sorted(used & set(in_scope))
    uncovered = sorted(set(in_scope) - used)
    reasons = reasons or {}
    return {"in_scope_total": len(in_scope), "covered": covered,
            "coverage_rate": round(len(covered) / len(in_scope), 3) if in_scope else 1.0,
            "uncovered": [{"kp": kp,
                           "reason": reasons.get(kp, "本次未覆盖：题量/题型配比限制，留待下次练习"),
                           "reason_source": "provided" if kp in reasons else "auto"} for kp in uncovered]}


def weak_point_plan(qs: list[dict], records: dict, history: list[dict]) -> dict:
    weak = M.weak_points(records)
    weak_kps = [w["kp"] for w in weak]
    used = {kp for q in qs for kp in (q.get("knowledge_points") or [])}
    hist_fp = {question_fingerprint(h) for h in history if stem_of(h)}
    dup = [q["id"] for q in qs if question_fingerprint(q) in hist_fp]
    return {"weak_points": weak_kps, "weak_covered": sorted(set(weak_kps) & used),
            "weak_missing": sorted(set(weak_kps) - used), "duplicated_with_history": dup}


# --------------------------------------------------------------------------- 泄题扫描
def _norm_code(s: str) -> str:
    """归一化到只剩字母数字与汉字：转义、空白、标点都不能用来藏答案。"""
    s = re.sub(r"\\[nrtfbv/\\'\"u]", " ", s or "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", s).lower()


def _all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v)


# 任何两个入门 C 程序都共享的样板行：不能作为「泄漏证据」，否则会误报
BOILERPLATE = {"includestdioh", "includestdlibh", "includestringh", "includefalse", "includeiostream",
               "intmainvoid", "intmain", "return0", "stdioh", "intmainintargccharargv",
               "includestdiohintmainvoid", "defmain", "ifnamemain", "print"}


def _norm_ws(s: str) -> str:
    """只折叠空白，保留标点：用来做「原样代码行」匹配。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


def _leak_probes(answer: str, min_len: int = 20) -> list[tuple[str, str]]:
    """从一段答案里提取「有辨识度」的探针。

    两类探针，避免误报：
    * ("norm", 整体归一化串)：抓「整段答案被贴进题面」。
    * ("raw", 单行原样串，保留标点)：抓「某一行代码被原样贴进题面」。
      L3 提示是自然语言伪代码（没有分号、花括号、空格布局不同），不会与真实代码行原样重合，
      所以合法的提示不会被误判成泄漏。
    """
    probes = []
    full = _norm_code(answer)
    if len(full) >= min_len + 8:
        probes.append(("norm", full))
    for line in (answer or "").splitlines():
        raw = _norm_ws(line)
        if len(raw) >= min_len and _norm_code(line) not in BOILERPLATE:
            probes.append(("raw", raw))
    seen, out = set(), []
    for kind, val in probes:
        if (kind, val) not in seen:
            seen.add((kind, val))
            out.append((kind, val))
    return out


def leak_scan(public_obj, private_obj: dict) -> list[dict]:
    """机械检查公开内容里是否混入了标准答案 / 期望答案 / 隐藏测试用例的期望输出。

    判定方式：把公开对象里所有字符串原文拼起来归一化，再看答案的「整体串」或「有辨识度的行」
    是否被包含。样板行（#include <stdio.h>、int main(void)、return 0）单独出现不算泄漏，
    否则任何两道入门 C 题都会互相误报。
    """
    public_strings = list(_all_strings(public_obj))
    norm_text = _norm_code("\n".join(public_strings))
    raw_text = "\n".join(public_strings)
    raw_ws_text = "\n".join(_norm_ws(x) for x in public_strings)
    allowed_outputs = set()

    def collect(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in {"output", "expected_output"} and isinstance(v, str):
                    allowed_outputs.add(_norm_code(v))
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)

    collect(public_obj)
    # 公开的待读代码（改错题/分析题）里本来就有的行，不能算「泄漏」：
    # 修复后的程序必然与题干共享未改动的行，真正要防的是「修复后的新行」被提前贴出来
    given_lines = {}

    def collect_given(o, qid=None):
        if isinstance(o, dict):
            if o.get("id"):
                qid = o["id"]
            for k, v in o.items():
                if k == "code_to_read" and isinstance(v, str):
                    given_lines.setdefault(qid, set()).update(
                        _norm_ws(line) for line in v.splitlines() if _norm_ws(line))
                collect_given(v, qid)
        elif isinstance(o, list):
            for v in o:
                collect_given(v, qid)

    collect_given(public_obj)
    leaks = []
    for qid, priv in (private_obj or {}).items():
        given = given_lines.get(qid, set())
        for key in ("reference_solution", "expected_answer"):
            val = priv.get(key)
            if not isinstance(val, str):
                continue
            for pkind, probe in _leak_probes(val):
                if pkind == "raw" and probe in given:
                    continue                  # 这一行本来就在公开的待读代码里
                hit = (probe in norm_text) if pkind == "norm" else (probe in raw_ws_text)
                if hit:
                    leaks.append({"qid": qid, "kind": key, "match": pkind,
                                  "detail": f"公开题目中出现 {key} 的内容片段（{probe[:40]}…）"})
                    break
            if len(val.strip()) >= 20 and val.strip() in raw_text:
                leaks.append({"qid": qid, "kind": key, "detail": f"公开题目中原样包含 {key}"})
        for i, t in enumerate(priv.get("tests", []) or [], 1):
            exp = t.get("expected_stdout") if isinstance(t, dict) else None
            if isinstance(exp, str) and len(exp.strip()) >= 8 and _norm_code(exp) not in allowed_outputs:
                if _norm_code(exp) in norm_text:
                    leaks.append({"qid": qid, "kind": "hidden_test_output",
                                  "detail": f"第 {i} 个隐藏测试用例的期望输出出现在公开题目中"})
    return leaks


# --------------------------------------------------------------------------- 构建
def build(root, sid, questions: list[dict], records: dict, history: list[dict] | None = None,
          reasons: dict | None = None, force: bool = False, order: str = "asc",
          size: int = DEFAULT_PAPER_SIZE) -> dict:
    history = history or []
    qs = sort_questions([dict(q) for q in questions], order)
    errors, warnings = [], []
    seen = set()
    for q in qs:
        errors += validate_question(q, records)
        if q.get("id") in seen:
            errors.append(f'题号重复：{q["id"]}')
        seen.add(q.get("id"))
    if not qs:
        errors.append("题目列表为空")
    paper = check_paper(qs, records, size=size, order=order, history=history)
    errors += paper["errors"]
    warnings += paper["warnings"]
    cov = coverage(qs, records, reasons)
    weak = weak_point_plan(qs, records, history)
    if cov["uncovered"]:
        warnings.append(f'有 {len(cov["uncovered"])} 个已学知识点本次未覆盖，已列出原因')
    if weak["duplicated_with_history"]:
        errors.append(f'与历史题目重复：{weak["duplicated_with_history"]}（必须换新题，不得重复原题）')

    report = {"session_id": sid, "question_count": len(qs), "errors": errors, "warnings": warnings,
              "paper": paper, "coverage": cov, "weak_point_plan": weak, "order": order,
              "excluded_notice": "",
              "must_tell_learner": ([f'可用知识点偏少（{paper["in_scope_kp_count"]} 个），'
                                     f'10 题会有角度上的重叠'] if paper.get("scope_scarce") else [])
                                    + ([f'薄弱点覆盖比例 {paper["weak_ratio"]:.0%}，'
                                        f'未覆盖：{paper["weak_missing"]}'] if paper.get("weak_missing") else []),
              "sequence": [{"id": q["id"], "type": q.get("question_type"), "difficulty": q["difficulty"],
                            "answer_kind": answer_kind(q), "kp": q["knowledge_points"]} for q in qs]}
    if errors and not force:
        report["ok"] = False
        raise QuestionError("；".join(errors))
    # 落盘前先扫泄漏：一旦发现答案混进公开题面，直接失败，什么都不写
    public_preview = [S.strip_private(q) for q in qs]
    private_preview = {q["id"]: {k: v for k, v in q.items() if k in S.PRIVATE_KEYS} for q in qs}
    pre_leaks = leak_scan(public_preview, private_preview)
    report["leaks"] = pre_leaks
    if pre_leaks:
        raise QuestionError(f"答案泄漏，已拒绝写入：{pre_leaks}")
    report["ok"] = True
    st = S.load_state(root, sid)
    if st["phase"] == "scope_confirmed":
        S.advance(root, sid, "questioning")
    elif st["phase"] != "questioning":
        raise QuestionError(f'当前阶段 {st["phase"]} 不能出题（必须先确认知识点范围 scope_confirmed）')
    written = S.write_questions(root, sid, qs)
    report["written"] = written
    post_leaks = leak_scan(S.read_public(root, sid), json.loads(
        Path(written["private_path"]).read_text(encoding="utf-8"))["answers"])
    report["leaks_after_write"] = post_leaks
    if post_leaks:
        raise QuestionError(f"答案泄漏（写入后复检）：{post_leaks}")
    S.advance(root, sid, "awaiting_submission")
    st_now = S.load_state(root, sid)
    rec_now = M.load(None) if False else None
    report["phase"] = S.phase(root, sid)
    new_excluded = (records.get("scope_notes") or {}).get("new_excluded") or []
    if new_excluded:
        report["excluded_notice"] = "以下按未学排除：" + "、".join(new_excluded)
    long_stems = [q["id"] for q in qs if len(stem_of(q)) > 180]
    if long_stems:
        report["warnings"].append(f"题面偏长（>180 字，建议只写描述/输入输出/限制/考点）：{long_stems}")
    bad_tests = [q["id"] for q in qs if q.get("tests") and not 3 <= len(q["tests"]) <= 5]
    if bad_tests:
        report["warnings"].append(f"测试用例数量建议每题 3~5 条，当前不符合：{bad_tests}")
    report["note"] = "已停止出题，等待学习者提交。标准答案只在 .private/，本阶段任何输出都不含答案。"
    return report


def types_table() -> str:
    lines = ["| 题型 id | 中文 | 作答形式 | 沙箱判定 | 必填（公开） | 必填（私有） | 配比(10题) | 说明 |",
             "| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |"]
    for t, s in QUESTION_TYPES.items():
        lo, hi = PAPER_RECIPE.get(t, (0, 0))
        lines.append(f'| `{t}` | {s["cn"]} | {s["answer_kind"]} | {"✅" if s["sandbox"] else "—"} | '
                     f'{", ".join(s["need_public"]) or "—"} | {", ".join(s["need_private"]) or "—"} | '
                     f'{lo}~{hi} | {s["desc"]} |')
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 出题（含题型系统）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build"); s.add_argument("--questions", required=True); s.add_argument("--records", required=True)
    s.add_argument("--root", default=".plc"); s.add_argument("--sid", required=True)
    s.add_argument("--history"); s.add_argument("--uncovered-reasons")
    s.add_argument("--order", choices=["asc", "desc"], default="asc")
    s.add_argument("--size", type=int, default=DEFAULT_PAPER_SIZE,
                   help=f"题量，默认且应当为 {DEFAULT_PAPER_SIZE}；不等于它会报错（仅测试用小卷）")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("check"); s.add_argument("--questions", required=True); s.add_argument("--records", required=True)
    s.add_argument("--history"); s.add_argument("--uncovered-reasons")
    s.add_argument("--order", choices=["asc", "desc"], default="asc")
    s.add_argument("--size", type=int, default=DEFAULT_PAPER_SIZE)
    sub.add_parser("types")
    s = sub.add_parser("show"); s.add_argument("--root", default=".plc"); s.add_argument("--sid", required=True)
    a = ap.parse_args(argv)

    def _load(p):
        return json.loads(Path(p).read_text(encoding="utf-8"))

    if a.cmd == "types":
        print(types_table()); return 0
    if a.cmd == "show":
        print(json.dumps(S.read_public(a.root, a.sid), ensure_ascii=False, indent=2)); return 0
    qs = _load(a.questions)
    qs = qs.get("questions", qs) if isinstance(qs, dict) else qs
    records = M.load(a.records)
    history = _load(a.history) if a.history else []
    history = history.get("questions", history) if isinstance(history, dict) else history
    reasons = _load(a.uncovered_reasons) if getattr(a, "uncovered_reasons", None) else None
    if a.cmd == "check":
        qs2 = sort_questions(qs, a.order)
        errs = []
        for q in qs2:
            errs += validate_question(q, records)
        paper = check_paper(qs2, records, size=a.size, order=a.order, history=history)
        errs += paper["errors"]
        print(json.dumps({"ok": not errs, "errors": errs, "warnings": paper["warnings"],
                          "type_counts": paper["type_counts"], "coverage": coverage(qs2, records, reasons),
                          "weak_point_plan": weak_point_plan(qs2, records, history)},
                         ensure_ascii=False, indent=2))
        return 0 if not errs else 3
    rep = build(a.root, a.sid, qs, records, history, reasons, a.force, a.order, a.size)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (QuestionError, S.SessionError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(3)
