#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3 验收：Vault 配置记住、范围自动锁定（T4 默认排除、不询问）、新弱证据一行提示、promote 生效、
免确认流程、摘要 ≤5 行、缓存命中。"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

import tutil
import analyze as A
import config as CFG
import mastery as M
import parse_notes as P
import plc_cli
import render_md as R
import session_store as S
import vault_write as V


class TestConfigNotTracked(unittest.TestCase):
    """config.json 含本机 Vault 路径，不能进版本库；缺失时要能从 example 自举。"""

    def test_config_json_is_gitignored(self):
        root = Path(CFG.CONFIG_PATH).parent
        gitignore = root / ".gitignore"
        self.assertTrue(gitignore.exists(), "仓库必须有 .gitignore")
        lines = [l.strip() for l in gitignore.read_text(encoding="utf-8").splitlines()]
        self.assertIn("config.json", lines, "config.json 必须被 .gitignore 排除")

    def test_example_config_keeps_placeholder_vault(self):
        self.assertTrue(CFG.EXAMPLE_PATH.exists(), "仓库必须保留 config.example.json")
        data = json.loads(CFG.EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["vault"], CFG.FALLBACK_VAULT,
                         "模板里的 Vault 必须是占位符，不能是真实路径")

    def test_ensure_config_creates_from_example(self):
        work = tutil.tmp("plc-cfg-")
        try:
            target = work / "config.json"
            with mock.patch.object(CFG, "CONFIG_PATH", target), \
                 mock.patch.object(CFG, "EXAMPLE_PATH", CFG.EXAMPLE_PATH):
                CFG.ensure_config()
            self.assertTrue(target.exists(), "config.json 缺失时应自动从模板生成")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["vault"],
                             CFG.FALLBACK_VAULT)
        finally:
            tutil.rm(work)


class TestVaultRemembered(unittest.TestCase):
    def test_config_has_default_vault(self):
        cfg = CFG.load()
        self.assertTrue(cfg["vault"], "Vault 路径必须永久记住")
        self.assertEqual(cfg["question_count"], 10)
        self.assertEqual(cfg["difficulty_order"], "asc")

    def test_resolve_without_arg_uses_config(self):
        cfg = CFG.load()
        self.addCleanup(CFG.save, cfg)
        work = tutil.tmp("plc-v3-vault-")
        try:
            vault = work / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            CFG.save({**cfg, "vault": str(vault)})
            v = V.resolve_vault(None)
            self.assertTrue(v.is_dir())
            self.assertEqual(v.resolve(), vault.resolve())
        finally:
            tutil.rm(work)

    def test_missing_path_raises_with_message(self):
        with self.assertRaises(V.VaultError) as cm:
            V.resolve_vault("<不存在的路径>")
        self.assertIn("路径不存在", str(cm.exception))

    def test_plan_needs_no_vault_argument(self):
        cfg = CFG.load()
        self.addCleanup(CFG.save, cfg)
        work = tutil.tmp("plc-v3-vault-")
        try:
            vault = work / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            CFG.save({**cfg, "vault": str(vault)})
            rc = V.main(["plan"])
            self.assertEqual(rc, 0, "不传 --vault 也必须能用配置里的路径")
        finally:
            tutil.rm(work)


class TestAutoLockScope(unittest.TestCase):
    def setUp(self):
        self.dir = tutil.tmp("plc-v3-scope-")

    def tearDown(self):
        tutil.rm(self.dir)

    def _note(self):
        return tutil.write(self.dir / "n.md",
                           "# C 笔记\n\n## 数据类型\n\nint / double 是基本类型，用 sizeof 查看字节数。\n\n"
                           "## 变量\n\n变量先声明后使用。\n\n"
                           "后面学到循环时还会遇到 while 和 for。\n\n"
                           "- **结构体 struct** —— 自定义复合类型\n")

    def test_t1_t3_usable_t4_excluded_no_question(self):
        rep = P.parse([str(self._note())])
        res = A.analyze(rep, {"version": 1, "cold_start": True, "records": {}})
        self.assertIn("数据类型", res["usable_kps"])
        self.assertIn("变量", res["usable_kps"])
        # T4 一律按未学排除
        for kp in ("for循环", "while循环", "结构体"):
            self.assertIn(kp, res["excluded_weak_kps"])
            self.assertNotIn(kp, res["usable_kps"])
        self.assertEqual(res["ambiguous"], [], "v3 不再产生需要确认的歧义项")
        self.assertEqual(res["needs_confirmation"], [], "永不要求确认")
        self.assertTrue(res["scope_current"])

    def test_prepare_auto_locks_without_confirmation(self):
        self._note()
        root = self.dir / "s"
        rec = self.dir / "records.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = plc_cli.main(["prepare", "--notes", str(self.dir), "--sid", "s1", "--records", str(rec),
                               "--root", str(root), "--cache-dir", str(self.dir / "cache")])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["phase"], "scope_confirmed", "prepare 必须自动推进到 scope_confirmed")
        self.assertEqual(out["next"], "范围已自动锁定，直接出 10 题（无需确认）")
        st = S.load_state(root, "s1")
        self.assertTrue(st["scope_confirmed"], "自动锁定后会话里必须有 scope_confirmed 标记")
        self.assertEqual(st["scope_confirmation"]["mode"], "auto-lock")
        self.assertTrue(out["excluded_weak_kps"])

    def test_questions_build_works_without_manual_confirm(self):
        """prepare 之后直接 build，不允许再有 scope-confirm 人工门闩。"""
        import questions as Q
        self._note()
        root = self.dir / "s2"
        rec_path = self.dir / "records2.json"
        with contextlib.redirect_stdout(io.StringIO()):
            plc_cli.main(["prepare", "--notes", str(self.dir), "--sid", "s3", "--records", str(rec_path),
                          "--root", str(root), "--cache-dir", str(self.dir / "cache")])
        rec = M.load(rec_path)
        qs = [
            tutil.make_text_question("Q1", 1, ["数据类型"], qtype="output_prediction"),
            tutil.make_text_question("Q2", 2, ["数据类型"], qtype="output_prediction"),
            tutil.make_text_question("Q3", 2, ["变量"], qtype="trace"),
            tutil.make_text_question("Q4", 2, ["数据类型"], qtype="concept"),
            tutil.make_text_question("Q5", 3, ["变量"], qtype="code_analysis"),
        ]
        fix = tutil.make_question("Q6", 3, ["变量", "数据类型"], qtype="fix_bug")
        fix["code_to_read"] = tutil.WRONG_C
        qs.append(fix)
        for i in (7, 8, 9):
            qs.append(tutil.make_question(f"Q{i}", 3 + (i - 7) // 2, ["变量", "数据类型"],
                                          qtype="programming"))
        qs.append(tutil.make_question("Q10", 5, ["变量", "数据类型"], qtype="comprehensive"))
        rep = Q.build(root, "s3", qs, rec)
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["phase"], "awaiting_submission")

    def test_new_weak_items_reported_once(self):
        self._note()
        root = self.dir / "s4"
        rec_path = self.dir / "records4.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plc_cli.main(["prepare", "--notes", str(self.dir), "--sid", "s4", "--records", str(rec_path),
                          "--root", str(root), "--cache-dir", str(self.dir / "cache")])
        first = json.loads(buf.getvalue())
        self.assertTrue(first["new_excluded"], "第一次见到的弱证据项要报一次")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            plc_cli.main(["prepare", "--notes", str(self.dir), "--sid", "s4b", "--records", str(rec_path),
                          "--root", str(root), "--cache-dir", str(self.dir / "cache")])
        second = json.loads(buf2.getvalue())
        self.assertEqual(second["new_excluded"], [], "笔记没变时不应重复提示")

    def test_scope_promote_makes_it_usable_next_round(self):
        self._note()
        root = self.dir / "s5"
        rec_path = self.dir / "records5.json"
        with contextlib.redirect_stdout(io.StringIO()):
            plc_cli.main(["prepare", "--notes", str(self.dir), "--sid", "s5", "--records", str(rec_path),
                          "--root", str(root), "--cache-dir", str(self.dir / "cache")])
        with contextlib.redirect_stdout(io.StringIO()):
            rc = plc_cli.main(["scope-promote", "--records", str(rec_path), "--kp", "for循环"])
        self.assertEqual(rc, 0)
        # 重新分析（缓存目录清掉，确保重新计算）
        rep = P.parse([str(self.dir)], cache_dir=None)
        res = A.analyze(rep, M.load(rec_path))
        self.assertIn("for循环", res["usable_kps"], "promote 后下一轮必须纳入")
        self.assertNotIn("for循环", res["excluded_weak_kps"])


class TestCompactFlow(unittest.TestCase):
    def test_summary_at_most_five_lines(self):
        res = {"counts": {"usable": 21, "excluded_weak": 17, "not_learned": 5,
                          "mastered": 1, "partial": 2, "learning": 18},
               "cold_start": False, "excluded_weak_kps": ["for循环", "while循环"]}
        text = R._progress_md(res)
        self.assertLessEqual(len(text.splitlines()), 5, "出题文档开头摘要不得超过 5 行")
        self.assertIn("21", text)

    def test_ambiguous_table_is_one_line(self):
        txt = A.ambiguous_table({"excluded_weak_kps": ["for循环", "while循环"]})
        self.assertEqual(len(txt.splitlines()), 1)
        self.assertTrue(txt.startswith("以下按未学排除："))

    def test_cache_hit_on_second_prepare(self):
        work = tutil.tmp("plc-v3-cache-")
        try:
            note = tutil.write(work / "n.md", "# 数据类型\n\nint 与 double。\n\n## 变量\n\n先声明后使用。\n")
            rec = work / "rec.json"
            outs = []
            for sid in ("a", "b"):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    plc_cli.main(["prepare", "--notes", str(note), "--sid", sid, "--records", str(rec),
                                  "--root", str(work / "s"), "--cache-dir", str(work / "cache")])
                outs.append(json.loads(buf.getvalue()))
            self.assertFalse(outs[0]["cache"]["parse_hit"])
            self.assertTrue(outs[1]["cache"]["parse_hit"], "笔记没变必须命中解析缓存")
        finally:
            tutil.rm(work)


if __name__ == "__main__":
    unittest.main(verbosity=2)
