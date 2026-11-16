#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 6：沙箱 —— 超时、无网络（诚实声明）、输出截断、不继承环境、临时目录清理、降级。"""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

import tutil
import sandbox_run as SBX

NOMATCH = [{"name": "t", "stdin": "", "expected_stdout": "__故意不匹配__"}]


class TestSandbox(unittest.TestCase):
    def run_py(self, code, tests=None, **kw):
        return SBX.run_tests(code, "python", NOMATCH if tests is None else tests, **kw)

    # --- 不继承环境变量 -------------------------------------------------------
    def test_env_not_inherited(self):
        os.environ["PLC_SECRET_MARKER"] = "leak-me"
        os.environ["PLC_OTHER"] = "x"
        try:
            r = self.run_py(tutil.ENVSPY_PY, timeout=20)
        finally:
            os.environ.pop("PLC_SECRET_MARKER", None)
            os.environ.pop("PLC_OTHER", None)
        self.assertTrue(r["ran"], r.get("reason"))
        data = json.loads(r["cases"][0]["actual_stdout"])
        self.assertIsNone(data["secret"], "父进程环境变量不得进入沙箱")
        self.assertNotIn("PLC_OTHER", data["keys"])
        self.assertEqual(data["proxy"], SBX.BLACKHOLE, "必须注入黑洞代理")
        self.assertFalse(r["isolation"]["env_inherited"])
        self.assertLess(len(data["keys"]), 25, f"环境应只含白名单：{data['keys']}")

    # --- 超时 -----------------------------------------------------------------
    def test_timeout_kills_process(self):
        t0 = time.monotonic()
        r = self.run_py(tutil.SPIN_PY, timeout=1)
        elapsed = time.monotonic() - t0
        c = r["cases"][0]
        self.assertTrue(c["timed_out"])
        self.assertFalse(c["passed"])
        self.assertTrue(c["process_terminated"], "超时后必须确认进程已终止，否则要如实标注")
        self.assertLess(c["duration_ms"], 5000)
        self.assertLess(elapsed, 12)
        self.assertTrue(r["isolation"]["process_terminated_all"])

    # --- 输出截断 -------------------------------------------------------------
    def test_output_truncated(self):
        r = self.run_py(tutil.BIG_PY, timeout=25, max_output=1000)
        c = r["cases"][0]
        self.assertLessEqual(len(c["actual_stdout"]), 1000)
        self.assertTrue(c["output_truncated"])
        self.assertTrue(c["process_terminated"])
        self.assertEqual(r["isolation"]["max_output_bytes"], 1000)

    # --- 临时目录与清理 --------------------------------------------------------
    def test_tmpdir_created_and_cleaned(self):
        work = tutil.tmp("plc-workroot-")
        try:
            r = self.run_py("print('hi')\n", workroot=str(work))
            p = r["isolation"]["tmpdir_path"]
            self.assertTrue(p.startswith(str(work)), "沙箱目录必须在指定 workroot 下")
            self.assertTrue(r["isolation"]["cleaned"])
            self.assertFalse(Path(p).exists(), "用后必须清理")
            self.assertEqual([x.name for x in work.iterdir()], [])
        finally:
            tutil.rm(work)

    # --- 工具链缺失 -> 静态审查 -----------------------------------------------
    def test_missing_toolchain_degrades_honestly(self):
        r = SBX.run_tests("whatever", "cobol", [{"expected_stdout": ""}])
        self.assertFalse(r["ran"])
        self.assertEqual(r["mode"], "static_review")
        self.assertEqual(r["statement"], "本次未运行，仅静态审查")
        self.assertIn("工具链", r["reason"])
        self.assertTrue(r["warnings"])
        self.assertTrue(r["static_review"]["performed"])

    def test_no_tests_is_static_only(self):
        r = SBX.run_tests("print(1)\n", "python", [])
        self.assertFalse(r["ran"])
        self.assertEqual(r["statement"], "本次未运行，仅静态审查")

    # --- 网络：诚实声明 --------------------------------------------------------
    def test_network_isolation_is_honest(self):
        r = self.run_py(tutil.NET_PY, timeout=30)
        self.assertIsNotNone(r["isolation"]["network_method"])
        if not r["ran"]:
            self.skipTest("python 沙箱不可用")
        raw = r["cases"][0]["actual_stdout"].strip()
        data = json.loads(raw.splitlines()[-1])
        if data["connected"]:
            self.assertFalse(r["isolation"]["network_blocked"],
                             "既然能连出去，就不得声称 network_blocked=true")
            self.assertTrue(any("best-effort" in w for w in r["warnings"]),
                            "无法真正断网时必须给出 warning")
        else:
            self.assertTrue(True)

    # --- C 工具链 --------------------------------------------------------------
    @unittest.skipUnless(tutil.has_toolchain("c"), "未安装 gcc")
    def test_c_correct_runs_and_passes(self):
        r = SBX.run_tests(tutil.CORRECT_C, "c",
                          [{"name": "样例", "stdin": "3\n", "expected_stdout": "6\n"}], timeout=15)
        self.assertTrue(r["ran"], r.get("reason"))
        self.assertEqual(r["mode"], "executed")
        self.assertEqual(r["passed"], 1)
        self.assertEqual(r["compile"]["exit_code"], 0)

    @unittest.skipUnless(tutil.has_toolchain("c"), "未安装 gcc")
    def test_c_compile_failure_falls_back_to_static(self):
        r = SBX.run_tests(tutil.WRONG_C, "c",
                          [{"name": "样例", "stdin": "3\n", "expected_stdout": "6\n"}], timeout=20)
        self.assertFalse(r["ran"])
        self.assertEqual(r["mode"], "static_review")
        self.assertEqual(r["statement"], "本次未运行，仅静态审查")
        self.assertIn("编译失败", r["reason"])
        self.assertNotEqual(r["compile"]["exit_code"], 0)
        self.assertTrue(r["static_review"]["issues"], "编译不过时静态审查要给出可定位的问题")
        self.assertIn("scanf_missing_amp", [i["rule"] for i in r["static_review"]["issues"]])

    # --- 静态审查与比较模式 -----------------------------------------------------
    def test_python_syntax_error_located(self):
        sr = SBX.static_review("def f(:\n    pass\n", "python")
        self.assertTrue(sr["blockers"])
        self.assertEqual(sr["issues"][0]["rule"], "syntax_error")
        self.assertEqual(sr["issues"][0]["line"], 1)

    def test_c_static_rules(self):
        sr = SBX.static_review('int main(void){ int a; if (a = 1) {} printf("%d", a); }', "c")
        rules = {i["rule"] for i in sr["issues"]}
        self.assertIn("assign_in_condition", rules)
        self.assertIn("missing_stdio", rules)

    def test_compare_modes(self):
        self.assertTrue(SBX.compare("6\n", "6\r\n", "trim"))
        # 多行输出：Windows 的 CRLF + 行尾空格 + 结尾空行都不能影响判定
        self.assertTrue(SBX.compare("百位:5 十位:8\n和:16\n", "百位:5 十位:8\r\n和:16  \r\n\r\n", "trim"))
        self.assertFalse(SBX.compare("百位:5\n和:16\n", "百位:5\n和:17\n", "trim"))
        self.assertFalse(SBX.compare("a\nb\n", "a\n", "trim"), "缺行必须判错")
        self.assertEqual(SBX._norm_out("a\r\nb  \r\n\r\n"), ["a", "b"])
        self.assertFalse(SBX.compare("6\n", "6\r\n", "exact"))
        self.assertTrue(SBX.compare("6 7", "6  7\n", "tokens"))
        self.assertTrue(SBX.compare("OK", "result: OK!", "contains"))
        self.assertTrue(SBX.compare(r"^\d+$", "42\n", "regex"))

    def test_toolchain_detection_reports_versions(self):
        tc = SBX.detect_toolchains()
        self.assertTrue(tc.get("python"))
        self.assertIn("exe", tc["python"])
        self.assertTrue(tc["python"]["version"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
