#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 8 + 9：Vault 写入、重名处理、历史记录保护（不覆盖、不删除）。"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import tutil
import mastery as M
import session_store as S
import vault_write as V

BODY = "---\ndate: 2026-11-01\nlanguages: [c]\nscore: 100\ntags: [练习记录]\nknowledge_points: [变量]\n---\n\n# 11月1日练习\n\n正文内容。\n"


def make_vault(with_obsidian: bool = True) -> Path:
    v = tutil.tmp("plc-vault-")
    if with_obsidian:
        (v / ".obsidian").mkdir(parents=True)
        (v / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    return v


def all_files(root: Path) -> set:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


class TestVaultResolution(unittest.TestCase):
    def setUp(self):
        self.v = make_vault()

    def tearDown(self):
        tutil.rm(self.v)

    def test_vault_path_comes_from_config(self):
        """Vault 路径问一次、记住：不传参数时直接用 config.json 里记住的路径。"""
        import config as CFG
        from unittest import mock
        with mock.patch.object(CFG, "load", return_value={"vault": str(self.v)}):
            v = V.resolve_vault(None)
        self.assertEqual(str(v), str(self.v.resolve()))
        self.assertTrue(v.is_dir())

    def test_missing_configured_vault_raises(self):
        import config as CFG
        from unittest import mock
        with mock.patch.object(CFG, "load", return_value={"vault": r"D:\这个路径一定不存在_plc_test"}):
            with self.assertRaises(V.VaultError) as cm:
                V.resolve_vault(None)
            self.assertIn("路径不存在", str(cm.exception))

    def test_nonexistent_path_rejected(self):
        with self.assertRaises(V.VaultError) as cm:
            V.resolve_vault(str(self.v / "不存在"))
        self.assertIn("路径不存在", str(cm.exception))

    def test_non_vault_dir_rejected(self):
        plain = tutil.tmp("plc-plain-")
        try:
            with self.assertRaises(V.VaultError) as cm:
                V.resolve_vault(str(plain))
            self.assertIn(".obsidian", str(cm.exception))
            self.assertTrue(V.resolve_vault(str(plain), strict=False))
        finally:
            tutil.rm(plain)

    def test_real_vault_detected(self):
        self.assertTrue(V.looks_like_vault(self.v))
        self.assertEqual(V.resolve_vault(str(self.v)), self.v.resolve())

    def test_traversal_refused(self):
        with self.assertRaises(V.VaultError):
            V.safe_join(self.v.resolve(), "..", "evil.md")

    def test_plan_shows_policy_without_writing(self):
        before = all_files(self.v)
        plan = V.plan(self.v)
        self.assertTrue(plan["is_vault"])
        self.assertEqual(all_files(self.v), before, "plan 不得写入任何文件")
        self.assertIn("不删除任何文件", plan["write_policy"])
        self.assertTrue(plan["planned_dirs"]["practice"]["path"].endswith(
            str(Path(V.ROOT_DIR) / V.SUBDIRS["practice"])))


class TestNaming(unittest.TestCase):
    def test_filename_uses_month_day_only(self):
        self.assertEqual(V.date_to_stem("2026-11-01", "practice"), "11月1日练习")
        self.assertEqual(V.date_to_stem("2026-01-09", "reading"), "1月9日代码精读")
        self.assertEqual(V.date_to_stem("2026-12-25", "practice"), "12月25日练习")

    def test_year_not_in_filename(self):
        self.assertNotIn("2026", V.date_to_stem("2026-11-01", "practice"))

    def test_bad_date_rejected(self):
        for bad in ("11-01", "2026/11/01", "", None):
            with self.assertRaises(V.VaultError):
                V.date_to_stem(bad, "practice")

    def test_unique_path_never_overwrites(self):
        d = tutil.tmp("plc-uniq-")
        try:
            first = V.unique_path(d, "11月1日练习")
            (d / "11月1日练习.md").write_text("old", encoding="utf-8")
            second = V.unique_path(d, "11月1日练习")
            self.assertEqual(first.name, "11月1日练习.md")
            self.assertEqual(second.name, "11月1日练习(2).md")
            (d / second.name).write_text("new", encoding="utf-8")
            third = V.unique_path(d, "11月1日练习")
            self.assertEqual(third.name, "11月1日练习(3).md")
            self.assertEqual((d / "11月1日练习.md").read_text(encoding="utf-8"), "old")
        finally:
            tutil.rm(d)


class TestWriteNote(unittest.TestCase):
    def setUp(self):
        self.v = make_vault()
        self.vr = self.v.resolve()

    def tearDown(self):
        tutil.rm(self.v)

    def test_dry_run_writes_nothing(self):
        before = all_files(self.v)
        res = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(all_files(self.v), before)

    def test_creates_structure_and_file(self):
        res = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False)
        p = Path(res["path"])
        self.assertTrue(p.exists())
        self.assertEqual(p.name, "11月1日练习.md")
        self.assertEqual(p.read_text(encoding="utf-8"), BODY)
        self.assertEqual(p.parent, self.vr, "默认必须写进 Vault 根目录")
        self.assertTrue(res["at_root"])
        self.assertFalse((self.v / V.ROOT_DIR).exists(), "默认不得新建子目录")
        self.assertFalse(res["overwrite"])

    def test_in_subdirs_opt_in(self):
        res = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False, at_root=False)
        p = Path(res["path"])
        self.assertEqual(p.parent, self.vr / V.ROOT_DIR / V.SUBDIRS["practice"])
        self.assertFalse(res["at_root"])

    def test_same_day_second_note_gets_suffix(self):
        r1 = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False)
        r2 = V.write_note(self.v, "practice", "2026-11-01", BODY + "\n第二次\n", dry_run=False)
        self.assertEqual(Path(r1["path"]).name, "11月1日练习.md")
        self.assertEqual(Path(r2["path"]).name, "11月1日练习(2).md")
        self.assertEqual(Path(r1["path"]).read_text(encoding="utf-8"), BODY, "第一个文件不得被改动")
        self.assertIn("第二次", Path(r2["path"]).read_text(encoding="utf-8"))

    def test_reading_kind_uses_its_own_dir(self):
        res = V.write_note(self.v, "reading", "2026-11-01", BODY, dry_run=False, at_root=False)
        self.assertEqual(Path(res["path"]).name, "11月1日代码精读.md")
        self.assertIn(V.SUBDIRS["reading"], res["dir"])
        res2 = V.write_note(self.v, "reading", "2026-11-02", BODY, dry_run=False)
        self.assertEqual(Path(res2["path"]).name, "11月2日代码精读.md")
        self.assertEqual(Path(res2["path"]).parent, self.vr)

    def test_reuses_existing_dir(self):
        existing = self.v / "学习" / V.SUBDIRS["practice"]
        existing.mkdir(parents=True)
        res = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False, at_root=False)
        self.assertTrue(res["reused_existing_dir"])
        self.assertEqual(Path(res["path"]).parent, existing.resolve())
        self.assertEqual(Path(res["path"]).name, "11月1日练习.md")
        self.assertFalse((self.v / V.ROOT_DIR).exists(), "已有目录时不得新建 Programming Learning Coach")

    def test_at_root_writes_into_vault_root(self):
        res = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False, at_root=True)
        p = Path(res["path"])
        self.assertEqual(p.parent, self.v.resolve())
        self.assertEqual(p.name, "11月1日练习.md")
        self.assertFalse((self.v / V.ROOT_DIR).exists(), "at-root 模式不得新建子目录")
        res2 = V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False, at_root=True)
        self.assertEqual(Path(res2["path"]).name, "11月1日练习(2).md")
        self.assertEqual(p.read_text(encoding="utf-8"), BODY, "重名仍然不得覆盖")

    def test_at_root_archive(self):
        r1 = V.update_archive(self.v, "学习进度.md", "第一次", dry_run=False, at_root=True)
        self.assertEqual(Path(r1["path"]).parent, self.v.resolve())
        r2 = V.update_archive(self.v, "学习进度.md", "第二次", dry_run=False, at_root=True)
        text = Path(r2["path"]).read_text(encoding="utf-8")
        self.assertIn("第一次", text)
        self.assertIn("第二次", text)
        self.assertTrue(r2["backup"])

    def test_bad_kind_rejected(self):
        with self.assertRaises(V.VaultError):
            V.write_note(self.v, "secret", "2026-11-01", BODY, dry_run=False)

    def test_cli_roundtrip(self):
        content = tutil.write(self.v.parent / "doc.md", BODY)
        rc = V.main(["note", "--vault", str(self.v), "--kind", "practice",
                     "--date", "2026-11-01", "--content", str(content), "--yes"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.v / "11月1日练习.md").exists(), "CLI 默认写根目录")
        rc = V.main(["note", "--vault", str(self.v), "--kind", "practice", "--date", "2026-11-01",
                     "--content", str(content), "--yes", "--in-subdirs"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.v / V.ROOT_DIR / V.SUBDIRS["practice"] / "11月1日练习.md").exists())
        rc = V.main(["note", "--vault", str(self.v), "--kind", "practice", "--date", "2026-11-01",
                     "--content", str(content), "--yes"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.v / "11月1日练习(2).md").exists(), "根目录重名同样不得覆盖")


class TestArchiveHistoryProtection(unittest.TestCase):
    def setUp(self):
        self.v = make_vault()

    def tearDown(self):
        tutil.rm(self.v)

    def test_first_update_creates_file(self):
        res = V.update_archive(self.v, "学习进度.md", "第一次内容", dry_run=False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["existed"])
        self.assertIsNone(res["backup"])
        self.assertIn("第一次内容", Path(res["path"]).read_text(encoding="utf-8"))

    def test_update_keeps_old_content_and_makes_backup(self):
        V.update_archive(self.v, "学习进度.md", "第一次内容", dry_run=False)
        path = self.v / "学习进度.md"
        before_hash = path.read_text(encoding="utf-8")
        res = V.update_archive(self.v, "学习进度.md", "第二次内容", dry_run=False)
        after = path.read_text(encoding="utf-8")
        self.assertIn("第二次内容", after)
        self.assertIn(before_hash.strip(), after, "旧内容必须整段保留")
        self.assertTrue(res["old_bytes_preserved"])
        self.assertTrue(res["backup"])
        bak = Path(res["backup"])
        self.assertTrue(bak.exists())
        self.assertEqual(bak.read_text(encoding="utf-8"), before_hash)
        self.assertIn(V.BACKUP_DIR, str(bak))
        self.assertFalse(res["deleted_anything"])

    def test_three_updates_preserve_every_version(self):
        for i in (1, 2, 3):
            V.update_archive(self.v, "知识点掌握记录.md", f"版本{i}：掌握度快照", dry_run=False)
        path = self.v / "知识点掌握记录.md"
        text = path.read_text(encoding="utf-8")
        for i in (1, 2, 3):
            self.assertIn(f"版本{i}：掌握度快照", text)
        backups = list((self.v / V.BACKUP_DIR).glob("*.md"))
        self.assertEqual(len(backups), 2, "第 2、3 次更新各留一份备份")

    def test_archive_name_restricted(self):
        with self.assertRaises(V.VaultError):
            V.update_archive(self.v, "别的文件.md", "x", dry_run=False)

    def test_dry_run_archive_writes_nothing(self):
        before = all_files(self.v)
        res = V.update_archive(self.v, "学习进度.md", "内容", dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(all_files(self.v), before)

    def test_mastery_history_accumulates(self):
        data = {"version": 1, "cold_start": True, "records": {}}
        M.apply_events(data, [{"kp": "for循环", "session_id": "s1", "question_id": "Q1", "correct": True}])
        p = self.v / "records.json"
        M.save(p, data)
        h1 = len(M.load(p)["records"]["for循环"]["history"])
        M.apply_events(data, [{"kp": "for循环", "session_id": "s2", "question_id": "Q2", "correct": False}])
        M.save(p, data)
        rec = M.load(p)["records"]["for循环"]
        self.assertEqual(len(rec["history"]), h1 + 1)
        self.assertEqual(rec["history"][0]["action"], "promote")
        self.assertEqual(rec["history"][-1]["action"], "demote")
        self.assertEqual(rec["state"], "学习中")
        self.assertIn("Q1", rec["correct_questions"])

    def test_session_never_recreated(self):
        root = tutil.tmp("plc-sess-")
        try:
            S.create(root, "s1", "c")
            with self.assertRaises(S.SessionError) as cm:
                S.create(root, "s1", "c")
            self.assertIn("不会覆盖", str(cm.exception))
        finally:
            tutil.rm(root)

    def test_nothing_deleted_during_full_write_cycle(self):
        V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False)
        snapshot = all_files(self.v)
        V.write_note(self.v, "practice", "2026-11-01", BODY, dry_run=False)
        V.write_note(self.v, "reading", "2026-11-01", BODY, dry_run=False)
        V.update_archive(self.v, "学习进度.md", "x", dry_run=False)
        V.update_archive(self.v, "学习进度.md", "y", dry_run=False)
        after = all_files(self.v)
        self.assertTrue(snapshot <= after, "任何一次写入都不得删除已有文件")
        self.assertGreater(len(after), len(snapshot))


if __name__ == "__main__":
    unittest.main(verbosity=2)
