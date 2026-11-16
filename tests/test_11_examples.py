#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例文件自检：保证 tests/fixtures 里的示例始终合法、可跑通、不超纲（示例腐烂就报警）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import tutil
import analyze as A
import parse_notes as P
import mastery as M
import questions as Q
import reading as RD
import sandbox_run as SBX

FX = Path(__file__).resolve().parent / "fixtures"


def L(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class TestExampleFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = P.parse([str(FX / "sample-note.md")])
        cls.records = {"version": 1, "cold_start": True, "records": {}}
        A.analyze(cls.report, cls.records)
        cls.questions = L("questions.example.json")["questions"]
        cls.notes = L("notes.example.json")
        cls.reading = L("reading.example.json")["items"]

    def test_sample_note_covers_all_example_knowledge_points(self):
        learned = {k["kp"] for k in self.report["knowledge"]}
        needed = {kp for q in self.questions for kp in q["knowledge_points"]}
        needed |= {kp for it in self.reading if not it.get("preview") for kp in it["knowledge_points"]}
        self.assertEqual(needed - learned, set(),
                         f"示例笔记必须覆盖示例题目/精读用到的知识点，否则示例本身就是超纲的：{sorted(needed - learned)}")
        self.assertIn("c", self.report["languages"])

    def test_example_questions_are_valid_and_in_scope(self):
        errs = []
        for q in self.questions:
            errs += Q.validate_question(q, self.records)
        self.assertEqual(errs, [], f"示例题目必须能通过校验：{errs}")

    def test_example_paper_composition(self):
        qs = Q.sort_questions(self.questions, "asc")
        rep = Q.check_paper(qs, self.records)
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertEqual(rep["size"], 10)
        self.assertEqual([q["id"] for q in qs], [f"Q{i}" for i in range(1, 11)],
                         "题号顺序应与「第 N 题」一致")
        self.assertEqual(qs[-1]["question_type"], "comprehensive")
        counts = rep["type_counts"]
        self.assertGreaterEqual(counts.get("output_prediction", 0), 1)
        self.assertGreaterEqual(counts.get("programming", 0), 2)
        self.assertTrue(any(k in counts for k in ("trace", "code_analysis", "concept")),
                        "试卷应混入文本作答题型")

    def test_example_questions_have_private_answers(self):
        for q in self.questions:
            kind = Q.answer_kind(q)
            if kind == "code":
                self.assertTrue(q.get("reference_solution"), f'{q["id"]} 缺参考程序')
                self.assertTrue(q.get("tests"), f'{q["id"]} 缺测试用例')
                for t in q["tests"]:
                    self.assertIn("stdin", t)
                    self.assertIn("expected_stdout", t)
            else:
                self.assertTrue(q.get("expected_answer"), f'{q["id"]} 缺期望答案')
            self.assertTrue(q.get("explanation"), f'{q["id"]} 缺逐步讲解')
            self.assertTrue(q.get("related_notes"), f'{q["id"]} 缺相关笔记链接')
            self.assertTrue(q.get("hints", {}).get("L1"), f'{q["id"]} 缺 L1 提示')

    def test_example_documents_have_structure_fields(self):
        for q in self.questions:
            self.assertTrue(q.get("stem"))
            self.assertTrue(q.get("title"))
            if q["question_type"] in ("output_prediction", "trace", "fix_bug", "code_analysis"):
                self.assertTrue(q.get("code_to_read"), f'{q["id"]} 是读程序题，必须有待读代码')
            if q["question_type"] == "comprehensive":
                self.assertEqual(len(q["self_check_questions"]), 3)
                self.assertEqual(len(q["self_check_answers"]), 3)

    def test_example_notes_cover_required_fields(self):
        for q in self.questions:
            qid = q["id"]
            if qid not in self.notes:
                continue                      # 完全正确的题可以只写 error_location=无明显错误
            n = self.notes[qid]
            for f in ("error_location", "error_reason", "suggestion"):
                self.assertIn(f, n, f"{qid} 的评语缺 {f}")
            if Q.judge_mode(q) == "manual":
                self.assertIn("manual_correct", n, f"{qid} 是人工判定题，必须给 manual_correct")
                self.assertIn("points_hit", n)
        for dim in ("知识理解", "代码编写", "逻辑思维", "知识点掌握", "薄弱环节", "训练建议"):
            self.assertTrue(self.notes["overall"].get(dim), f"整体评价缺 {dim}")

    def test_example_meta_has_summary_content(self):
        meta = L("practice-meta.example.json")
        for f in ("date", "title", "companion_note", "progress_analysis", "key_points",
                  "kp_table", "topic_links", "improvements", "good_points", "next_steps"):
            self.assertTrue(meta.get(f), f"meta 缺 {f}")
        self.assertEqual(len(meta["improvements"]), 5)
        self.assertTrue(all(isinstance(x, dict) and x.get("kp") and x.get("point")
                            for x in meta["kp_table"]))

    def test_example_reading_list(self):
        rep = RD.check_list(self.reading, self.records)
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertEqual(len(self.reading), 10)
        self.assertTrue(self.reading[-1].get("preview"), "预习篇应放在最后")
        self.assertTrue(self.reading[-1].get("preview_uses"))
        rmeta = L("reading-meta.example.json")
        for f in ("selection_basis", "companion_note", "how_to_read", "title", "date"):
            self.assertTrue(rmeta.get(f), f"reading meta 缺 {f}")

    def test_sample_answers_are_real_files(self):
        for name in ("sample-answer-correct.c", "sample-answer-with-bug.c"):
            code = (FX / name).read_text(encoding="utf-8")
            self.assertIn("int main", code)
            self.assertIn("#include <stdio.h>", code)
        self.assertTrue((FX / "sample-answer-text.txt").read_text(encoding="utf-8").strip())

    def test_records_start_as_cold_start_estimates(self):
        self.assertTrue(self.records["cold_start"])
        for r in self.records["records"].values():
            self.assertLessEqual(r["state_index"], M.IDX["待检验"])
            self.assertTrue(r["estimated"])


@unittest.skipUnless(tutil.has_toolchain("c"), "未安装 gcc，跳过示例代码的实跑校验")
class TestExamplesActuallyRun(unittest.TestCase):
    """示例不能腐烂：参考程序必须通过自己的测试用例；读程序题的期望答案必须等于真实输出。"""

    @classmethod
    def setUpClass(cls):
        cls.questions = L("questions.example.json")["questions"]
        cls.reading = L("reading.example.json")["items"]

    def test_reference_solutions_pass_own_tests(self):
        for q in self.questions:
            if Q.answer_kind(q) != "code":
                continue
            with self.subTest(qid=q["id"]):
                r = SBX.run_tests(q["reference_solution"], q.get("language", "c"), q["tests"], timeout=25)
                self.assertTrue(r["ran"], f'{q["id"]} 参考程序无法运行：{r.get("reason")}')
                self.assertEqual(r["passed"], r["total"], f'{q["id"]} 参考程序未通过自己的测试用例')

    def test_text_expected_answers_match_real_output(self):
        for q in self.questions:
            if Q.judge_mode(q) != "auto" or Q.answer_kind(q) != "text":
                continue
            with self.subTest(qid=q["id"]):
                r = SBX.run_tests(q["code_to_read"], "c",
                                  [{"name": "x", "stdin": q.get("stdin", ""),
                                    "expected_stdout": "", "compare": "contains"}], timeout=25)
                self.assertTrue(r["ran"], r.get("reason"))
                real = r["cases"][0]["actual_stdout"].replace("\r\n", "\n").rstrip("\n")
                self.assertEqual(real, q["expected_answer"].strip(),
                                 f'{q["id"]} 的期望答案与真实输出不一致（示例已腐烂）')

    def test_reading_programs_all_compile_and_run(self):
        for i, it in enumerate(self.reading, 1):
            with self.subTest(item=i):
                out = RD.verify_item(it, run=True, timeout=25)
                self.assertTrue(out["compile_ok"], f'第 {i} 篇编译/运行失败：{out["verify_line"]}')
                self.assertIsNotNone(out["actual_output"])
                self.assertFalse(out.get("compile_warnings"), f'第 {i} 篇有编译警告：{out["compile_warnings"]}')


if __name__ == "__main__":
    unittest.main(verbosity=2)
