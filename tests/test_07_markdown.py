#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 7：Markdown 生成、frontmatter 合法性、内容完整性（原题与我的答案不得遗漏）。"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import tutil
import analyze as A
import grading as GR
import mastery as M
import parse_notes as P
import questions as Q
import render_md as R
import session_store as S

LATEX = "本题复杂度为 $O(n)$，累加和为 $$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$"


def full_pipeline(root: Path, sid: str = "s1"):
    """跑通 解析 -> 分析 -> 出题 -> 提交 -> 批改，返回渲染所需的全部数据。"""
    notes = tutil.tmp("plc-notes-")
    tutil.fixture_notes(notes)
    report = P.parse([str(notes)])
    records = {"version": 1, "cold_start": True, "records": {}}
    analysis = A.analyze(report, records)
    in_scope = [kp for kp, r in records["records"].items() if r["state_index"] >= M.SCOPED_MIN_INDEX]
    assert len(in_scope) >= 3, f"fixture 笔记应至少覆盖 3 个知识点，实际 {in_scope}"
    k1, k2, k3 = in_scope[0], in_scope[1], in_scope[2]
    qs = [tutil.make_question("Q1", 3, [k1, k2], required=[k1], lang="c",
                              desc="读入一个整数 n，输出它的两倍。注意 n 的取值范围与输入失败的情况。"),
          tutil.make_question("Q2", 5, [k1, k2, k3], required=[k1, k2], combined=True, lang="c",
                              desc="读入 n，按规则构造数列并求和，要求处理边界 n=1 与 n=100。")]
    S.create(root, sid, "c")
    S.set_field(root, sid, scope_confirmed=True)
    S.advance(root, sid, "analysis")
    S.advance(root, sid, "scope_confirmed")
    Q.build(root, sid, qs, records, size=len(qs))
    S.save_submission(root, sid, "Q1", tutil.CORRECT_C, "c")     # Q2 故意不提交
    notes_json = {
        "Q1": {"error_location": "无明显错误", "error_reason": "—",
               "suggestion": "保留你的思路，只把 scanf 的返回值判断写成注释说明",
               "step_by_step": "1) 读入 n；2) 计算 $2n$；3) 输出并换行。",
               "better_approach": "在你现有循环思路上少一次判断即可", "better_approach_kps": [k1],
               "manual_correct": True},
        "overall": {"知识理解": "理解正确", "代码编写": "规范", "逻辑思维": "清晰",
                    "知识点掌握": f"{k1} 升到待检验", "薄弱环节": "边界处理", "训练建议": "多练组合题"},
    }
    grading = GR.grade_all(root, sid, records, notes_json, run=False)
    public = S.read_public(root, sid)
    subs = {"Q1": tutil.CORRECT_C}
    meta = {"date": "2026-11-01", "key_points": LATEX, "next_steps": "下一步练 2 道组合题",
            "pitfalls": "- scanf 少写 & 是本次最常见的错误", "tags": ["练习记录", "C语言"],
            "progress_analysis": "已学 12 个；已掌握 0 个（冷启动，仅初始估计）"}
    tutil.rm(notes)
    return {"grading": grading, "questions": public, "submissions": subs, "records": records,
            "analysis": analysis, "meta": meta, "root": root, "sid": sid}


class TestTemplateEngine(unittest.TestCase):
    def test_var_each_if_index(self):
        tpl = ("T={{a.b}}\n{{#each items}}- {{index}}:{{name}} {{#if hot}}HOT{{else}}cold{{/if}}\n{{/each}}"
               "{{#if empty}}X{{else}}Y{{/if}}")
        out = R.render(tpl, {"a": {"b": 1}, "items": [{"name": "x", "hot": True}, {"name": "y"}], "empty": []})
        self.assertIn("T=1", out)
        self.assertIn("- 1:x HOT", out)
        self.assertIn("- 2:y cold", out)
        self.assertTrue(out.endswith("Y"))

    def test_missing_var_renders_empty_not_crash(self):
        self.assertEqual(R.render("[{{nope}}]", {}), "[]")

    def test_this_for_scalars(self):
        self.assertEqual(R.render("{{#each xs}}<{{this}}>{{/each}}", {"xs": ["a", "b"]}), "<a><b>")


class TestFrontmatter(unittest.TestCase):
    def test_roundtrip_and_required_keys(self):
        fm = R.make_frontmatter({"date": "2026-11-01", "languages": ["c"], "score": 83,
                                 "tags": ["练习记录"], "knowledge_points": ["变量", "printf输出"]})
        text = f"---\n{fm}\n---\n\n# t\n"
        self.assertEqual(R.validate_frontmatter(text), [])
        parsed = R.parse_frontmatter(text)
        self.assertEqual(parsed["date"], "2026-11-01")
        self.assertEqual(parsed["languages"], ["c"])
        self.assertEqual(parsed["score"], 83)
        self.assertEqual(parsed["knowledge_points"], ["变量", "printf输出"])

    def test_values_with_special_chars_are_quoted(self):
        fm = R.make_frontmatter({"date": "2026-11-01", "languages": ["c"], "score": "",
                                 "tags": ['a: b', "he said \"hi\"", "#hash"], "knowledge_points": []})
        text = f"---\n{fm}\n---\n"
        self.assertEqual(R.validate_frontmatter(text), [])
        self.assertEqual(R.parse_frontmatter(text)["tags"], ['a: b', 'he said "hi"', "#hash"])

    def test_missing_keys_and_bad_date_detected(self):
        errs = R.validate_frontmatter("---\ndate: 11-01\ntags: [a]\n---\n")
        self.assertTrue(any("languages" in e for e in errs))
        self.assertTrue(any("date" in e for e in errs))
        self.assertTrue(R.validate_frontmatter("# 没有 frontmatter\n"))

    def test_score_can_be_empty_when_nothing_graded(self):
        fm = R.make_frontmatter({"date": "2026-11-01", "languages": ["c"], "score": "",
                                 "tags": ["x"], "knowledge_points": ["变量"]})
        self.assertEqual(R.parse_frontmatter(f"---\n{fm}\n---\n")["score"], "")


class TestPracticeDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tutil.tmp("plc-render-")
        cls.data = full_pipeline(cls.root)
        cls.doc = R.render_practice(**{k: cls.data[k] for k in
                                       ("grading", "questions", "submissions", "records", "analysis", "meta")})

    @classmethod
    def tearDownClass(cls):
        tutil.rm(cls.root)

    def test_frontmatter_valid_and_complete(self):
        self.assertEqual(R.validate_frontmatter(self.doc), [])
        fm = R.parse_frontmatter(self.doc)
        for k in R.REQUIRED_FRONTMATTER:
            self.assertIn(k, fm)
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}$", fm["date"]), "年份必须写进 date")
        self.assertEqual(fm["languages"], ["c"])
        self.assertIsInstance(fm["knowledge_points"], list)
        self.assertTrue(fm["knowledge_points"])
        self.assertEqual(fm["unfinished"], 1)

    def test_all_original_questions_present(self):
        for q in self.data["questions"]:
            self.assertIn(q["id"], self.doc)
            self.assertIn(q["title"], self.doc)
            self.assertIn(q["description"].strip(), self.doc)
            self.assertIn(q["input_format"], self.doc)
            self.assertIn(q["constraints"][0], self.doc)
            self.assertIn(q["question_type"] and "题型", self.doc)
            self.assertIn(q["examples"][0]["input"].strip(), self.doc)
            self.assertIn(q["examples"][0]["output"].strip(), self.doc)

    def test_my_answer_verbatim(self):
        body = tutil.CORRECT_C.rstrip("\n")
        self.assertIn(body, self.doc, "我的答案必须逐字保留")
        self.assertIn("```c", self.doc, "代码块必须标注语言")
        indented = [ln for ln in body.splitlines() if ln.startswith("    ")]
        self.assertTrue(indented, "缩进必须保留")
        for ln in indented:
            self.assertIn(ln, self.doc)

    def test_unsubmitted_question_marked(self):
        self.assertIn("未完成", self.doc)
        self.assertIn("Q2", self.doc)
        self.assertNotIn("Q2 的假答案", self.doc)

    def test_wikilinks_and_latex(self):
        for kp in R.parse_frontmatter(self.doc)["knowledge_points"]:
            self.assertIn(f"[[{kp}]]", self.doc)
        self.assertIn(LATEX, self.doc, "LaTeX 必须原样保留")

    def test_required_sections_present(self):
        """结构必须与范例《10月18日练习》一致。"""
        for head in ["### 原题", "### 我的答案", "### 标准答案", "### 注意事项及易错点",
                     "**相关笔记：**", "**配套笔记：**", "**本次情况：**", "**练习前进度：**",
                     "# 总结", "## 一、知识点速查表", "## 二、目前最需要改进的",
                     "## 三、做得好的地方", "## 四、未掌握知识点与掌握度变化", "## 五、下一步建议"]:
            self.assertIn(head, self.doc)
        for sec in ["**题型：**", "**难度：**", "**知识点：**", "**判定：**", "**执行情况：**"]:
            self.assertIn(sec, self.doc)
        self.assertIn("第 1 题｜", self.doc)
        self.assertIn("| 知识点 | 要点 | 相关题号 |", self.doc)

    def test_incomplete_document_is_rejected(self):
        with self.assertRaises(R.IncompleteDocument):
            R.assert_complete(self.doc, self.data["questions"],
                              {**self.data["submissions"], "Q2": "int main(void){ return 0; }"})
        trimmed = [dict(q) for q in self.data["questions"]]
        extra = "这段补充说明根本没有出现在文档里" * 3
        trimmed[0]["stem"] = trimmed[0]["stem"] + extra
        trimmed[0]["description"] = trimmed[0]["stem"]
        with self.assertRaises(R.IncompleteDocument):
            R.assert_complete(self.doc, trimmed, self.data["submissions"])

    def test_bad_frontmatter_blocks_render(self):
        broken = "# 没有 frontmatter\n{{title}}\n"
        with self.assertRaises(R.IncompleteDocument):
            R.render_practice(template=broken, **{k: self.data[k] for k in
                                                  ("grading", "questions", "submissions", "records", "analysis", "meta")})

    def test_cli_writes_file(self):
        out = self.root / "out" / "11月1日练习.md"
        g = self.root / "grading.json"
        q = self.root / "questions.json"
        rec = self.root / "records.json"
        ana = self.root / "analysis.json"
        meta = self.root / "meta.json"
        g.write_text(json.dumps(self.data["grading"], ensure_ascii=False), encoding="utf-8")
        q.write_text(json.dumps({"questions": self.data["questions"]}, ensure_ascii=False), encoding="utf-8")
        rec.write_text(json.dumps(self.data["records"], ensure_ascii=False), encoding="utf-8")
        ana.write_text(json.dumps(self.data["analysis"], ensure_ascii=False), encoding="utf-8")
        meta.write_text(json.dumps(self.data["meta"], ensure_ascii=False), encoding="utf-8")
        rc = R.main(["practice", "--grading", str(g), "--questions", str(q), "--records", str(rec),
                     "--analysis", str(ana), "--meta", str(meta), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        self.assertEqual(R.validate_frontmatter(out.read_text(encoding="utf-8")), [])


class TestReadingOpensourceDocument(unittest.TestCase):
    def _recs(self, n=10, offline=False):
        out = []
        for i in range(1, n + 1):
            r = {"name": f"项目{i}", "language": "c", "difficulty": min(5, (i + 1) // 2),
                 "reason": f"理由{i}", "knowledge_points": ["for循环"], "focus": f"重点{i}",
                 "method": f"方法{i}", "ideas": f"思想{i}", "tasks": [f"任务{i}a", f"任务{i}b"]}
            if not offline:
                r.update({"url": f"https://github.com/o/p{i}", "repo": f"o/p{i}",
                          "path": f"src/f{i}.c", "license": "MIT", "source": "GitHub",
                          "verification": {"status": "已验证", "reason": "仓库与文件路径均存在",
                                           "checked_at": "2026-11-01 10:00:00"}})
            else:
                r["source"] = "（离线，未验证来源）"
                r["verification"] = {"status": "待验证", "reason": "无法联网验证", "checked_at": "x"}
            out.append(r)
        return out

    def test_render_ten_items(self):
        doc = R.render_reading_opensource(self._recs(), {"date": "2026-11-01", "weak_points": ["for循环"],
                                                         "how_to_read": "先猜再读", "network_note": "已联网验证"})
        self.assertEqual(R.validate_frontmatter(doc), [])
        for i in range(1, 11):
            self.assertIn(f"项目{i}", doc)
        self.assertIn("先猜再读", doc)
        self.assertIn("[[for循环]]", doc)

    def test_frontmatter_counts_verified_items(self):
        doc = R.render_reading_opensource(self._recs(), {"date": "2026-11-01"})
        fm = R.parse_frontmatter(doc)
        self.assertEqual(fm["verified"], 10, "已验证数量必须来自 verification.status")
        self.assertEqual(fm["unverified"], 0)
        doc2 = R.render_reading_opensource(self._recs(offline=True), {"date": "2026-11-01"})
        fm2 = R.parse_frontmatter(doc2)
        self.assertEqual(fm2["verified"], 0)
        self.assertEqual(fm2["unverified"], 10)

    def test_offline_items_have_no_links_or_paths(self):
        doc = R.render_reading_opensource(self._recs(offline=True), {"date": "2026-11-01",
                                                                    "network_note": "离线"})
        self.assertNotIn("http", doc, "离线时不得给出任何链接")
        self.assertNotIn("src/f1.c", doc)
        self.assertIn("待验证", doc)
        self.assertIn("不编造", doc)

    def test_missing_item_rejected(self):
        recs = self._recs()
        tpl = R.load_template("code_reading_opensource.md")
        with self.assertRaises(R.IncompleteDocument):
            R.render_reading_opensource(recs, {"date": "2026-11-01"},
                                        template=tpl.replace("{{name}}", "{{language}}"))


def reading_item(i: int, difficulty: int | None = None, **over) -> dict:
    it = {
        "title": f"主题{i}", "language": "c",
        "difficulty": difficulty if difficulty is not None else min(5, (i + 1) // 2),
        "why": f"你在第 {i} 题反复出错，这篇专门对着这个薄弱点。",
        "code": "#include <stdio.h>\n\nint main(void)\n{\n    printf(\"ok%d\\n\", " + str(i) + ");\n    return 0;\n}\n",
        "knowledge_points": ["for循环"], "related_exercises": f"第 {i} 题",
        "input_spec": "无输入", "stdin": "",
        "predict_questions": [f"预测第 {i} 篇第 1 问", f"预测第 {i} 篇第 2 问"],
        "watch_points": [f"盯住要点 {i}a", f"盯住要点 {i}b"],
        "related_notes": [{"target": "C语言", "note": "对应语法"}],
        "preview": False,
    }
    it.update(over)
    return it


class TestReadingSelfAuthoredDocument(unittest.TestCase):
    """精读模式 A（自编程序）：结构必须对齐范例《C语言精读10篇》。"""

    def _items(self, n=10):
        items = [reading_item(i) for i in range(1, n + 1)]
        for it in items:
            it["actual_output"] = "ok"
            it["compile_ok"] = True
            it["verify_line"] = "已用 gcc 编译并运行：无警告"
        return items

    def test_structure_matches_example(self):
        records = {"records": {"指针基础": {"state": "未学习", "state_index": 0, "estimated": False},
                               "结构体": {"state": "未学习", "state_index": 0, "estimated": False}}}
        doc = R.render_reading(self._items(), {"date": "2026-11-01", "title": "C语言精读10篇",
                                               "selection_basis": "根据你的学习情况挑选。",
                                               "companion_note": "对应练习见 [[11月1日练习]]。",
                                               "how_to_read": "① 先预测 ② 再运行对照 ③ 不一致处就是要补的"},
                               records)
        self.assertEqual(R.validate_frontmatter(doc), [])
        for head in ["**为什么读：**", "**输入：**", "**先预测（别急着运行）：**", "**实际输出：**",
                     "**验证状态：**", "**读代码时盯住这几点：**", "**相关笔记：**",
                     "## 暂时先不要读的", "| # | 主题 | 对应你的练习 |"]:
            self.assertIn(head, doc)
        for i in range(1, 11):
            self.assertIn(f"## 第 {i} 篇｜主题{i}", doc)
        self.assertIn("[[指针基础]]", doc, "『暂时先不要读的』要按未学知识点自动生成")
        self.assertIn("K&R", doc)

    def test_not_yet_blocks_on_note_only_advanced_kps(self):
        """笔记里提到过、但没练过的进阶知识点，同样不能推大型开源项目（出现≠掌握）。"""
        records = {"records": {"指针基础": {"state": "待检验", "state_index": 2, "estimated": True},
                               "结构体": {"state": "学习中", "state_index": 1, "estimated": True}}}
        txt = R.not_yet_md(records)
        self.assertIn("[[指针基础]]", txt)
        self.assertIn("还没有经过练习检验", txt)
        self.assertNotIn("可以开始读小型单文件", txt)

    def test_not_yet_allows_opensource_after_practice(self):
        records = {"records": {"指针基础": {"state": "基本掌握", "state_index": 3, "estimated": False},
                               "结构体": {"state": "基本掌握", "state_index": 3, "estimated": False}}}
        txt = R.not_yet_md(records)
        self.assertIn("可以开始读小型单文件", txt)

    def test_sandbox_note_appears_once_in_header(self):
        records = {"records": {}}
        doc = R.render_reading(self._items(), {"date": "2026-11-01"}, records)
        self.assertIn("## 暂时先不要读的", doc)

    def test_preview_items_marked_and_last(self):
        items = self._items()
        items[-1]["preview"] = True
        items[-1]["preview_uses"] = "用了还没学的 while 循环"
        doc = R.render_reading(items, {"date": "2026-11-01"}, {"records": {}})
        self.assertIn("【预习】主题10", doc)
        self.assertEqual(R.parse_frontmatter(doc)["preview_items"], 1)

    def test_missing_actual_output_rejected(self):
        items = self._items()
        items[3]["actual_output"] = None
        items[3]["compile_ok"] = False
        with self.assertRaises(R.IncompleteDocument) as cm:
            R.render_reading(items, {"date": "2026-11-01"}, {"records": {}})
        self.assertIn("缺少实际输出", str(cm.exception))

    def test_preview_item_may_lack_output(self):
        items = self._items()
        items[-1]["preview"] = True
        items[-1]["actual_output"] = None
        items[-1]["compile_ok"] = False
        items[-1]["verify_line"] = "未验证：本机没有工具链"
        doc = R.render_reading(items, {"date": "2026-11-01"}, {"records": {}})
        self.assertIn("未验证", doc)

    def test_code_and_title_completeness(self):
        items = self._items(3)
        tpl = R.load_template("code_reading.md").replace("{{code_block}}", "")
        with self.assertRaises(R.IncompleteDocument):
            R.render_reading(items, {"date": "2026-11-01"}, {"records": {}}, template=tpl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
