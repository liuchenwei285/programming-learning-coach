#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行全部验收测试，并输出「验收标准 -> 测试文件 -> 结果」对照表。

用法：
  python tests/run_tests.py            # 全量
  python tests/run_tests.py -k 06      # 只跑文件名含 06 的
  python tests/run_tests.py --show-output   # 同时显示脚本的 stdout（默认吞掉，保持报告干净）
  python tests/run_tests.py --offline  # 跳过需要联网的用例
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

CRITERIA = [
    ("1", "笔记解析（md/txt/docx，其它格式降级）", "test_01_parse_notes.py"),
    ("2", "知识点提取与「出现≠掌握」", "test_01_parse_notes.py"),
    ("3", "出题流程与不超纲检查", "test_03_questions.py"),
    ("4", "答案保密：出题阶段输出/上下文不含标准答案", "test_03_questions.py"),
    ("3b", "题型系统、组卷配比、提示三级、一次性提交", "test_03_questions.py"),
    ("5", "批改流程（未提交题、代码沙箱、文本逐行对照、人工判定）", "test_05_grading.py"),
    ("6", "沙箱：超时、无网络、输出截断", "test_06_sandbox.py"),
    ("7", "Markdown 生成与 frontmatter 合法", "test_07_markdown.py"),
    ("8", "Vault 写入、文件重名处理", "test_08_vault.py"),
    ("9", "历史记录保护（不覆盖、不删除）", "test_08_vault.py"),
    ("10", "精读模式B：开源片段来源验证（联网真实验证 / 离线降级）", "test_10_verify.py"),
    ("10b", "精读模式A：自编程序逐篇编译运行，实际输出不得编造", "test_12_reading.py"),
    ("附", "示例文件自检（fixtures 里的示例必须始终合法可用）", "test_11_examples.py"),
    ("v2-1", "固定 10 题：不足/超出都报错", "test_13_v2_rules.py"),
    ("v2-2", "难度从低到高（并可切 desc）", "test_13_v2_rules.py"),
    ("v2-3", "范围一次确认：无歧义不问 / 已确认沿用 / 只问新增", "test_13_v2_rules.py"),
    ("v2-4", "笔记哈希缓存命中与失效", "test_13_v2_rules.py"),
    ("v2-5", "出题阶段输出不含标准答案", "test_13_v2_rules.py"),
    ("v2-6", "近似查重 + 薄弱点比例 + 知识点预算预警", "test_13_v2_rules.py"),
]


def count_by_module(suite) -> dict:
    out: dict[str, int] = {}

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                mod = type(t).__module__ or "?"
                out[mod] = out.get(mod, 0) + 1
    walk(suite)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    show_output = "--show-output" in argv
    argv = [a for a in argv if a != "--show-output"]
    if "--offline" in argv:
        argv.remove("--offline")
        os.environ["PLC_FORCE_OFFLINE"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    pattern = "test_*.py"
    loader = unittest.TestLoader()
    if any(a.startswith("-k") for a in argv):
        k = [a for a in argv if a.startswith("-k")][0]
        k = k[2:] if len(k) > 2 else argv[argv.index(k) + 1]
        suite = unittest.TestSuite()
        for f in sorted(TESTS_DIR.glob(f"test_*{k}*.py")):
            suite.addTests(loader.loadTestsFromName(f.stem))
    else:
        suite = loader.discover(str(TESTS_DIR), pattern=pattern, top_level_dir=str(TESTS_DIR))
    totals = count_by_module(suite)
    buf = io.StringIO()
    stream = sys.stderr
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    with contextlib.redirect_stdout(buf):
        result = runner.run(suite)
    stats: dict[str, dict] = {m: {"total": n, "failed": 0, "error": 0, "skipped": 0}
                              for m, n in totals.items()}

    def mod_of(test):
        return type(test).__module__

    for t, _ in result.failures:
        stats[mod_of(t)]["failed"] += 1
    for t, _ in result.errors:
        stats[mod_of(t)]["error"] += 1
    for t, _ in result.skipped:
        stats[mod_of(t)]["skipped"] += 1

    print("\n" + "=" * 78)
    print("验收标准对照表")
    print("=" * 78)
    print(f'{"#":<4}{"验收标准":<46}{"测试文件":<26}结果')
    for num, desc, fname in CRITERIA:
        mod = fname[:-3]
        st = stats.get(mod)
        if not st:
            verdict = "未找到测试"
        elif st["failed"] or st["error"]:
            verdict = f'❌ 失败 {st["failed"] + st["error"]}/{st["total"]}'
        else:
            ok = st["total"] - st["skipped"]
            verdict = f'✅ 通过 {ok}/{st["total"]}' + (f'（跳过 {st["skipped"]}）' if st["skipped"] else "")
        print(f'{num:<4}{desc:<46}{fname:<26}{verdict}')
    print("-" * 78)
    total = sum(s["total"] for s in stats.values())
    passed = total - len(result.failures) - len(result.errors) - len(result.skipped)
    print(f'合计：{total} 个用例，通过 {passed}，失败 {len(result.failures)}，'
          f'错误 {len(result.errors)}，跳过 {len(result.skipped)}')
    for t, why in result.skipped:
        print(f'  跳过：{t.id().split(".")[-1]} —— {why}')
    if show_output and buf.getvalue().strip():
        print("-" * 78)
        print("脚本 stdout（--show-output）：")
        print(buf.getvalue())
    print("=" * 78)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
