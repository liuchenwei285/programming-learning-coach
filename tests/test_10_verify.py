#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 10：代码精读推荐 —— 来源验证逻辑（可联网则真实验证，不可联网则降级标记）。"""
from __future__ import annotations

import json
import unittest

import tutil
import render_md as R
import verify_sources as VS

NET_OK, NET_MSG = VS.probe_network(timeout=6.0)
REAL_REPO = "python/cpython"
REAL_PATH = "Python/ceval.c"
FAKE_REPO = "plc-nonexistent-org-9x7/definitely-not-a-repo-9x7"
BAD_PATH = "Python/this_file_does_not_exist_plc.c"


def mk(i: int, difficulty: int | None = None, **over) -> dict:
    item = {
        "name": f"示例项目{i}", "language": "c", "source": "GitHub",
        "difficulty": difficulty if difficulty is not None else min(5, (i + 1) // 2),
        "reason": f"理由{i}：对应本次薄弱点", "knowledge_points": ["for循环"],
        "focus": f"阅读重点{i}", "method": f"精读方法{i}", "ideas": f"编程思想{i}",
        "tasks": [f"任务{i}-1：抄写关键片段", f"任务{i}-2：用已学语法重写最小版本"],
    }
    item.update(over)
    return item


def mk10(**over) -> list[dict]:
    return [mk(i, **over) for i in range(1, 11)]


class TestOfflineDegradation(unittest.TestCase):
    def test_sanitized_fields_removed(self):
        item = mk(1, url="https://github.com/o/p", repo="o/p", path="src/a.c",
                  license="MIT", author="某人")
        out = VS.sanitize_offline(item)
        for f in VS.ONLINE_FIELDS:
            if f == "source":
                self.assertEqual(out["source"], "（离线，未验证来源）", "source 只能留占位说明")
                continue
            self.assertNotIn(f, out, f"离线时不得保留 {f}")
        self.assertNotIn("github", json.dumps(out, ensure_ascii=False).lower())
        self.assertEqual(out["verification"]["status"], "待验证")
        self.assertIn("无法联网", out["verification"]["reason"])
        self.assertEqual(out["name"], "示例项目1", "项目名保留")
        self.assertTrue(out["reason"] and out["focus"] and out["tasks"])

    def test_offline_mode_marks_everything_pending(self):
        res = VS.verify_all(mk10(url="https://github.com/o/p", repo="o/p", path="a.c"), offline=True)
        self.assertEqual(res["mode"], "offline")
        self.assertEqual(res["rules"]["pending"], 10)
        self.assertEqual(res["rules"]["verified"], 0)
        text = json.dumps(res, ensure_ascii=False)
        self.assertNotIn("github.com", text, "离线输出里不能出现任何链接")
        for r in res["recommendations"]:
            self.assertNotIn("path", r)
            self.assertIsNone(r.get("license"))

    def test_offline_document_has_no_links(self):
        res = VS.verify_all(mk10(url="https://github.com/o/p", repo="o/p", path="a.c"), offline=True)
        doc = R.render_reading_opensource(res["recommendations"],
                                          {"date": "2026-11-01", "network_note": res["probe"]})
        self.assertNotIn("http", doc)
        self.assertNotIn("a.c", doc)
        self.assertIn("待验证", doc)
        self.assertEqual(R.validate_frontmatter(doc), [])

    def test_network_probe_reports_reason(self):
        ok, msg = VS.probe_network(timeout=6.0)
        self.assertIsInstance(ok, bool)
        self.assertTrue(msg)


class TestRules(unittest.TestCase):
    def test_must_be_exactly_ten(self):
        rep = VS.check_rules(mk10()[:9])
        self.assertFalse(rep["ok"])
        self.assertTrue(any("10 份" in e for e in rep["errors"]))

    def test_difficulty_must_increase(self):
        recs = mk10()
        recs[0]["difficulty"], recs[-1]["difficulty"] = 5, 1
        rep = VS.check_rules(recs)
        self.assertTrue(any("难度必须递增" in e for e in rep["errors"]))

    def test_difficulty_must_be_int_in_range(self):
        recs = mk10()
        recs[3]["difficulty"] = "很难"
        self.assertTrue(any("difficulty" in e for e in VS.check_rules(recs)["errors"]))

    def test_required_fields(self):
        recs = mk10()
        del recs[2]["method"]
        rep = VS.check_rules(recs)
        self.assertTrue(any("method" in e for e in rep["errors"]))
        self.assertIn("第 3 份", " ".join(rep["errors"]))

    def test_verified_item_needs_a_file_path(self):
        recs = mk10()
        for r in recs:
            r["verification"] = {"status": "已验证"}
        recs[4].pop("path", None)
        rep = VS.check_rules(recs)
        self.assertTrue(any("选片段而不是整个项目" in e for e in rep["errors"]))

    def test_tasks_at_least_two(self):
        recs = mk10()
        recs[0]["tasks"] = ["只有一条"]
        self.assertTrue(any("阅读任务" in e for e in VS.check_rules(recs)["errors"]))

    def test_unverified_items_warn(self):
        recs = mk10()
        for r in recs:
            r["verification"] = {"status": "未验证"}
        rep = VS.check_rules(recs)
        self.assertEqual(rep["unverified"], 10)
        self.assertTrue(any("未通过联网验证" in w for w in rep["warnings"]))

    def test_weak_point_coverage_warning(self):
        rep = VS.check_rules(mk10(), weak_points=["for循环", "指针基础"])
        self.assertTrue(any("薄弱点未被精读覆盖" in w and "指针基础" in w for w in rep["warnings"]))
        rep2 = VS.check_rules(mk10(), weak_points=["for循环"])
        self.assertFalse(any("薄弱点未被精读覆盖" in w for w in rep2["warnings"]))

    def test_repo_parsed_from_url(self):
        self.assertEqual(VS.split_repo({"url": "https://github.com/o/p/tree/main/x.c"}), ("o", "p"))
        self.assertEqual(VS.split_repo({"repo": "o/p.git"}), ("o", "p"))
        self.assertEqual(VS.split_repo({}), (None, None))


@unittest.skipUnless(NET_OK, f"本机无法联网（{NET_MSG}），跳过真实验证用例")
class TestOnlineVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = VS.verify_all([
            mk(1, 1, repo=REAL_REPO, path=REAL_PATH, name="CPython 求值循环"),
            mk(2, 2, repo=REAL_REPO, path=BAD_PATH, name="路径不存在的例子"),
            mk(3, 3, repo=FAKE_REPO, path="a.c", name="仓库不存在的例子"),
        ], offline=False, timeout=15.0)

    def setUp(self):
        statuses = [r["verification"].get("http_status") for r in self.res["recommendations"]]
        if any(st in (403, 429) for st in statuses):
            raise unittest.SkipTest("GitHub API 已限流（403/429），联网核验用例本轮跳过；"
                                    "设置 GITHUB_TOKEN 可避免限流")

    def test_mode_online(self):
        self.assertEqual(self.res["mode"], "online")
        self.assertIn("可达", self.res["probe"])

    def test_real_repo_and_path_verified(self):
        v = self.res["recommendations"][0]["verification"]
        self.assertEqual(v["status"], "已验证", v)
        self.assertTrue(v["repo_exists"])
        self.assertTrue(v["path_exists"])
        self.assertTrue(v["license"], "许可证必须来自 API，不能靠记忆")
        self.assertEqual(self.res["recommendations"][0]["license"], v["license"])

    def test_bad_path_not_verified(self):
        v = self.res["recommendations"][1]["verification"]
        self.assertEqual(v["status"], "未验证")
        self.assertTrue(v["repo_exists"])
        self.assertFalse(v["path_exists"])

    def test_fake_repo_not_verified(self):
        v = self.res["recommendations"][2]["verification"]
        self.assertEqual(v["status"], "未验证")
        self.assertFalse(v["repo_exists"])
        self.assertIn("必须替换", v["reason"])

    def test_document_shows_verification_status(self):
        doc = R.render_reading_opensource(self.res["recommendations"], {"date": "2026-11-01"})
        self.assertIn("已验证", doc)
        self.assertIn("未验证", doc)
        self.assertIn(REAL_PATH, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
