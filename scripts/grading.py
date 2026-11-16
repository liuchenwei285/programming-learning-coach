#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批改：按题型分两条判定路径（沙箱跑代码 / 逐行对照文本），九段结构 + 整体评价 + 掌握度事件。

九段（缺一不可，代码会列出 missing_sections）：
  我的答案 / 正确性 / 错误位置 / 错误原因 / 知识点 / 修改建议 / 标准答案 / 逐步讲解 / 更好写法

判定路径：
* answer_kind == "code"（编程题、综合挑战题、改错题）→ 沙箱编译并运行测试用例；
  跑不了就写「本次未运行，仅静态审查」，绝不假装运行过。
* answer_kind == "text"（输出预测题、程序追踪题、代码分析题、概念辨析题）→ 与期望答案逐行对照，
  空白折叠后比较（Tab 与空格等价），给出「第几行对/错」的对照表；行级错误定位可自动得出，
  错误原因与修改建议仍由教练写（不编造）。

硬规则：
* 未提交 → 「未完成」，不编造答案、不编造成绩、不产生掌握度事件。
* 「更好写法」涉及未学知识点 → 自动移除并说明（不用超纲写法替换你的思路）。
* 标准答案只在批改阶段从 .private/ 读取（阶段门由 session_store 强制）。

用法：
  python grading.py grade --root .plc --sid s1 --records rec.json --notes notes.json --out grading.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery as M           # noqa: E402
import questions as QS        # noqa: E402
import session_store as S     # noqa: E402
import sandbox_run as SBX     # noqa: E402

SECTIONS = ["my_answer", "correctness", "error_location", "error_reason", "knowledge_points",
            "suggestion", "reference_solution", "step_by_step", "better_approach"]
SECTION_CN = {"my_answer": "我的答案", "correctness": "正确性", "error_location": "错误位置",
              "error_reason": "错误原因", "knowledge_points": "知识点", "suggestion": "修改建议",
              "reference_solution": "标准答案", "step_by_step": "逐步讲解", "better_approach": "更好写法"}
OVERALL_DIMS = ["知识理解", "代码编写", "逻辑思维", "知识点掌握", "薄弱环节", "训练建议"]
NOT_RUN_STATEMENT = "本次未运行，仅静态审查"
TEXT_NO_BETTER = "（本题为文本作答，不涉及改写代码；更简洁的表述见上面标准答案的逐条讲解）"


class GradingError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 文本答案对照
def _norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u3000", " ")).strip()


def compare_text_answer(expected: str | None, actual: str | None) -> dict:
    """逐行对照：空白折叠后比较（Tab/多空格等价），大小写与标点差异算错。"""
    exp = [_norm_line(x) for x in (expected or "").splitlines()]
    act = [_norm_line(x) for x in (actual or "").splitlines()]
    exp = [x for x in exp if x]
    act_keep = [x for x in act if x]
    rows, matched = [], 0
    for i in range(max(len(exp), len(act_keep))):
        e = exp[i] if i < len(exp) else None
        a = act_keep[i] if i < len(act_keep) else None
        ok = e is not None and a is not None and e == a
        matched += 1 if ok else 0
        rows.append({"line": i + 1, "expected": e, "actual": a,
                     "passed": ok,
                     "verdict": "✅" if ok else ("❌ 不一致" if (e and a) else "❌ 缺行")})
    total = len(exp)
    score = round(100 * matched / total) if total else None
    correctness = ("完全正确" if total and matched == total
                   else ("部分正确" if matched else ("错误" if total else "待人工判定（未运行）")))
    return {"mode": "text_line_diff", "rows": rows, "matched": matched, "total": total,
            "score": score, "correctness": correctness,
            "wrong_lines": [r["line"] for r in rows if not r["passed"]]}


def _text_diff_table(cmp_res: dict) -> str:
    rows = cmp_res.get("rows") or []
    if not rows:
        return ""
    lines = ["| 行 | 我的答案 | 标准答案 | 判定 |", "| :-- | :-- | :-- | :-- |"]

    def cell(v):
        if v is None:
            return "—（缺行）"
        return (str(v).replace("|", "\\|")[:120] or "—")

    for r in rows:
        lines.append(f'| {r["line"]} | {cell(r["actual"])} | {cell(r["expected"])} | {r["verdict"]} |')
    lines.append("")
    lines.append(f'**逐行对照：{cmp_res["matched"]}/{cmp_res["total"]} 行一致'
                 + (f'，出错行：{cmp_res["wrong_lines"]}' if cmp_res["wrong_lines"] else "") + "**")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 单题批改
def grade_question(q: dict, priv: dict, submission: str | None, notes: dict,
                   sandbox: dict | None, records: dict) -> dict:
    qid = q["id"]
    n = notes.get(qid, {}) or {}
    kind = QS.answer_kind(q)
    qtype = q.get("question_type", "")
    tspec = QS.QUESTION_TYPES.get(qtype, {"cn": qtype or "未标注题型"})
    priv = priv or {}

    better = n.get("better_approach") or priv.get("better_approach")
    better_kps = n.get("better_approach_kps") or q.get("better_approach_kps") or []
    out_of_scope = [kp for kp in better_kps if not M.in_scope(records, kp)] if better else []
    if out_of_scope:
        better = None            # 不用超纲写法替换学习者的思路
    if kind == "text" and not better:
        better = TEXT_NO_BETTER

    reference = priv.get("reference_solution") or priv.get("expected_answer")
    res = {
        "id": qid, "title": q.get("title"), "question_type": qtype, "type_cn": tspec.get("cn", qtype),
        "answer_kind": kind, "difficulty": q.get("difficulty"), "est_minutes": q.get("est_minutes"),
        "language": q.get("language"), "is_combined": bool(q.get("is_combined")),
        "knowledge_points": q.get("knowledge_points", []),
        "required_knowledge_points": q.get("required_knowledge_points", []),
        "status": "已提交" if submission is not None else "未完成",
        "my_answer": submission,
        "my_answer_sha256_16": (S.hashlib.sha256(submission.encode()).hexdigest()[:16]
                                if submission is not None else None),
        "reference_solution": reference,
        "reference_kind": "text" if (kind == "text" or not priv.get("reference_solution")) else "code",
        "reference_language": priv.get("reference_solution_language") or q.get("language") or "c",
        "expected_answer": priv.get("expected_answer"),
        "answer_points": priv.get("answer_points"), "judge_mode": None,
        "points_hit": n.get("points_hit"), "points_missed": n.get("points_missed"),
        "tests": priv.get("tests", []),
        "self_check_questions": q.get("self_check_questions", []),
        "self_check_answers": priv.get("self_check_answers"),
        "explanation": n.get("step_by_step") or priv.get("explanation"),
        "execution": None, "text_diff": None,
        "execution_statement": NOT_RUN_STATEMENT,
        "correctness": None, "passed": None, "total": None, "score": None,
        "error_location": n.get("error_location"), "error_reason": n.get("error_reason"),
        "suggestion": n.get("suggestion"),
        "step_by_step": n.get("step_by_step") or priv.get("explanation"),
        "better_approach": better,
        "better_approach_dropped_for_out_of_scope": out_of_scope,
        "notes": n.get("correctness_note"), "pending_model_input": [],
    }

    if submission is None:
        res["correctness"] = "未提交（未完成）"
        res["error_location"] = "—"
        res["error_reason"] = "未提交，无法判定；不编造答案与成绩"
        res["suggestion"] = "补做后再提交，本题不计入本次得分"
        res["step_by_step"] = res["step_by_step"] or None
        return res

    if kind == "text" and QS.judge_mode(q) == "auto":
        cmp_res = compare_text_answer(priv.get("expected_answer"), submission)
        res["text_diff"] = cmp_res
        res["judge_mode"] = "auto"
        res["passed"], res["total"] = cmp_res["matched"], cmp_res["total"]
        res["correctness"] = cmp_res["correctness"] + "（逐行对照）"
        res["score"] = cmp_res["score"]
        res["execution_statement"] = "文本作答：与标准答案逐行对照（空白折叠后比较，Tab 与空格等价）"
        if cmp_res["wrong_lines"] and not res["error_location"]:
            res["error_location"] = "第 " + "、".join(str(x) for x in cmp_res["wrong_lines"]) + " 行与标准答案不一致"
    elif kind == "text":
        # 概念辨析 / 代码分析：答案是散文，逐行比对会误判 —— 交给教练对照评分要点判定
        res["judge_mode"] = "manual"
        res["execution_statement"] = "文本作答（散文）：不做逐行机器比对，由教练对照下面的评分要点判定"
        manual = n.get("manual_correct")
        if manual is None:
            res["correctness"] = "待教练判定（对照评分要点）"
            res["pending_model_input"].append("manual_correct")
        else:
            res["correctness"] = {True: "正确（教练对照评分要点判定）",
                                  False: "错误（教练对照评分要点判定）",
                                  "partial": "部分正确（教练对照评分要点判定）"}.get(manual, str(manual))
            score = n.get("manual_score")
            res["score"] = score if score is not None else {True: 100, False: 0, "partial": 60}.get(manual)
            hit = n.get("points_hit") or []
            res["passed"], res["total"] = len(hit), len(priv.get("answer_points") or [])
    else:
        if sandbox:
            res["execution"] = {"ran": sandbox.get("ran"), "mode": sandbox.get("mode"),
                                "statement": sandbox.get("statement"), "reason": sandbox.get("reason"),
                                "toolchain": (sandbox.get("toolchain") or {}).get("exe"),
                                "compile": sandbox.get("compile"), "cases": sandbox.get("cases"),
                                "isolation": sandbox.get("isolation"), "warnings": sandbox.get("warnings"),
                                "static_review": sandbox.get("static_review")}
            res["execution_statement"] = (sandbox.get("statement") if sandbox.get("ran")
                                          else f'{NOT_RUN_STATEMENT}（原因：{sandbox.get("reason")}）')
            res["passed"], res["total"] = sandbox.get("passed"), sandbox.get("total")
            if sandbox.get("ran") and sandbox.get("total"):
                res["correctness"] = ("完全正确" if sandbox["passed"] == sandbox["total"]
                                      else ("部分正确" if sandbox["passed"] else "错误"))
                res["score"] = round(100 * sandbox["passed"] / sandbox["total"])
            else:
                manual = n.get("manual_correct")
                if manual is None:
                    res["correctness"] = "待人工判定（未运行）"
                    res["pending_model_input"].append("manual_correct")
                else:
                    res["correctness"] = {True: "正确（静态审查判定）", False: "错误（静态审查判定）",
                                          "partial": "部分正确（静态审查判定）"}.get(manual, str(manual))
                    res["score"] = {True: 100, False: 0, "partial": 60}.get(manual)
        else:
            manual = n.get("manual_correct")
            res["correctness"] = ("待人工判定（未运行）" if manual is None else
                                  {True: "正确（静态审查判定）", False: "错误（静态审查判定）"}.get(manual, str(manual)))
            if manual is None:
                res["pending_model_input"].append("manual_correct")
            else:
                res["score"] = {True: 100, False: 0}.get(manual)

    if not res["error_location"]:
        res["error_location"] = "无明显错误" if str(res["correctness"]).startswith(("完全正确", "正确")) else None
    for key in ("error_reason", "suggestion", "step_by_step"):
        if not res.get(key):
            res["pending_model_input"].append(key)
    return res


# --------------------------------------------------------------------------- 整体评价
def overall(results: list[dict], notes: dict, records: dict) -> dict:
    submitted = [r for r in results if r["status"] == "已提交"]
    unsubmitted = [r for r in results if r["status"] != "已提交"]
    scores = [r["score"] for r in submitted if isinstance(r["score"], (int, float))]
    avg = round(sum(scores) / len(scores), 1) if scores else None
    avg_all = round(sum(scores + [0] * len(unsubmitted)) / len(results), 1) if results else None
    ran = [r for r in submitted if (r.get("execution") or {}).get("ran")]
    text_qs = [r for r in submitted if r["answer_kind"] == "text"]
    code_qs = [r for r in submitted if r["answer_kind"] == "code"]
    dims = (notes.get("overall") or {})
    return {
        "submitted": len(submitted), "unsubmitted": len(unsubmitted),
        "unsubmitted_ids": [r["id"] for r in unsubmitted],
        "score_submitted_only": avg,
        "score_including_unsubmitted_as_zero": avg_all,
        "score_note": ("score 仅按已提交题目计算；未完成题目单列，不编造成绩" if unsubmitted
                       else "全部题目已提交"),
        "type_counts": {t: sum(1 for r in results if r["question_type"] == t)
                        for t in sorted({r["question_type"] for r in results})},
        "text_questions": len(text_qs), "code_questions": len(code_qs),
        "text_score": (round(sum(r["score"] for r in text_qs if isinstance(r["score"], (int, float)))
                             / max(1, len([r for r in text_qs if isinstance(r["score"], (int, float))])), 1)
                       if text_qs else None),
        "code_score": (round(sum(r["score"] for r in code_qs if isinstance(r["score"], (int, float)))
                             / max(1, len([r for r in code_qs if isinstance(r["score"], (int, float))])), 1)
                       if code_qs else None),
        "executed_questions": len(ran), "static_only_questions": len(code_qs) - len(ran),
        "compile_warning_questions": sum(1 for r in ran if ((r["execution"].get("compile") or {}).get("warnings"))),
        "dimensions": {d: dims.get(d) for d in OVERALL_DIMS},
        "dimensions_missing": [d for d in OVERALL_DIMS if not dims.get(d)],
        "weak_points": M.weak_points(records),
    }


def build_events(results: list[dict], sid: str) -> list[dict]:
    """掌握度事件：未提交或无法判定的题不产生任何事件（不编造表现）。"""
    events = []
    for r in results:
        if r["status"] != "已提交":
            continue
        corr = str(r.get("correctness") or "")
        if not corr or corr.startswith(("待人工判定", "待教练判定")):
            continue
        correct = corr.startswith("完全正确") or corr.startswith("正确")
        required = set(r.get("required_knowledge_points") or [])
        for kp in r["knowledge_points"]:
            is_required = kp in required
            if not correct and not is_required:
                # 答错只下调「解题必需」的知识点：一道题涉及 5 个知识点，
                # 错因通常只落在必需的那 1~3 个上，把全部涉及项都降级会造成薄弱点噪声
                continue
            events.append({"kp": kp, "session_id": sid, "question_id": r["id"],
                           "correct": correct, "combined": r["is_combined"],
                           "required": is_required,
                           "partial": corr.startswith("部分正确"), "score": r["score"],
                           "question_type": r["question_type"],
                           "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return events


def grade_all(root, sid, records, notes, *, run: bool = True, timeout: float = 5.0,
              max_output: int = 20000, workroot: str | None = None) -> dict:
    st = S.load_state(root, sid)
    if st["phase"] not in {"awaiting_submission", "grading"}:
        raise GradingError(f"当前阶段 {st['phase']} 不能批改（必须先出题并等待提交）")
    if st["phase"] == "awaiting_submission":
        if not S.list_submissions(root, sid):
            raise GradingError("还没有任何提交，不能进入批改（禁止跳过提交环节）")
        S.advance(root, sid, "grading")
    questions = S.read_public(root, sid)
    answers = S.read_private(root, sid)
    subs = S.list_submissions(root, sid)
    results = []
    for q in questions:
        qid = q["id"]
        submission = S.load_submission(root, sid, qid) if qid in subs else None
        sandbox = None
        if run and submission is not None and QS.answer_kind(q) == "code":
            lang = q.get("language") or st.get("language") or "text"
            sandbox = SBX.run_tests(submission, lang, (answers.get(qid) or {}).get("tests") or [],
                                    timeout=timeout, max_output=max_output, workroot=workroot)
        results.append(grade_question(q, answers.get(qid) or {}, submission, notes, sandbox, records))
    for r in results:
        r["missing_sections"] = ([k for k in SECTIONS if r.get(k) is None]
                                 if r["status"] == "已提交" else [])
    events = build_events(results, sid)
    M.apply_events(records, events)
    report = {
        "session_id": sid, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": S.phase(root, sid), "results": results,
        "overall": overall(results, notes, records), "events": events,
        "sandbox_available": any((r.get("execution") or {}).get("ran") for r in results),
        "static_only_statement": (NOT_RUN_STATEMENT if not any(
            (r.get("execution") or {}).get("ran") for r in results
            if r["answer_kind"] == "code") and any(r["answer_kind"] == "code" for r in results) else None),
    }
    S.append_events(root, sid, events)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 批改")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("grade")
    s.add_argument("--root", default=".plc"); s.add_argument("--sid", required=True)
    s.add_argument("--records", required=True); s.add_argument("--notes")
    s.add_argument("--no-run", action="store_true"); s.add_argument("--timeout", type=float, default=5.0)
    s.add_argument("--max-output", type=int, default=20000); s.add_argument("--workroot")
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    records = M.load(a.records)
    notes = json.loads(Path(a.notes).read_text(encoding="utf-8")) if a.notes else {}
    rep = grade_all(a.root, a.sid, records, notes, run=not a.no_run, timeout=a.timeout,
                    max_output=a.max_output, workroot=a.workroot)
    M.save(a.records, records)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if S.phase(a.root, a.sid) == "grading":
        S.advance(a.root, a.sid, "practice_doc")
    print(json.dumps({"ok": True, "out": a.out, "overall": rep["overall"],
                      "pending_model_input": {r["id"]: r["pending_model_input"] for r in rep["results"]
                                              if r["pending_model_input"]},
                      "static_only": rep["static_only_statement"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (GradingError, S.SessionError, M.MasteryError) as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(5)
