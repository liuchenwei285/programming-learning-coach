#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 新增验收：固定 10 题、难度顺序、范围一次性确认、哈希缓存、答案保密、近似查重、薄弱点比例。

对应任务书「五、验收 2」的 6 条 + 3 条配套（近似重复、薄弱点比例、预告句/列举句降级）。
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import time
import unittest
from pathlib import Path

import tutil
import analyze as A
import mastery as M
import parse_notes as P
import plc_cli
import questions as Q
import session_store as S

REC = {"for循环": "学习中", "if分支": "学习中", "数据类型": "待检验",
       "printf输出": "基本掌握", "scanf输入": "学习中", "变量": "学习中",
       "指针基础": "未学习"}


def records():
    return tutil.make_records(REC)


def q10(**over):
    """10 题卷：题型配比合规、场景各不相同、难度递增、综合挑战收尾。"""
    plan = [("Q1", 1, "output_prediction", False), ("Q2", 2, "output_prediction", False),
            ("Q3", 2, "trace", False), ("Q4", 2, "concept", False),
            ("Q5", 3, "code_analysis", False), ("Q6", 3, "fix_bug", False),
            ("Q7", 3, "programming", False), ("Q8", 4, "programming", False),
            ("Q9", 4, "programming", False), ("Q10", 5, "comprehensive", True)]
    out = []
    for qid, diff, qtype, combined in plan:
        if Q.QUESTION_TYPES[qtype]["answer_kind"] == "text":
            q = tutil.make_text_question(qid, diff, ["scanf输入"], qtype=qtype)
        else:
            q = tutil.make_question(qid, diff, ["for循环", "if分支"], qtype=qtype, combined=combined)
            if qtype == "fix_bug":
                q["code_to_read"] = tutil.WRONG_C
                q["stem"] = q["stem"] + " 下面这段代码有多处问题，请找出全部并提交修正后的完整程序。"
        q.update(over.get(qid, {}))
        out.append(q)
    return out


class TestFixedTen(unittest.TestCase):
    def test_nine_questions_rejected(self):
        rec = records()
        rep = Q.check_paper(Q.sort_questions(q10()[:9], "asc"), rec)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("题量必须正好 10 题" in e for e in rep["errors"]), rep["errors"])

    def test_eleven_questions_rejected(self):
        qs = q10() + [tutil.make_question("Q11", 5, ["for循环"], qtype="programming")]
        rep = Q.check_paper(Q.sort_questions(qs, "asc"), records())
        self.assertFalse(rep["ok"])
        self.assertTrue(any("题量必须正好 10 题" in e for e in rep["errors"]))

    def test_ten_questions_pass(self):
        rep = Q.check_paper(Q.sort_questions(q10(), "asc"), records())
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertEqual(rep["size"], 10)

    def test_build_rejects_wrong_size_end_to_end(self):
        root = tutil.tmp("plc-v2-size-")
        try:
            S.create(root, "s1", "c")
            S.set_field(root, "s1", scope_confirmed=True)
            S.advance(root, "s1", "analysis")
            S.advance(root, "s1", "scope_confirmed")
            with self.assertRaises(Q.QuestionError) as cm:
                Q.build(root, "s1", q10()[:9], records())      # 不传 size，走生产默认 10
            self.assertIn("题量必须正好 10 题", str(cm.exception))
            self.assertFalse((S.session_dir(root, "s1") / "questions.public.json").exists(),
                             "题量不对时必须什么都不写")
        finally:
            tutil.rm(root)


class TestDifficultyOrder(unittest.TestCase):
    def test_asc_is_default_and_strictly_checkable(self):
        qs = Q.sort_questions(q10(), "asc")
        diffs = [q["difficulty"] for q in qs]
        self.assertEqual(diffs, sorted(diffs), "默认必须是难度从低到高")
        self.assertEqual(qs[0]["id"], "Q1")
        self.assertEqual(qs[-1]["question_type"], "comprehensive", "综合挑战题必须收尾")

    def test_desc_order_supported(self):
        qs = Q.sort_questions(q10(), "desc")
        diffs = [q["difficulty"] for q in qs]
        self.assertEqual(diffs, sorted(diffs, reverse=True), "题 1 最难时用 --order desc")
        self.assertEqual(qs[0]["question_type"], "comprehensive")

    def test_unordered_paper_rejected(self):
        qs = q10()
        qs[0]["difficulty"], qs[-1]["difficulty"] = 5, 1
        rep = Q.check_paper(qs, records(), order="asc")
        self.assertTrue(any("难度必须递增" in e for e in rep["errors"]), rep["errors"])


class TestScopeConfirmationOnce(unittest.TestCase):
    """v3：范围自动锁定 —— T1~T3 可用、T4 一律按未学排除，永不询问学习者。"""

    def setUp(self):
        self.dir = tutil.tmp("plc-v3-scope-")
        self.extra = []

    def tearDown(self):
        tutil.rm(self.dir)

    def _note(self, name="n.md", text=None):
        return tutil.write(self.dir / name, text or (
            "# C 笔记\n\n## 数据类型\n\nint / char / double 是基本类型，用 sizeof 查看字节数。\n\n"
            "## 变量\n\n变量先声明后使用。\n"))

    def test_unambiguous_kp_does_not_trigger_question(self):
        rep = P.parse([str(self._note())])
        res = A.analyze(rep, {"version": 1, "cold_start": True, "records": {}})
        self.assertIn("数据类型", res["usable_kps"])
        self.assertEqual(res["ambiguous"], [], "v3 不产生需要确认的歧义项")
        self.assertTrue(res["scope_current"])

    def test_weak_kp_is_excluded_not_asked(self):
        note = self._note("m.md", "# 笔记\n\n## 分支\n\nif / else 用来判断。\n\n"
                                   "后面学到循环时还会遇到 while 和 for。\n")
        rep = P.parse([str(note)])
        res = A.analyze(rep, {"version": 1, "cold_start": True, "records": {}})
        self.assertIn("for循环", res["excluded_weak_kps"], "T4 一律按未学排除")
        self.assertIn("while循环", res["excluded_weak_kps"])
        self.assertEqual(res["ambiguous"], [])
        self.assertEqual(res["needs_confirmation"], [])
        self.assertEqual(len(A.ambiguous_table(res).splitlines()), 1, "只给一行不阻塞的提示")

    def test_no_repeat_question_after_lock(self):
        note = self._note("m.md", "# 笔记\n\n## 分支\n\nif / else 用来判断。\n\n"
                                   "后面学到循环时还会遇到 while 和 for。\n")
        rep = P.parse([str(note)])
        rec = {"version": 1, "cold_start": True, "records": {}}
        first = A.analyze(rep, rec)
        self.assertTrue(first["excluded_weak_kps"])
        second = A.analyze(rep, rec)
        self.assertTrue(second["scope_current"], "自动锁定后始终不再询问")
        self.assertEqual(second["needs_confirmation"], [])
        self.assertEqual(second["excluded_weak_kps"], first["excluded_weak_kps"], "排除集合稳定")

    def test_new_note_only_reports_new_weak_items(self):
        note = self._note("m.md", "# 笔记\n\n## 分支\n\nif / else 用来判断。\n\n"
                                   "后面学到循环时还会遇到 while 和 for。\n")
        rep = P.parse([str(note)])
        rec = {"version": 1, "cold_start": True, "records": {}}
        A.analyze(rep, rec)
        prev = ["for循环", "while循环"]
        tutil.write(self.dir / "新笔记.md",
                    "# 新笔记\n\n## 数据类型\n\n继续讲 int 与 double。\n\n- **结构体 struct** —— 自定义复合类型\n")
        rep2 = P.parse([str(self.dir)])
        third = A.analyze(rep2, rec)
        self.assertIn("结构体", third["excluded_weak_kps"])
        new = [k for k in third["excluded_weak_kps"] if k not in prev]
        self.assertIn("结构体", new, "只有新增弱证据项需要提示")

    def test_scope_confirm_is_noop_and_never_blocks(self):
        rep = P.parse([str(self._note())])
        res = A.analyze(rep, {"version": 1, "cold_start": True, "records": {}})
        rec = {"version": 1, "cold_start": True, "records": {}}
        rpath = self.dir / "records.json"
        M.save(rpath, rec)
        apath = self.dir / "analysis.json"
        apath.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        S.create(self.dir / "s", "s1", "c")
        S.advance(self.dir / "s", "s1", "analysis")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = plc_cli.main(["scope-confirm", "--root", str(self.dir / "s"), "--sid", "s1",
                               "--records", str(rpath), "--analysis", str(apath), "--auto"])
        self.assertEqual(rc, 0, "v3 取消人工门闩，scope-confirm 永远不阻塞")
        self.assertEqual(S.phase(self.dir / "s", "s1"), "scope_confirmed")


class TestNoteHashCache(unittest.TestCase):
    def setUp(self):
        self.dir = tutil.tmp("plc-v2-cache-")
        self.note = tutil.write(self.dir / "n.md", "# 数据类型\n\nint 与 double。\n\n## 变量\n\n先声明后使用。\n")
        self.cache = self.dir / "cache"

    def tearDown(self):
        tutil.rm(self.dir)

    def test_second_parse_hits_cache(self):
        t0 = time.perf_counter()
        r1 = P.parse([str(self.note)], cache_dir=self.cache)
        t_first = time.perf_counter() - t0
        t0 = time.perf_counter()
        r2 = P.parse([str(self.note)], cache_dir=self.cache)
        t_cached = time.perf_counter() - t0
        self.assertFalse(r1["cache"]["hit"])
        self.assertTrue(r2["cache"]["hit"], "笔记内容没变必须命中缓存")
        self.assertEqual(r1["fingerprint"], r2["fingerprint"])
        self.assertEqual(r1["knowledge"], r2["knowledge"], "缓存结果必须与首次一致")
        self.assertLess(t_cached, t_first, f"缓存应更快：{t_cached:.4f}s vs {t_first:.4f}s")

    def test_cache_invalidated_when_note_changes(self):
        P.parse([str(self.note)], cache_dir=self.cache)
        tutil.write(self.note, "# 数据类型\n\nint 与 double。\n\n## 变量\n\n先声明后使用。\n\n## 指针\n\n指针保存地址。\n")
        r = P.parse([str(self.note)], cache_dir=self.cache)
        self.assertFalse(r["cache"]["hit"], "笔记变了必须重新解析")
        self.assertIn("指针基础", [k["kp"] for k in r["knowledge"]])

    def test_cache_invalidated_when_glossary_changes(self):
        g = self.dir / "g.json"
        g.write_text(json.dumps({"entries": [{"id": "x", "kp": "数据类型", "lang": "c", "core": True,
                                              "names": ["int"], "weak_names": []}]}), encoding="utf-8")
        P.parse([str(self.note)], str(g), self.cache)
        g.write_text(json.dumps({"entries": [{"id": "x", "kp": "数据类型", "lang": "c", "core": True,
                                              "names": ["int", "double"], "weak_names": []}]}), encoding="utf-8")
        r = P.parse([str(self.note)], str(g), self.cache)
        self.assertFalse(r["cache"]["hit"], "词库变了缓存必须失效")

    def test_analysis_cache_hits(self):
        rep = P.parse([str(self.note)], cache_dir=self.cache)
        rec = {"version": 1, "cold_start": True, "records": {}}
        a1 = A.analyze_cached(rep, rec, None, self.cache)
        a2 = A.analyze_cached(rep, rec, None, self.cache)
        self.assertFalse(a1["cache"]["hit"])
        self.assertTrue(a2["cache"]["hit"])


class TestNoAnswerLeakInQuestioning(unittest.TestCase):
    MARK = "SECRET_REFERENCE_SOLUTION_v2_9527"

    def test_questioning_stage_output_has_no_answer(self):
        root = tutil.tmp("plc-v2-leak-")
        try:
            S.create(root, "s1", "c")
            S.set_field(root, "s1", scope_confirmed=True)
            S.advance(root, "s1", "analysis")
            S.advance(root, "s1", "scope_confirmed")
            qs = q10()
            for q in qs:
                q["reference_solution"] = f'#include <stdio.h>\nint main(void){{ /*{self.MARK}*/ return 0; }}'
                q["tests"] = [{"name": "hidden", "stdin": "1\n",
                               "expected_stdout": "HIDDEN_OUTPUT_317\n"}]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rep = Q.build(root, "s1", qs, records())
            out = buf.getvalue()
            self.assertNotIn(self.MARK, out, "出题阶段 stdout 不能有标准答案")
            self.assertNotIn("HIDDEN_OUTPUT_317", out)
            self.assertNotIn("reference_solution", out)
            pub = (S.session_dir(root, "s1") / "questions.public.json").read_text(encoding="utf-8")
            self.assertNotIn(self.MARK, pub)
            self.assertTrue(rep["leaks"] == [] and rep["leaks_after_write"] == [])
            with self.assertRaises(S.SessionError):
                S.read_private(root, "s1")          # 出题阶段仍然读不到
        finally:
            tutil.rm(root)


class TestAntiRepetitionAndWeakPoints(unittest.TestCase):
    def test_near_duplicate_in_same_paper_rejected(self):
        qs = q10()
        qs[1]["stem"] = qs[0]["stem"]                 # 换题号但题面一样
        qs[1]["code_to_read"] = qs[0]["code_to_read"]
        rep = Q.check_paper(qs, records())
        self.assertTrue(any("机械重复" in e for e in rep["errors"]), rep["errors"])

    def test_history_duplicate_rejected(self):
        qs = q10()
        hist = [{"id": "H1", "stem": qs[0]["stem"], "code_to_read": qs[0]["code_to_read"]}]
        rep = Q.check_paper(qs, records(), history=hist)
        self.assertTrue(any("与历史题目机械重复" in e for e in rep["errors"]), rep["errors"])

    def test_weak_points_must_be_allocated(self):
        rec = records()
        rec["records"]["if分支"]["wrong_questions"] = ["上次Q2"]
        rec["records"]["if分支"]["practice_evidence"] = [
            {"session_id": "s0", "question_id": "上次Q2", "result": "wrong"}]
        qs = q10()
        for q in qs:                                   # 全部避开薄弱点
            q["knowledge_points"] = ["for循环"]
            q["required_knowledge_points"] = ["for循环"]
        rep = Q.check_paper(qs, rec)
        self.assertTrue(any("薄弱点" in e for e in rep["errors"]), rep["errors"])

    def test_scope_scarce_triggers_must_tell_learner(self):
        rec = {"version": 1, "cold_start": True, "records": {
            k: {**M.blank(k, "c"), "state": "学习中", "state_index": 1, "note_evidence": ["x"]}
            for k in ("for循环", "if分支")}}
        rep = Q.check_paper(Q.sort_questions(q10(), "asc"), rec)
        self.assertTrue(rep["scope_scarce"], "可用知识点 2 个 < 10 题")
        self.assertTrue(any("可用知识点偏少" in w for w in rep["warnings"]), rep["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
