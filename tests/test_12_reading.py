#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 10（模式 A）：自编精读程序 —— 每篇都必须真的编译运行，「实际输出」不得编造。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import tutil
import mastery as M
import reading as RD

GOOD_C = ('#include <stdio.h>\n\nint main(void)\n{\n    int a = 7;\n    double b = 2.5;\n'
          '    printf("%d %.1f\\n", a, b / 2);\n    return 0;\n}\n')
INPUT_C = ('#include <stdio.h>\n\nint main(void)\n{\n    int total;\n    scanf("%d", &total);\n'
           '    printf("%02d:%02d:%02d\\n", total / 3600, total % 3600 / 60, total % 60);\n'
           '    return 0;\n}\n')
WARN_C = ('#include <stdio.h>\n\nint main(void)\n{\n    int unused_thing;\n'
          '    printf("hi\\n");\n    return 0;\n}\n')
BAD_C = '#include <stdio.h>\nint main(void){ int a; scanf("%d", a); printf("%d", a) return 0; }\n'

STATES = {"数据类型": "待检验", "printf输出": "待检验", "整数除法与取余": "学习中",
          "for循环": "学习中", "指针基础": "未学习", "结构体": "未学习"}


def recs():
    return tutil.make_records(STATES)


def item(i: int, difficulty: int | None = None, **over) -> dict:
    it = {"title": f"主题{i}", "language": "c",
          "difficulty": difficulty if difficulty is not None else min(5, (i + 1) // 2),
          "why": f"你在第 {i} 题反复出错，这篇专门对着这个薄弱点。",
          "code": GOOD_C, "knowledge_points": ["数据类型"], "related_exercises": f"第 {i} 题",
          "input_spec": "无输入", "stdin": "",
          "predict_questions": ["第 1 行为什么是这个结果？", "如果改成整数除法会怎样？"],
          "watch_points": ["盯住强制转换的位置", "盯住格式符与类型的匹配"],
          "related_notes": [{"target": "C语言", "note": "数据类型"}], "preview": False}
    it.update(over)
    return it


def items10(**over) -> list[dict]:
    return [item(i, **over) for i in range(1, 11)]


class TestReadingValidation(unittest.TestCase):
    def test_valid_item_passes(self):
        self.assertEqual(RD.validate_item(item(1), recs(), 1), [])

    def test_missing_fields(self):
        for f in RD.REQUIRED_FIELDS:
            it = item(1)
            del it[f]
            errs = RD.validate_item(it, recs(), 1)
            self.assertTrue(any(f in e for e in errs), f"{f}: {errs}")

    def test_predict_and_watch_minimum(self):
        it = item(1, predict_questions=["只有一条"])
        self.assertTrue(any("predict_questions" in e for e in RD.validate_item(it, recs(), 1)))
        it2 = item(1, watch_points=["只有一条"])
        self.assertTrue(any("watch_points" in e for e in RD.validate_item(it2, recs(), 1)))

    def test_open_source_fields_rejected_in_self_authored_mode(self):
        it = item(1, url="https://github.com/o/p", path="a.c")
        errs = RD.validate_item(it, recs(), 1)
        self.assertTrue(any("自编模式不得含开源字段" in e for e in errs), errs)

    def test_out_of_scope_rejected_unless_preview(self):
        it = item(1, knowledge_points=["指针基础"])
        errs = RD.validate_item(it, recs(), 1)
        self.assertTrue(any("超纲" in e for e in errs), errs)
        it2 = item(1, knowledge_points=["数据类型"], preview=True)
        self.assertTrue(any("preview_uses" in e for e in RD.validate_item(it2, recs(), 1)))
        it3 = item(1, knowledge_points=["指针基础"], preview=True, preview_uses="用了还没学的指针")
        self.assertEqual(RD.validate_item(it3, recs(), 1), [])

    def test_check_list_size_and_order(self):
        rep = RD.check_list(items10()[:9], recs())
        self.assertTrue(any("必须 10 篇" in e for e in rep["errors"]))
        bad = items10()
        bad[0]["difficulty"], bad[-1]["difficulty"] = 5, 1
        rep2 = RD.check_list(bad, recs())
        self.assertTrue(any("难度必须递增" in e for e in rep2["errors"]))

    def test_preview_must_be_last(self):
        its = items10()
        its[0]["preview"] = True
        its[0]["preview_uses"] = "用了还没学的 while"
        rep = RD.check_list(its, recs())
        self.assertTrue(any("预习篇必须放在最后" in e for e in rep["errors"]), rep["errors"])

    def test_weak_point_coverage_warning(self):
        r = recs()
        r["records"]["for循环"]["wrong_questions"] = ["上次Q1"]
        r["records"]["for循环"]["practice_evidence"] = [{"session_id": "s0", "question_id": "上次Q1",
                                                        "result": "wrong"}]
        rep = RD.check_list(items10(), r)
        self.assertTrue(any("薄弱点未被精读覆盖" in w and "for循环" in w for w in rep["warnings"]),
                        rep["warnings"])
        covered = items10(knowledge_points=["数据类型", "for循环"])
        rep2 = RD.check_list(covered, r)
        self.assertFalse(any("薄弱点未被精读覆盖" in w for w in rep2["warnings"]))

    def test_ten_valid_items_pass(self):
        rep = RD.check_list(items10(), recs())
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertEqual(rep["size"], 10)


@unittest.skipUnless(tutil.has_toolchain("c"), "未安装 gcc")
class TestReadingVerification(unittest.TestCase):
    def test_real_program_output_is_captured(self):
        out = RD.verify_item(item(1), run=True, timeout=25)
        self.assertTrue(out["compile_ok"], out["verify_line"])
        self.assertEqual(out["actual_output"], "7 1.2")
        self.assertIn("编译并运行", out["verify_line"])
        self.assertIn("无警告", out["verify_line"])

    def test_input_and_prefix(self):
        out = RD.verify_item(item(1, code=INPUT_C, stdin="3725\n",
                                 input_spec="输入秒数，如 3725",
                                 output_prefix="输入 3725 → "), run=True, timeout=25)
        self.assertTrue(out["compile_ok"], out["verify_line"])
        self.assertEqual(out["actual_output"], "输入 3725 → 01:02:05")

    def test_compile_failure_is_not_faked(self):
        out = RD.verify_item(item(1, code=BAD_C), run=True, timeout=30)
        self.assertFalse(out["compile_ok"])
        self.assertIsNone(out["actual_output"])
        self.assertIn("未通过验证", out["verify_line"])
        self.assertIn("不编造", out["verify_line"])

    def test_warnings_are_reported(self):
        out = RD.verify_item(item(1, code=WARN_C), run=True, timeout=25)
        self.assertTrue(out["compile_ok"], out["verify_line"])
        self.assertTrue(out["compile_warnings"])
        self.assertIn("有编译警告", out["verify_line"])

    def test_missing_toolchain_degrades(self):
        out = RD.verify_item(item(1, language="cobol"), run=True, timeout=10)
        self.assertFalse(out["compile_ok"])
        self.assertIsNone(out["actual_output"])
        self.assertIn("未通过验证", out["verify_line"])

    def test_no_run_mode(self):
        out = RD.verify_item(item(1), run=False)
        self.assertFalse(out["compile_ok"])
        self.assertIn("本次未运行编译", out["verify_line"])
        self.assertIn("不编造", out["verify_line"])

    def test_summarize_claim_is_honest(self):
        good = RD.verify_item(item(1), run=True, timeout=25)
        bad = RD.verify_item(item(2, code=BAD_C), run=True, timeout=30)
        summary = RD.summarize([good, bad])
        self.assertEqual(summary["compiled_and_run"], 1)
        self.assertEqual(summary["failed"], ["主题2"])
        self.assertIn("1/2 篇通过编译运行验证", summary["claim"])
        all_good = RD.summarize([good, RD.verify_item(item(2), run=True, timeout=25)])
        self.assertIn("全部 2 篇都已编译运行验证", all_good["claim"])
        self.assertIn("没有警告", all_good["claim"])


class TestReadingCli(unittest.TestCase):
    def test_build_writes_verified_json(self):
        work = tutil.tmp("plc-reading-")
        try:
            items = items10()
            if not tutil.has_toolchain("c"):
                for i, it in enumerate(items):
                    it["preview"] = i >= 8
                    it["preview_uses"] = "无工具链，未验证"
            src = work / "reading.json"
            src.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
            rp = work / "records.json"
            M.save(rp, recs())
            out = work / "verified.json"
            rc = RD.main(["build", "--items", str(src), "--records", str(rp), "--out", str(out)])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "self_authored")
            self.assertEqual(len(data["items"]), 10)
            self.assertTrue(data["rules"]["ok"], data["rules"]["errors"])
            for it in data["items"]:
                self.assertIn("verify_line", it)
                if not it["compile_ok"]:
                    self.assertIsNone(it["actual_output"], "没跑通就不许有输出")
                    self.assertIn("不编造", it["verify_line"] + it.get("verify_line", ""))
        finally:
            tutil.rm(work)

    def test_check_rejects_out_of_scope(self):
        work = tutil.tmp("plc-reading2-")
        try:
            items = items10(knowledge_points=["指针基础"])
            src = work / "reading.json"
            src.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
            rp = work / "records.json"
            M.save(rp, recs())
            rc = RD.main(["check", "--items", str(src), "--records", str(rp)])
            self.assertEqual(rc, 3)
        finally:
            tutil.rm(work)


if __name__ == "__main__":
    unittest.main(verbosity=2)
