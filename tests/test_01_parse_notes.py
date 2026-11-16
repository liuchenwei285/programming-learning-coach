#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 1 + 2：笔记解析（md/txt/docx + 降级路径）、知识点提取与「出现≠掌握」。"""
from __future__ import annotations

import unittest
from pathlib import Path

import tutil
import glossary as G
import parse_notes as P
import mastery as M
import analyze as A


class TestParseNotes(unittest.TestCase):
    def setUp(self):
        self.dir = tutil.tmp("plc-parse-")
        self.files = tutil.fixture_notes(self.dir)
        self.rep = P.parse([str(self.dir)])
        self.by_name = {Path(i["path"]).name: i for i in self.rep["inputs"]}

    def tearDown(self):
        tutil.rm(self.dir)

    def test_md_and_txt_parsed(self):
        for name in ("C语言.md", "循环.txt"):
            self.assertEqual(self.by_name[name]["status"], "ok", name)
            self.assertGreater(self.by_name[name]["chars"], 10)

    def test_docx_parsed_with_stdlib(self):
        info = self.by_name["函数.docx"]
        self.assertEqual(info["status"], "ok")
        self.assertIn("zipfile", info["note"])
        self.assertTrue(any(h["text"] == "函数" for h in self.rep["headings"]),
                        "docx 的 Heading 样式应被识别")
        kp = {k["kp"]: k for k in self.rep["knowledge"]}
        self.assertIn("函数", kp)
        self.assertTrue(any(e["source"].endswith(".docx") for e in kp["函数"]["evidence"]),
                        "docx 正文必须真的被读到，而不是只看文件名")

    def test_pdf_degrades_honestly(self):
        info = self.by_name["讲义.pdf"]
        self.assertIn(info["status"], {"ok", "degraded"})
        if info["status"] == "degraded":
            reasons = " ".join(d["reason"] for d in self.rep["degraded"])
            self.assertIn("pypdf", reasons)
            self.assertIn("降级", reasons)

    def test_image_handed_to_model(self):
        imgs = [i["path"] for i in self.rep["image_sources"]]
        self.assertTrue(any(p.endswith("板书.png") for p in imgs))
        self.assertTrue(all(i["status"] == "needs_model_vision" for i in self.rep["image_sources"]))
        self.assertTrue(any("图片" in w for w in self.rep["warnings"]))

    def test_unknown_extension_degrades(self):
        self.assertEqual(self.by_name["notes.xyz"]["status"], "degraded")
        self.assertTrue(any(d["path"].endswith(".xyz") for d in self.rep["degraded"]))

    def test_missing_path_warns(self):
        rep = P.parse([str(self.dir / "不存在.md")])
        self.assertTrue(any("不存在" in w for w in rep["warnings"]))

    def test_code_blocks_and_language(self):
        langs = {b["lang"] for b in self.rep["code_blocks"]}
        self.assertIn("c", langs)
        self.assertIn("c", self.rep["languages"])
        self.assertTrue(any("#include <stdio.h>" in b["code"] for b in self.rep["code_blocks"]))

    def test_every_knowledge_point_has_basis(self):
        self.assertTrue(self.rep["knowledge"])
        for k in self.rep["knowledge"]:
            self.assertTrue(k["evidence"], f'{k["kp"]} 缺少依据')
            ev = k["evidence"][0]
            self.assertTrue(ev["source"] and ev["line"] >= 1 and ev["snippet"])

    def test_pitfalls_collected(self):
        self.assertTrue(any("易错" in p["text"] or "注意" in p["text"] for p in self.rep["pitfalls"]))


class TestGlossaryAntiFalsePositive(unittest.TestCase):
    def setUp(self):
        self.entries = G.load()

    def test_symbol_alias_alone_does_not_count(self):
        lines = ["int a = b * c;", "scanf(\"%d\", &a);", "x = y << 2;"]
        found = {f["kp"] for f in G.match_lines(self.entries, lines, "t.md", langs={"c", "*"})}
        self.assertNotIn("指针基础", found, "只有 * 和 & 不能判定学过指针")
        self.assertNotIn("位运算", found, "只有 << 不能判定学过位运算")
        self.assertNotIn("Python运算符", found)

    def test_word_alias_does_count(self):
        found = {f["kp"] for f in G.match_lines(self.entries, ["指针就是地址", "空指针 NULL"], "t.md",
                                                 langs={"c", "*"})}
        self.assertIn("指针基础", found)

    def test_language_filter(self):
        allowed = G.allowed_langs({"c": 100, "text": 5})
        self.assertIn("c", allowed)
        self.assertNotIn("python", allowed)
        found = {f["kp"] for f in G.match_lines(self.entries, ["if (x) {}", "for (i=0;i<n;i++)"], "t.md",
                                                langs=allowed)}
        self.assertIn("if分支", found)
        self.assertNotIn("Python分支", found)

    def test_word_boundary_no_substring_hit(self):
        pat = G.name_pattern("int")
        self.assertTrue(pat.search("int a;"))
        self.assertIsNone(pat.search("printf(\"%d\", point);"), "int 不应命中 point/printf")

    def test_weak_confidence_for_generic_terms(self):
        found = G.match_lines(self.entries, ["这里讨论了变量和运算符"], "t.md", langs={"c", "*"})
        weak = [f for f in found if f["confidence"] == "weak"]
        self.assertTrue(weak, "只用通用词命中应标 weak")


class TestAppearanceIsNotMastery(unittest.TestCase):
    def test_notes_only_capped_at_pending(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        knowledge = [
            {"kp": "数据类型", "lang": "c", "tier": 1, "in_heading": True, "has_code": True,
             "evidence": [{"source": "n.md", "line": 3, "snippet": "## 数据类型"}]},
            {"kp": "变量", "lang": "c", "tier": 3, "in_heading": False, "has_code": False,
             "evidence": [{"source": "n.md", "line": 9, "snippet": "变量用来存数据"}]},
        ]
        M.from_note_knowledge(data, knowledge)
        self.assertEqual(M.state_of(data, "数据类型"), "待检验")
        self.assertEqual(M.state_of(data, "变量"), "学习中")
        for r in data["records"].values():
            self.assertLessEqual(r["state_index"], M.IDX["待检验"], "仅凭笔记不得高于待检验")
            self.assertTrue(r["estimated"])
        self.assertTrue(data["cold_start"])

    def test_analyze_mastered_empty_on_cold_start(self):
        rep = {"languages": {"c": 10}, "allowed_glossary_langs": ["c", "*"],
               "knowledge": [{"kp": "数据类型", "lang": "c", "tier": 1, "count": 3, "in_heading": True,
                              "has_code": True, "confidence": "strong",
                              "evidence": [{"source": "n.md", "line": 3, "snippet": "## 数据类型"}]}],
               "code_blocks": [], "pitfalls": [], "image_sources": [], "warnings": []}
        res = A.analyze(rep, {"version": 1, "cold_start": True, "records": {}})
        self.assertEqual(res["mastered"], [], "冷启动时「已掌握」必须为空")
        self.assertTrue(res["cold_start"])
        self.assertTrue(any("初始估计" in w for w in res["warnings"]))
        self.assertTrue(any("已掌握" in w for w in res["warnings"]))
        self.assertEqual(res["counts"]["learning"], 1)
        self.assertTrue(res["not_learned"], "词库里未出现的核心知识点应判为未学")
        for it in res["learned"] + res["not_learned"]:
            self.assertTrue(it["basis"], f'{it["kp"]} 缺依据')

    def test_promotion_ladder(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        M.from_note_knowledge(data, [{"kp": "for循环", "lang": "c", "tier": 3, "in_heading": False,
                                      "has_code": False, "evidence": [{"source": "n.md", "line": 1, "snippet": "for"}]}])
        self.assertEqual(M.state_of(data, "for循环"), "学习中")

        M.apply_events(data, [{"kp": "for循环", "session_id": "s1", "question_id": "Q1", "correct": True}])
        self.assertEqual(M.state_of(data, "for循环"), "待检验", "一次答对最多升到待检验")

        M.apply_events(data, [{"kp": "for循环", "session_id": "s1", "question_id": "Q1", "correct": True}])
        self.assertEqual(M.state_of(data, "for循环"), "待检验", "同一题重复答对不算第二次")

        M.apply_events(data, [{"kp": "for循环", "session_id": "s1", "question_id": "Q2", "correct": True}])
        self.assertEqual(M.state_of(data, "for循环"), "基本掌握", "2 道不同题答对 -> 基本掌握")

        M.apply_events(data, [{"kp": "for循环", "session_id": "s2", "question_id": "Q3",
                               "correct": True, "combined": True}])
        self.assertEqual(M.state_of(data, "for循环"), "熟练掌握", "3 题 + 2 次练习 + 组合题 -> 熟练掌握")

    def test_proficient_needs_combined_and_two_sessions(self):
        for events, expect in (
            ([{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True},
              {"kp": "k", "session_id": "s2", "question_id": "Q2", "correct": True},
              {"kp": "k", "session_id": "s2", "question_id": "Q3", "correct": True}], "基本掌握"),
            ([{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True},
              {"kp": "k", "session_id": "s1", "question_id": "Q2", "correct": True},
              {"kp": "k", "session_id": "s1", "question_id": "Q3", "correct": True, "combined": True}],
             "基本掌握"),
            ([{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True},
              {"kp": "k", "session_id": "s2", "question_id": "Q2", "correct": True},
              {"kp": "k", "session_id": "s2", "question_id": "Q3", "correct": True, "combined": True}],
             "熟练掌握"),
        ):
            data = {"version": 1, "cold_start": True, "records": {}}
            M.apply_events(data, events)
            self.assertEqual(M.state_of(data, "k"), expect, events)

    def test_wrong_answer_demotes_one_level(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        M.apply_events(data, [{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True},
                              {"kp": "k", "session_id": "s1", "question_id": "Q2", "correct": True}])
        self.assertEqual(M.state_of(data, "k"), "基本掌握")
        M.apply_events(data, [{"kp": "k", "session_id": "s2", "question_id": "Q3", "correct": False}])
        self.assertEqual(M.state_of(data, "k"), "待检验", "答错降一级")
        self.assertEqual(data["records"]["k"]["history"][-1]["action"], "demote")
        self.assertEqual(M.state_of(data, "k"), "待检验")
        self.assertIn("k", [w["kp"] for w in M.weak_points(data)])

    def test_notes_never_downgrade_practice_confirmed_mastery(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        M.apply_events(data, [{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True},
                              {"kp": "k", "session_id": "s1", "question_id": "Q2", "correct": True}])
        self.assertEqual(M.state_of(data, "k"), "基本掌握")
        M.from_note_knowledge(data, [{"kp": "k", "lang": "c", "tier": 3, "in_heading": False, "has_code": False,
                                      "evidence": [{"source": "n.md", "line": 1, "snippet": "k"}]}])
        self.assertEqual(M.state_of(data, "k"), "基本掌握", "笔记不得覆盖练习已确认的掌握度")
        self.assertFalse(data["records"]["k"]["estimated"])

    def test_demote_floor_keeps_note_backed_kp_usable(self):
        """答错降一级，但笔记教过的知识点不能掉回「未学习」（否则永久无法出题）。"""
        data = {"version": 1, "cold_start": True, "records": {}}
        M.from_note_knowledge(data, [{"kp": "关系与逻辑运算", "lang": "c", "tier": 3, "in_heading": False,
                                      "has_code": False,
                                      "evidence": [{"source": "n.md", "line": 4, "snippet": "逻辑运算"}]}])
        self.assertEqual(M.state_of(data, "关系与逻辑运算"), "学习中")
        M.apply_events(data, [{"kp": "关系与逻辑运算", "session_id": "s1", "question_id": "Q1",
                               "correct": False}])
        self.assertEqual(M.state_of(data, "关系与逻辑运算"), "学习中",
                         "有笔记依据时降级下限是学习中")
        self.assertTrue(M.in_scope(data, "关系与逻辑运算"))
        self.assertEqual(data["records"]["关系与逻辑运算"]["history"][-1]["action"], "demote")

    def test_demote_to_not_learned_without_note_evidence(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        M.apply_events(data, [{"kp": "k", "session_id": "s1", "question_id": "Q1", "correct": True}])
        self.assertEqual(M.state_of(data, "k"), "待检验")
        M.apply_events(data, [{"kp": "k", "session_id": "s2", "question_id": "Q2", "correct": False},
                              {"kp": "k", "session_id": "s2", "question_id": "Q3", "correct": False}])
        self.assertEqual(M.state_of(data, "k"), "未学习", "完全没有笔记依据时可以降回未学习")

    def test_typical_error_scanner(self):
        blocks = [{"source": "a.md", "line": 10, "lang": "c",
                   "code": 'int main(void){ int a; if (a = 1) {} scanf("%d", a); }'}]
        found = {t["rule"] for t in A.scan_typical_errors(blocks)}
        self.assertIn("assign_in_condition", found)
        self.assertIn("scanf_missing_amp", found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
