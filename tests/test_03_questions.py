#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 3 + 4：出题流程与不超纲检查；答案保密（出题阶段无答案）。"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

import tutil
import mastery as M
import questions as Q
import session_store as S
import plc_cli

MARK_SOL = "SECRET_SOLVER_MARKER_42"
MARK_OUT = "SECRET_EXPECTED_OUTPUT_777"

RECORD_STATES = {"for循环": "学习中", "if分支": "学习中", "数据类型": "待检验",
                 "printf输出": "基本掌握", "scanf输入": "学习中", "指针基础": "未学习"}


def records():
    return tutil.make_records(RECORD_STATES)


def qset():
    return [
        tutil.make_question("Q1", 2, ["for循环"], required=["for循环"]),
        tutil.make_question("Q2", 5, ["for循环", "if分支", "数据类型"],
                            required=["for循环", "if分支"], combined=True),
        tutil.make_question("Q3", 3, ["printf输出", "数据类型"], required=["printf输出"]),
    ]


class TestScopeCheck(unittest.TestCase):
    def test_out_of_scope_required_kp_rejected(self):
        q = tutil.make_question("Q9", 3, ["指针基础"], required=["指针基础"])
        errs = Q.validate_question(q, records())
        self.assertTrue(any("超纲" in e for e in errs), errs)

    def test_out_of_scope_mentioned_kp_rejected(self):
        q = tutil.make_question("Q9", 3, ["for循环", "指针基础"], required=["for循环"])
        errs = Q.validate_question(q, records())
        self.assertTrue(any("超纲" in e and "涉及" in e for e in errs), errs)

    def test_unknown_kp_is_out_of_scope(self):
        q = tutil.make_question("Q9", 3, ["量子计算"], required=["量子计算"])
        self.assertTrue(any("超纲" in e for e in Q.validate_question(q, records())))

    def test_in_scope_question_passes(self):
        for q in qset():
            self.assertEqual(Q.validate_question(q, records()), [], q["id"])

    def test_structural_validation(self):
        q = tutil.make_question("Q1", 2, ["for循环"])
        del q["title"]
        del q["stem"]
        del q["description"]
        q["difficulty"] = 9
        q["examples"] = []
        errs = Q.validate_question(q, records())
        self.assertTrue(any("title" in e for e in errs))
        self.assertTrue(any("stem" in e for e in errs), errs)
        self.assertTrue(any("difficulty" in e for e in errs))
        self.assertTrue(any("examples" in e for e in errs))

    def test_better_approach_must_stay_in_scope(self):
        q = tutil.make_question("Q9", 3, ["for循环"], required=["for循环"])
        q["better_approach_kps"] = ["指针基础"]
        errs = Q.validate_question(q, records())
        self.assertTrue(any("更好写法" in e and "超纲" in e for e in errs), errs)

    def test_hint_L3_must_not_be_full_answer(self):
        q = tutil.make_question("Q1", 2, ["for循环"])
        q["hints"]["L3"] = "#include <stdio.h>\nint main(void){ for(int i=0;i<10;i++){ printf(\"%d\", i); } return 0; }" * 6
        errs = Q.validate_question(q, records())
        self.assertTrue(any("L3" in e for e in errs), errs)


class TestFlowGates(unittest.TestCase):
    def setUp(self):
        self.root = tutil.tmp("plc-flow-")
        S.create(self.root, "s1", "c")

    def tearDown(self):
        tutil.rm(self.root)

    def test_cannot_skip_phases(self):
        with self.assertRaises(S.SessionError) as cm:
            S.advance(self.root, "s1", "questioning")
        self.assertIn("禁止跳步", str(cm.exception))
        self.assertIn("awaiting_submission", str(cm.exception))

    def test_cannot_grade_without_submission(self):
        S.set_field(self.root, "s1", scope_confirmed=True)
        S.advance(self.root, "s1", "analysis")
        S.advance(self.root, "s1", "scope_confirmed")
        S.advance(self.root, "s1", "questioning")
        S.advance(self.root, "s1", "awaiting_submission")
        with self.assertRaises(S.SessionError) as cm:
            S.advance(self.root, "s1", "grading")
        self.assertIn("还没有任何提交", str(cm.exception))

    def test_scope_is_auto_locked_without_manual_confirmation(self):
        """v3：范围自动锁定（T1~T3 可用、T4 按未学排除），不再要求学习者确认。"""
        S.advance(self.root, "s1", "analysis")
        st = S.advance(self.root, "s1", "scope_confirmed")
        self.assertEqual(st["phase"], "scope_confirmed")
        self.assertTrue(st["scope_confirmed"], "进入该阶段即自动置位")

    def test_scope_confirm_no_longer_blocks(self):
        rec_path = self.root / "records.json"
        M.save(rec_path, records())
        rc = plc_cli.main(["scope-confirm", "--root", str(self.root), "--sid", "s1",
                           "--records", str(rec_path)])
        self.assertEqual(rc, 0, "v3 取消人工门闩：范围自动锁定，不再要求确认")

    def test_questions_rejected_before_scope_confirmation(self):
        with self.assertRaises(Q.QuestionError) as cm:
            Q.build(self.root, "s1", qset(), records(), size=3)
        self.assertIn("scope_confirmed", str(cm.exception))

    def test_full_order_and_stop_at_awaiting_submission(self):
        S.set_field(self.root, "s1", scope_confirmed=True)
        S.advance(self.root, "s1", "analysis")
        S.advance(self.root, "s1", "scope_confirmed")
        rep = Q.build(self.root, "s1", qset(), records(), size=3)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["phase"], "awaiting_submission", "出题后必须停下等待提交")
        public = S.read_public(self.root, "s1")
        self.assertEqual([q["id"] for q in public], ["Q1", "Q3", "Q2"], "默认按难度递增（范例顺序）")
        self.assertEqual([q["difficulty"] for q in public], [2, 3, 5])
        self.assertEqual([q["id"] for q in Q.sort_questions(qset(), "desc")], ["Q2", "Q3", "Q1"])

    def test_duplicate_with_history_rejected(self):
        S.set_field(self.root, "s1", scope_confirmed=True)
        S.advance(self.root, "s1", "analysis")
        S.advance(self.root, "s1", "scope_confirmed")
        hist = [dict(qset()[0])]
        with self.assertRaises(Q.QuestionError) as cm:
            Q.build(self.root, "s1", qset(), records(), history=hist, size=3)
        self.assertIn("重复", str(cm.exception))


class TestCoverageAndWeakPoints(unittest.TestCase):
    def test_coverage_lists_uncovered_with_reason(self):
        qs = Q.sort_questions(qset())
        cov = Q.coverage(qs, records(), {"scanf输入": "本次题目都是给定输入，下次专门练读入"})
        self.assertIn("for循环", cov["covered"])
        self.assertIn("printf输出", cov["covered"])
        un = {u["kp"]: u for u in cov["uncovered"]}
        self.assertIn("scanf输入", un)
        self.assertEqual(un["scanf输入"]["reason_source"], "provided")
        self.assertNotIn("指针基础", un, "未学习的知识点不属于覆盖范围")
        self.assertLess(cov["coverage_rate"], 1.0)
        for u in cov["uncovered"]:
            self.assertTrue(u["reason"], "未覆盖必须给出原因")

    def test_uncovered_reason_auto_filled(self):
        cov = Q.coverage(Q.sort_questions(qset()), records(), None)
        self.assertTrue(all(u["reason_source"] == "auto" for u in cov["uncovered"]))

    def test_weak_point_plan(self):
        rec = records()
        rec["records"]["if分支"]["wrong_questions"] = ["上次Q3"]
        rec["records"]["if分支"]["practice_evidence"] = [{"session_id": "s0", "question_id": "上次Q3",
                                                          "result": "wrong"}]
        plan = Q.weak_point_plan(Q.sort_questions(qset()), rec, [])
        self.assertIn("if分支", plan["weak_points"])
        self.assertIn("if分支", plan["weak_covered"])

    def test_fingerprint_ignores_whitespace(self):
        a = tutil.make_question("Q1", 2, ["for循环"], desc="输入 n，输出 1..n 的和。")
        b = tutil.make_question("Q9", 3, ["for循环"], desc="输入  n ， 输出 1..n 的 和 。")
        self.assertEqual(Q.question_fingerprint(a), Q.question_fingerprint(b))


def paper(records_=None, *, bad_type=None, prog_count=4, comprehensive_last=True):
    """构造一份合规的 10 题卷：2 输出预测 + 1 追踪 + 1 改错 + 1 代码分析 + 4 编程 + 1 综合挑战。"""
    rec = records_ or records()
    qs = [
        tutil.make_text_question("Q1", 1, ["数据类型"], qtype="output_prediction"),
        tutil.make_text_question("Q2", 2, ["printf输出"], qtype="output_prediction",
                                 stem="分析程序的输出，注意空格与换行的区别。"),
        tutil.make_text_question("Q3", 2, ["数据类型"], qtype="trace",
                                 stem="写出程序运行后的全部输出，并列出每次输出时变量的值。",
                                 requirements=["说明 a++ 与 ++a 的区别"]),
        tutil.make_text_question("Q4", 3, ["if分支"], qtype="code_analysis",
                                 stem="阅读代码，判断哪些语句合法，哪些会导致编译错误。",
                                 requirements=["指出导致编译错误的那一行并解释原因", "解释全局变量与局部变量的关系"]),
    ]
    fix = tutil.make_question("Q5", 3, ["scanf输入"] if "scanf输入" in rec["records"] else ["printf输出"],
                              qtype="fix_bug",
                              desc="下面的代码存在多处问题，请找出所有影响编译或输入输出正确性的问题，并给出修正后的完整程序。")
    fix["code_to_read"] = tutil.WRONG_C
    qs.append(fix)
    for i in range(prog_count):
        qs.append(tutil.make_question(f"Q{6+i}", 3 + (i // 2), ["scanf输入"] if "scanf输入" in rec["records"]
                                      else ["for循环"], qtype="programming"))
    comp = tutil.make_question("Q10", 5, ["for循环", "if分支"], qtype="comprehensive", combined=True)
    qs.append(comp)
    if not comprehensive_last:
        qs.insert(0, qs.pop())
    if bad_type:
        qs[0]["question_type"] = bad_type
    return qs


class TestQuestionTypesAndPaper(unittest.TestCase):
    def test_all_seven_types_known(self):
        self.assertEqual(set(Q.QUESTION_TYPES), {"output_prediction", "trace", "fix_bug", "code_analysis",
                                                 "concept", "programming", "comprehensive"})
        for t, spec in Q.QUESTION_TYPES.items():
            self.assertIn(spec["answer_kind"], {"text", "code"}, t)
            self.assertTrue(spec["cn"])

    def test_unknown_type_rejected(self):
        qs = paper(bad_type="essay")
        errs = Q.validate_question(qs[0], records())
        self.assertTrue(any("question_type 未知" in e for e in errs), errs)

    def test_text_question_needs_code_and_expected_answer(self):
        q = tutil.make_text_question("Q1", 2, ["数据类型"])
        self.assertEqual(Q.validate_question(q, records()), [])
        del q["expected_answer"]
        self.assertTrue(any("expected_answer" in e for e in Q.validate_question(q, records())))
        q2 = tutil.make_text_question("Q1", 2, ["数据类型"])
        del q2["code_to_read"]
        self.assertTrue(any("code_to_read" in e for e in Q.validate_question(q2, records())))

    def test_code_analysis_needs_requirements(self):
        q = tutil.make_text_question("Q1", 3, ["数据类型"], qtype="code_analysis")
        del q["requirements"]
        self.assertTrue(any("requirements" in e for e in Q.validate_question(q, records())))

    def test_comprehensive_needs_self_check(self):
        q = tutil.make_question("Q1", 5, ["for循环"], qtype="comprehensive")
        self.assertTrue(q["self_check_questions"])
        self.assertEqual(Q.validate_question(q, records()), [])
        del q["self_check_questions"]
        self.assertTrue(any("self_check_questions" in e for e in Q.validate_question(q, records())))

    def test_answer_kind_mismatch_rejected(self):
        q = tutil.make_question("Q1", 3, ["for循环"])
        q["answer_kind"] = "text"
        self.assertTrue(any("answer_kind" in e for e in Q.validate_question(q, records())))

    def test_paper_of_ten_passes(self):
        qs = Q.sort_questions(paper())
        rep = Q.check_paper(qs, records())
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertEqual(rep["size"], 10)
        self.assertEqual(rep["type_counts"]["comprehensive"], 1)

    def test_too_many_programming_questions_rejected(self):
        qs = Q.sort_questions(paper(prog_count=7))
        rep = Q.check_paper(qs, records())
        self.assertTrue(any("配比超限" in e for e in rep["errors"]), rep["errors"])

    def test_difficulty_must_increase(self):
        qs = paper()
        qs[0]["difficulty"] = 5
        rep = Q.check_paper(Q.sort_questions(qs, "asc"), records(), order="asc")
        # 排序后一定递增，所以直接构造乱序来验证规则
        shuffled = sorted(qs, key=lambda q: -q["difficulty"])
        rep2 = Q.check_paper(shuffled, records(), order="asc")
        self.assertTrue(any("难度必须递增" in e for e in rep2["errors"]), rep2["errors"])
        self.assertEqual(rep["errors"], [])

    def test_comprehensive_must_be_last(self):
        # 综合挑战题被放到卷首（且不靠难度排序纠正）时必须报错
        rep = Q.check_paper(paper(comprehensive_last=False), records(), order="asc")
        self.assertTrue(any("综合挑战题必须放在最后" in e for e in rep["errors"]), rep["errors"])

    def test_paper_build_end_to_end(self):
        root = tutil.tmp("plc-paper-")
        try:
            S.create(root, "s1", "c")
            S.set_field(root, "s1", scope_confirmed=True)
            S.advance(root, "s1", "analysis")
            S.advance(root, "s1", "scope_confirmed")
            rep = Q.build(root, "s1", paper(), records(), size=10)
            self.assertTrue(rep["ok"], rep["errors"])
            self.assertEqual(rep["question_count"], 10)
            self.assertEqual(rep["paper"]["type_counts"]["comprehensive"], 1)
            self.assertEqual(rep["sequence"][-1]["id"], "Q10")
            self.assertEqual([x["difficulty"] for x in rep["sequence"]],
                             sorted(x["difficulty"] for x in rep["sequence"]))
            kinds = {x["id"]: x["answer_kind"] for x in rep["sequence"]}
            self.assertEqual(kinds["Q1"], "text")
            self.assertEqual(kinds["Q10"], "code")
        finally:
            tutil.rm(root)

    def test_all_programming_paper_warns(self):
        qs = [tutil.make_question(f"Q{i}", 3, ["for循环"]) for i in range(1, 11)]
        rep = Q.check_paper(qs, records())
        self.assertTrue(any("读程序写输出" in w for w in rep["warnings"]), rep["warnings"])
        self.assertTrue(any("都是编程题" in w for w in rep["warnings"]))

    def test_expected_answer_is_private(self):
        root = tutil.tmp("plc-priv-")
        try:
            S.create(root, "s1", "c")
            S.set_field(root, "s1", scope_confirmed=True)
            S.advance(root, "s1", "analysis")
            S.advance(root, "s1", "scope_confirmed")
            qs = paper()
            marker = "SECRET_EXPECTED_ANSWER_991"
            qs[0]["expected_answer"] = marker + "\n" + "x" * 30
            Q.build(root, "s1", qs, records(), size=len(qs))
            pub = (S.session_dir(root, "s1") / "questions.public.json").read_text(encoding="utf-8")
            priv = (S.session_dir(root, "s1") / "answers.private.json").read_text(encoding="utf-8")
            self.assertNotIn(marker, pub)
            self.assertNotIn("expected_answer", pub)
            self.assertIn(marker, priv)
        finally:
            tutil.rm(root)


class TestHintsAndBulkSubmit(unittest.TestCase):
    """提示三级（逐级、不跳级、永不给答案）与一次性提交。"""

    def setUp(self):
        self.root = tutil.tmp("plc-hint-")
        S.create(self.root, "s1", "c")
        S.set_field(self.root, "s1", scope_confirmed=True)
        S.advance(self.root, "s1", "analysis")
        S.advance(self.root, "s1", "scope_confirmed")
        self.qs = qset()
        for q in self.qs:
            q["reference_solution"] = f"int main(void){{ /*{MARK_SOL}*/ }}"
        Q.build(self.root, "s1", self.qs, records(), size=len(self.qs))

    def tearDown(self):
        tutil.rm(self.root)

    def _hint(self, qid, level):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = plc_cli.main(["hint", "--root", str(self.root), "--sid", "s1",
                               "--qid", qid, "--level", level])
        return rc, buf.getvalue()

    def test_cannot_skip_levels(self):
        rc, out = self._hint("Q1", "L2")
        self.assertEqual(rc, 9)
        self.assertIn("必须先申请 L1", out)
        rc, out = self._hint("Q1", "L3")
        self.assertEqual(rc, 9)

    def test_ladder_grants_one_level_at_a_time(self):
        rc, out = self._hint("Q1", "L1")
        self.assertEqual(rc, 0)
        self.assertIn("考察", out)
        rc2, _ = self._hint("Q1", "L1")
        self.assertEqual(rc2, 9, "同一级不重复给")
        rc3, out3 = self._hint("Q1", "L2")
        self.assertEqual(rc3, 0)
        self.assertIn("L2", out3)
        rc4, _ = self._hint("Q1", "L3")
        self.assertEqual(rc4, 0)
        log = S.load_state(self.root, "s1")["hint_log"]
        self.assertEqual([e["level"] for e in log if e["qid"] == "Q1"], ["L1", "L2", "L3"])

    def test_hint_output_never_contains_answer(self):
        for lvl in ("L1", "L2", "L3"):
            rc, out = self._hint("Q1", lvl)
            self.assertEqual(rc, 0, out)
            self.assertNotIn(MARK_SOL, out)
            self.assertNotIn("reference_solution", out)

    def test_hint_refused_before_submission_phase(self):
        S.create(self.root, "s2", "c")
        S.set_field(self.root, "s2", scope_confirmed=True)
        S.advance(self.root, "s2", "analysis")
        S.advance(self.root, "s2", "scope_confirmed")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = plc_cli.main(["hint", "--root", str(self.root), "--sid", "s2",
                               "--qid", "Q1", "--level", "L1"])
        self.assertEqual(rc, 9)
        self.assertIn("不提供提示", buf.getvalue())

    def test_bulk_submit(self):
        d = tutil.tmp("plc-submits-")
        try:
            (d / "Q1.c").write_text(tutil.CORRECT_C, encoding="utf-8")
            (d / "Q2.c").write_text(tutil.WRONG_C, encoding="utf-8")
            (d / "readme.txt").write_text("不是答案", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = plc_cli.main(["submit-dir", "--root", str(self.root), "--sid", "s1",
                                   "--dir", str(d), "--language", "c"])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(sorted(out["saved"]), ["Q1.c", "Q2.c"])
            self.assertEqual(out["unknown_files"], ["readme.txt"])
            self.assertEqual(sorted(out["submitted"]), ["Q1", "Q2"])
            self.assertEqual(out["missing"], ["Q3"])
            self.assertEqual(out["phase"], "awaiting_submission")
            self.assertIn("int main", S.load_submission(self.root, "s1", "Q1"))
        finally:
            tutil.rm(d)


class TestAnswerSecrecy(unittest.TestCase):
    def setUp(self):
        self.root = tutil.tmp("plc-secret-")
        S.create(self.root, "s1", "c")
        S.set_field(self.root, "s1", scope_confirmed=True)
        S.advance(self.root, "s1", "analysis")
        S.advance(self.root, "s1", "scope_confirmed")
        self.qs = qset()
        for q in self.qs:
            q["reference_solution"] = f'#include <stdio.h>\nint main(void){{ /*{MARK_SOL}*/ return 0; }}'
            q["tests"] = [{"name": "hidden", "stdin": "9\n", "expected_stdout": MARK_OUT + "\n"}]
            q["explanation"] = MARK_SOL + " 的讲解"
        self.rep = Q.build(self.root, "s1", self.qs, records(), size=len(self.qs))

    def tearDown(self):
        tutil.rm(self.root)

    def test_public_file_has_no_answers(self):
        pub = (S.session_dir(self.root, "s1") / "questions.public.json").read_text(encoding="utf-8")
        self.assertNotIn(MARK_SOL, pub)
        self.assertNotIn(MARK_OUT, pub)
        for key in ("reference_solution", "tests", "explanation", "better_approach"):
            self.assertNotIn(f'"{key}"', pub)

    def test_private_file_has_answers(self):
        priv = (S.session_dir(self.root, "s1") / "answers.private.json").read_text(encoding="utf-8")
        self.assertIn(MARK_SOL, priv)
        self.assertIn(MARK_OUT, priv)

    def test_private_unreadable_before_grading(self):
        self.assertEqual(S.phase(self.root, "s1"), "awaiting_submission")
        with self.assertRaises(S.SessionError) as cm:
            S.read_private(self.root, "s1")
        self.assertIn("禁止读取标准答案", str(cm.exception))

    def test_inspect_and_cli_output_clean(self):
        text = json.dumps(S.inspect(self.root, "s1"), ensure_ascii=False)
        self.assertNotIn(MARK_SOL, text)
        self.assertNotIn(MARK_OUT, text)
        self.assertTrue(text)

    def test_no_leak_reported(self):
        self.assertEqual(self.rep["leaks"], [])
        self.assertEqual(self.rep["leaks_after_write"], [])

    def test_leak_scan_catches_answer_in_description(self):
        S.create(self.root, "s2", "c")
        S.set_field(self.root, "s2", scope_confirmed=True)
        S.advance(self.root, "s2", "analysis")
        S.advance(self.root, "s2", "scope_confirmed")
        bad = qset()
        bad[0]["description"] = "参考实现：\n" + bad[0]["reference_solution"]
        with self.assertRaises(Q.QuestionError) as cm:
            Q.build(self.root, "s2", bad, records(), size=len(bad))
        self.assertIn("答案泄漏", str(cm.exception))
        pub = S.session_dir(self.root, "s2") / "questions.public.json"
        self.assertFalse(pub.exists(), "泄漏时必须什么都不写")

    def test_hint_pseudocode_is_not_a_leak(self):
        """L3 提示是自然语言伪代码，不得被误判成泄漏（真实踩过的坑）。"""
        q = tutil.make_question("Q1", 4, ["if分支"], required=["if分支"])
        q["reference_solution"] = ("#include <stdio.h>\nint main(void){\n    char ch;\n"
                                   "    if (ch >= 'A' && ch <= 'Z') {\n        ch = ch + 32;\n    }\n"
                                   "    return 0;\n}\n")
        q["hints"]["L3"] = "if (ch>='A'&&ch<='Z') 转小写 +32；else if (ch>='a'&&ch<='z') 转大写 -32"
        pub = S.strip_private(q)
        priv = {k: v for k, v in q.items() if k in S.PRIVATE_KEYS}
        self.assertEqual(Q.leak_scan([pub], {"Q1": priv}), [])

    def test_fix_bug_shared_lines_are_not_leaks(self):
        """改错题的待读代码与修复后程序必然共享未改动的行，不能误判成泄漏。"""
        q = tutil.make_question("Q1", 3, ["for循环"], qtype="fix_bug")
        q["code_to_read"] = ("#include <stdio.h>\nint main(void){\n    int n;\n"
                             "    scanf(\"%d\", n);\n    printf(\"%d\\n\", n * 3);\n"
                             "    return 0;\n}\n")
        q["reference_solution"] = ("#include <stdio.h>\nint main(void){\n    int n;\n"
                                   "    if (scanf(\"%d\", &n) != 1) return 1;\n"
                                   "    printf(\"%d\\n\", n * 3);\n    return 0;\n}\n")
        pub = S.strip_private(q)
        priv = {k: v for k, v in q.items() if k in S.PRIVATE_KEYS}
        self.assertEqual(Q.leak_scan([pub], {"Q1": priv}), [],
                         "共享的未改动行不应判为泄漏")

    def test_pasted_code_line_is_a_leak(self):
        q = tutil.make_question("Q1", 4, ["if分支"], required=["if分支"])
        q["reference_solution"] = ("#include <stdio.h>\nint main(void){\n"
                                   "    double fee = 10 * 2 + (w - 10) * 3;\n    return 0;\n}\n")
        q["stem"] = "提示：可以直接写 double fee = 10 * 2 + (w - 10) * 3; 这一行"
        pub = S.strip_private(q)
        priv = {k: v for k, v in q.items() if k in S.PRIVATE_KEYS}
        leaks = Q.leak_scan([pub], {"Q1": priv})
        self.assertTrue(leaks, "原样贴进题面的代码行必须被判为泄漏")
        self.assertEqual(leaks[0]["match"], "raw")

    def test_strip_private_is_recursive(self):
        payload = {"a": [{"tests": [1], "id": "Q1", "nested": {"reference_solution": "x"}}]}
        clean = S.strip_private(payload)
        self.assertNotIn("tests", clean["a"][0])
        self.assertNotIn("reference_solution", clean["a"][0]["nested"])
        S.assert_no_private(clean)
        with self.assertRaises(S.SessionError):
            S.assert_no_private(payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
