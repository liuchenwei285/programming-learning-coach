#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 5：批改流程（九段结构、未提交题、无运行环境时的诚实声明）。"""
from __future__ import annotations

import json
import unittest

import tutil
import grading as GR
import mastery as M
import questions as Q
import session_store as S

STATES = {"for循环": "学习中", "if分支": "学习中", "printf输出": "待检验", "指针基础": "未学习"}
TEXT_SUB = "第一行\n第二行写错了"
NOTES = {
    "Q1": {"error_location": "无明显错误", "error_reason": "—", "suggestion": "可以把 n*2 写成累加以练习循环",
           "step_by_step": "1) 读入 n；2) 计算；3) 输出。数学上即 $2n$。",
           "better_approach": "在你现有写法上减少一次乘法", "better_approach_kps": ["for循环"]},
    "overall": {"知识理解": "对循环边界理解正确", "代码编写": "命名清晰，无编译警告",
                "逻辑思维": "能独立完成单知识点题", "知识点掌握": "for循环 达到待检验",
                "薄弱环节": "scanf 取地址符仍不牢", "训练建议": "下次安排 2 道组合题"},
}


def build_session(root, sid="s1", *, submit=("Q1",), tests=None, qtext=False):
    rec = tutil.make_records(STATES)
    S.create(root, sid, "c")
    S.set_field(root, sid, scope_confirmed=True)
    S.advance(root, sid, "analysis")
    S.advance(root, sid, "scope_confirmed")
    qs = [tutil.make_question("Q1", 3, ["for循环", "printf输出"], required=["for循环"],
                              tests=tests or [{"name": "样例", "stdin": "3\n", "expected_stdout": "6\n"}]),
          tutil.make_question("Q2", 5, ["for循环", "if分支"], required=["for循环", "if分支"],
                              combined=True)]
    if qtext:
        qs.append(tutil.make_text_question("Q3", 2, ["printf输出"], qtype="concept",
                                           requirements=["说明两者的区别"],
                                           expected="第一行\n第二行"))
        qs[-1]["answer_points"] = ["要点一", "要点二", "要点三"]
        qs.append(tutil.make_text_question("Q4", 1, ["printf输出"], qtype="output_prediction",
                                           expected="line A\nline B"))
    Q.build(root, sid, qs, rec, size=len(qs))
    for qid in submit:
        body = {("Q3", True): TEXT_SUB, ("Q4", True): "line A\nline X\n"}.get((qid, qtext), tutil.CORRECT_C)
        S.save_submission(root, sid, qid, body, "c")
    return rec, qs


def graded(root, rec, sid="s1"):
    """能跑沙箱就真跑；跑不了就给出静态判定（manual_correct），两者都不编造分数。"""
    notes = json.loads(json.dumps(NOTES))
    run = tutil.has_toolchain("c")
    if not run:
        notes["Q1"]["manual_correct"] = True
    return GR.grade_all(root, sid, rec, notes, run=run, timeout=10)


class TestGrading(unittest.TestCase):
    def setUp(self):
        self.root = tutil.tmp("plc-grade-")

    def tearDown(self):
        tutil.rm(self.root)

    def test_nine_sections_present_for_submitted(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        for sec in GR.SECTIONS:
            self.assertIsNotNone(r1.get(sec), f"缺少批改段落：{sec}")
        self.assertEqual(r1["status"], "已提交")
        self.assertIn("int main", r1["my_answer"])
        self.assertEqual(r1["missing_sections"], [])

    def test_unsubmitted_not_fabricated(self):
        rec, _ = build_session(self.root)
        rep = graded(self.root, rec)
        r2 = [r for r in rep["results"] if r["id"] == "Q2"][0]
        self.assertEqual(r2["status"], "未完成")
        self.assertIsNone(r2["my_answer"])
        self.assertIsNone(r2["score"])
        self.assertIn("未提交", r2["correctness"])
        self.assertIn("不编造", r2["error_reason"])
        ov = rep["overall"]
        self.assertEqual(ov["unsubmitted_ids"], ["Q2"])
        self.assertEqual(ov["submitted"], 1)
        self.assertEqual(ov["unsubmitted"], 1)
        self.assertEqual(ov["score_submitted_only"], 100)
        self.assertEqual(ov["score_including_unsubmitted_as_zero"], 50)
        self.assertIn("不编造成绩", ov["score_note"])

    def test_events_only_from_submitted(self):
        rec, _ = build_session(self.root)
        rep = graded(self.root, rec)
        self.assertTrue(rep["events"])
        self.assertTrue(all(e["question_id"] == "Q1" for e in rep["events"]),
                        "未提交的题不能产生掌握度事件")
        self.assertEqual({e["kp"] for e in rep["events"]}, {"for循环", "printf输出"})
        self.assertEqual(S.read_events(self.root, "s1"), rep["events"])

    def test_mastery_promotion_after_grading(self):
        rec, _ = build_session(self.root)
        self.assertEqual(M.state_of(rec, "for循环"), "学习中")
        graded(self.root, rec)
        self.assertEqual(M.state_of(rec, "for循环"), "待检验", "一次答对最多升到待检验")
        self.assertFalse(rec["records"]["for循环"]["estimated"])

    def test_static_review_statement_when_not_run(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertIn(GR.NOT_RUN_STATEMENT, r1["execution_statement"])
        self.assertIn("manual_correct", r1["pending_model_input"])
        self.assertEqual(rep["sandbox_available"], False)
        self.assertEqual(rep["static_only_statement"], GR.NOT_RUN_STATEMENT)
        self.assertIsNone(r1["score"], "未运行且未给静态判定时不得编造分数")

    def test_manual_correct_used_when_no_toolchain(self):
        rec, _ = build_session(self.root)
        notes = json.loads(json.dumps(NOTES))
        notes["Q1"]["manual_correct"] = True
        rep = GR.grade_all(self.root, "s1", rec, notes, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertIn("静态审查判定", r1["correctness"])
        self.assertEqual(r1["score"], 100)
        self.assertIn(GR.NOT_RUN_STATEMENT, r1["execution_statement"])

    def test_better_approach_dropped_when_out_of_scope(self):
        rec, _ = build_session(self.root)
        notes = json.loads(json.dumps(NOTES))
        notes["Q1"]["better_approach"] = "用指针数组来遍历会更快"
        notes["Q1"]["better_approach_kps"] = ["指针基础"]
        rep = GR.grade_all(self.root, "s1", rec, notes, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertIsNone(r1["better_approach"], "超纲的更好写法必须被移除")
        self.assertEqual(r1["better_approach_dropped_for_out_of_scope"], ["指针基础"])

    def test_explanation_and_better_approach_fall_back_to_private(self):
        """出题阶段已写好的逐步讲解/更好写法，批改时即使模型没写也要用上，九段不得缺失。"""
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, {}, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertIsNotNone(r1["step_by_step"], "应回落到私有 explanation")
        self.assertIsNotNone(r1["better_approach"], "应回落到私有 better_approach")
        # 只有「针对我这份代码」的三段必须由模型写，缺了就如实列出，不编造
        self.assertEqual(r1["missing_sections"], ["error_location", "error_reason", "suggestion"])

    def test_notes_override_private_explanation(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertEqual(r1["step_by_step"], NOTES["Q1"]["step_by_step"])

    def test_manual_judge_for_prose_answers(self):
        """概念辨析/代码分析题的答案是散文：不做逐行机器比对，未给判定时不编造分数。"""
        rec, _ = build_session(self.root, submit=("Q1", "Q3", "Q4"), qtext=True)
        rep = GR.grade_all(self.root, "s1", rec, {}, run=False)
        r3 = [r for r in rep["results"] if r["id"] == "Q3"][0]
        self.assertEqual(r3["judge_mode"], "manual")
        self.assertIsNone(r3["score"], "教练没判定前不得编造分数")
        self.assertIn("待教练判定", r3["correctness"])
        self.assertIn("manual_correct", r3["pending_model_input"])
        self.assertTrue(r3["answer_points"])
        self.assertIsNone(r3["text_diff"], "散文答案不做逐行对照")

    def test_manual_judge_with_coach_verdict(self):
        rec, _ = build_session(self.root, submit=("Q1", "Q3", "Q4"), qtext=True)
        notes = {"Q3": {"manual_correct": "partial", "manual_score": 60,
                        "points_hit": [1, 3], "points_missed": [2],
                        "error_location": "第 2 点", "error_reason": "漏了结束符",
                        "suggestion": "补上 '\\0' 的说明", "step_by_step": "逐条对照评分要点"}}
        rep = GR.grade_all(self.root, "s1", rec, notes, run=False)
        r3 = [r for r in rep["results"] if r["id"] == "Q3"][0]
        self.assertEqual(r3["score"], 60)
        self.assertIn("部分正确", r3["correctness"])
        self.assertEqual((r3["passed"], r3["total"]), (2, 3))

    def test_auto_judge_line_diff_for_output_prediction(self):
        """输出预测题：逐行对照，Tab 与空格等价，错行自动定位。"""
        rec, _ = build_session(self.root, submit=("Q1", "Q3", "Q4"), qtext=True)
        rep = GR.grade_all(self.root, "s1", rec, {}, run=False)
        r4 = [r for r in rep["results"] if r["id"] == "Q4"][0]
        self.assertEqual(r4["judge_mode"], "auto")
        self.assertEqual((r4["passed"], r4["total"]), (1, 2))
        self.assertEqual(r4["score"], 50)
        self.assertEqual(r4["text_diff"]["wrong_lines"], [2])
        self.assertIn("部分正确", r4["correctness"])
        self.assertIn("第 2 行", r4["error_location"])

    def test_auto_judge_ignores_tab_and_space_difference(self):
        rec, _ = build_session(self.root, submit=("Q1", "Q3", "Q4"), qtext=True)
        S.save_submission(self.root, "s1", "Q4", "line\tA\nline B  \n", "text")
        rep = GR.grade_all(self.root, "s1", rec, {}, run=False)
        r4 = [r for r in rep["results"] if r["id"] == "Q4"][0]
        self.assertEqual(r4["score"], 100, "Tab/空格与行尾空白差异不应判错")
        self.assertIn("完全正确", r4["correctness"])

    def test_overall_six_dimensions(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        dims = rep["overall"]["dimensions"]
        for d in GR.OVERALL_DIMS:
            self.assertTrue(dims.get(d), f"整体评价缺少维度：{d}")
        self.assertEqual(rep["overall"]["dimensions_missing"], [])

    def test_missing_dimensions_reported(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, {"Q1": NOTES["Q1"]}, run=False)
        self.assertEqual(set(rep["overall"]["dimensions_missing"]), set(GR.OVERALL_DIMS))

    def test_cannot_grade_without_any_submission(self):
        rec, _ = build_session(self.root, submit=())
        with self.assertRaises(GR.GradingError) as cm:
            GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        self.assertIn("不能进入批改", str(cm.exception))

    def test_partial_credit(self):
        tests = [{"name": "样例", "stdin": "3\n", "expected_stdout": "6\n"},
                 {"name": "另一组", "stdin": "5\n", "expected_stdout": "10\n"}]
        rec, _ = build_session(self.root, tests=tests)
        S.save_submission(self.root, "s1", "Q1",
                          '#include <stdio.h>\nint main(void){ printf("6\\n"); return 0; }\n', "c")
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=tutil.has_toolchain("c"))
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        if not tutil.has_toolchain("c"):
            self.skipTest("没有 gcc，跳过部分正确的运行判定")
        self.assertEqual(r1["correctness"], "部分正确")
        self.assertEqual(r1["score"], 50)
        self.assertEqual([c["passed"] for c in r1["execution"]["cases"]], [True, False])
        ev = [e for e in rep["events"] if e["question_id"] == "Q1"][0]
        self.assertFalse(ev["correct"], "部分正确按『出现错误』处理")
        self.assertTrue(ev["partial"])

    @unittest.skipUnless(tutil.has_toolchain("c"), "未安装 gcc")
    def test_sandbox_executed_and_reported(self):
        rec, _ = build_session(self.root)
        rep = GR.grade_all(self.root, "s1", rec, NOTES, run=True, timeout=10)
        r1 = [r for r in rep["results"] if r["id"] == "Q1"][0]
        self.assertTrue(rep["sandbox_available"])
        self.assertEqual(r1["execution"]["mode"], "executed")
        self.assertEqual(r1["correctness"], "完全正确")
        self.assertEqual(r1["score"], 100)
        self.assertIn("已在沙箱中运行", r1["execution_statement"])
        self.assertTrue(r1["execution"]["isolation"]["cleaned"])

    def test_phase_after_grading_allows_private_read(self):
        rec, _ = build_session(self.root)
        GR.grade_all(self.root, "s1", rec, NOTES, run=False)
        self.assertEqual(S.phase(self.root, "s1"), "grading")
        self.assertIn("Q1", S.read_private(self.root, "s1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
