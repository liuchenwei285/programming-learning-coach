#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码精读（自编程序模式，默认）：10 篇短小程序，每篇都**真的编译并运行**，
「实际输出」来自真实执行结果，绝不凭空编写。

为什么默认用自编程序而不是开源项目：
  范例《C语言精读10篇》的做法更适合初学者 —— 每篇都能一次读完、能预测输出、能立刻编译验证，
  且严格只用学习者已学的语法。开源片段模式（verify_sources.py + render reading-opensource）
  保留给学完指针/结构体之后的进阶阶段。

每篇必填字段：
  title 主题 / why 为什么读（必须引用你的具体错误）/ code 完整可编译程序 /
  knowledge_points 知识点 / related_notes 相关笔记 / predict_questions 先预测（>=2 条）/
  watch_points 读代码时盯住这几点（>=2 条）/ related_exercises 对应你的练习 /
  input_spec 输入说明 / stdin 实际喂给程序的输入 / output_prefix 输出前缀（可选）
  preview=true 表示「预习篇」：允许使用未学知识点，但必须写 preview_uses 说明用了什么

用法：
  python reading.py build --items reading.json --records records.json --out verified.json [--no-run]
  python reading.py check --items reading.json --records records.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery as M           # noqa: E402
import sandbox_run as SBX     # noqa: E402

REQUIRED_FIELDS = ["title", "why", "code", "knowledge_points", "related_notes",
                   "predict_questions", "watch_points"]
DEFAULT_SIZE = 10
OPEN_SOURCE_KEYS = {"url", "repo", "path", "license", "author"}


class ReadingError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 校验
def validate_item(it: dict, records: dict, idx: int) -> list[str]:
    errs = []
    tag = it.get("title") or f"第{idx}篇"
    for f in REQUIRED_FIELDS:
        if not it.get(f):
            errs.append(f"{tag}：缺少必填字段 {f}")
    if len(it.get("predict_questions") or []) < 2:
        errs.append(f"{tag}：predict_questions 至少 2 条（先预测再运行是本模式的核心）")
    if len(it.get("watch_points") or []) < 2:
        errs.append(f"{tag}：watch_points 至少 2 条")
    bad = OPEN_SOURCE_KEYS & set(it)
    if bad:
        errs.append(f"{tag}：自编模式不得含开源字段 {sorted(bad)}（要推荐开源片段请用 reading-opensource）")
    if it.get("preview"):
        if not it.get("preview_uses"):
            errs.append(f"{tag}：标了 preview（预习）就必须写 preview_uses，说明用了哪些还没学的知识点")
    else:
        for kp in it.get("knowledge_points") or []:
            if M.state_of(records, kp) == "未学习":
                errs.append(f"超纲：{tag} 用到未学知识点「{kp}」；若确需提前接触，请标 preview=true 并写 preview_uses")
    return errs


def check_list(items: list[dict], records: dict, *, size: int = DEFAULT_SIZE,
               weak_points: list[str] | None = None) -> dict:
    errors, warnings = [], []
    for i, it in enumerate(items, 1):
        errors += validate_item(it, records, i)
    if len(items) != size:
        errors.append(f"必须 {size} 篇，当前 {len(items)} 篇")
    diffs = [int(it.get("difficulty") or 0) for it in items]
    if any(d and not 1 <= d <= 5 for d in diffs):
        errors.append(f"difficulty 必须是 1..5：{diffs}")
    elif any(diffs) and diffs != sorted(diffs):
        errors.append(f"难度必须递增（从易到难）：{diffs}")
    preview_idx = [i for i, it in enumerate(items) if it.get("preview")]
    if preview_idx and len(items) >= 5:
        tail = list(range(len(items) - len(preview_idx), len(items)))
        if preview_idx != tail:
            errors.append(f"预习篇必须放在最后（当前在第 {[i+1 for i in preview_idx]} 篇）")
    if not weak_points:
        weak_points = [w["kp"] for w in M.weak_points(records)]
    used = {kp for it in items for kp in (it.get("knowledge_points") or [])}
    miss = [w for w in weak_points if w not in used]
    if miss:
        warnings.append(f"薄弱点未被精读覆盖：{miss}（前几篇应专门对着你的薄弱点）")
    no_ex = [it.get("title") for it in items if not it.get("related_exercises")]
    if no_ex:
        warnings.append(f"这些篇目没有 related_exercises（应对应到具体练习题号）：{no_ex}")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "size": len(items), "preview_count": len(preview_idx),
            "weak_points": weak_points, "weak_covered": sorted(set(weak_points) & used)}


# --------------------------------------------------------------------------- 编译运行验证
def verify_item(it: dict, *, lang: str = "c", run: bool = True, timeout: float = 20.0,
                max_output: int = 20000, workroot: str | None = None) -> dict:
    out = dict(it)
    lang = it.get("language") or lang
    if not run:
        out.update({"compile_ok": False, "actual_output": None, "compile_warnings": None,
                    "verify_line": "未验证：本次未运行编译，实际输出留空（不编造输出）"})
        return out
    tests = [{"name": "run", "stdin": it.get("stdin") or "", "expected_stdout": "",
              "compare": "contains", "timeout": it.get("timeout", timeout)}]
    res = SBX.run_tests(it.get("code") or "", lang, tests, timeout=timeout,
                        max_output=max_output, workroot=workroot)
    tool = (res.get("toolchain") or {}).get("exe")
    comp = res.get("compile") or {}
    warns = (comp.get("warnings") or "").strip()
    case = (res.get("cases") or [{}])[0]
    stdout = case.get("actual_stdout")
    if res.get("ran") and case.get("exit_code") == 0 and stdout is not None:
        text = stdout.replace("\r\n", "\n").rstrip("\n")
        out["actual_output"] = (it.get("output_prefix") or "") + text
        out["compile_ok"] = True
        out["compile_warnings"] = warns or None
        ver = (res.get("toolchain") or {}).get("version") or tool or lang
        out["verify_line"] = (f"已用 `{Path(str(tool)).name}` 编译并运行（{ver}）："
                              f'{"无警告" if not warns else "有编译警告，见下"}'
                              f'，上面的「实际输出」是真实运行结果，运行耗时 {case.get("duration_ms")} ms')
        if warns:
            out["verify_line"] += "；警告内容：" + warns[:300]
    else:
        out["actual_output"] = None
        out["compile_ok"] = False
        out["compile_warnings"] = warns or None
        reason = res.get("reason") or (f'退出码 {case.get("exit_code")}' if case else "未知原因")
        if case.get("timed_out"):
            reason = f'运行超时（>{timeout}s）'
        out["verify_line"] = (f"**未通过验证**：{reason}。实际输出留空 —— 不编造运行结果；"
                              f"这一篇在修好之前不要当成范文读。")
        if warns:
            out["verify_line"] += " 编译输出：" + warns[:300]
    return out


def verify_all(items: list[dict], *, lang: str = "c", run: bool = True, timeout: float = 20.0,
               workroot: str | None = None) -> list[dict]:
    return [verify_item(it, lang=lang, run=run, timeout=timeout, workroot=workroot) for it in items]


def summarize(items: list[dict]) -> dict:
    ok = [i for i in items if i.get("compile_ok")]
    return {"total": len(items), "compiled_and_run": len(ok),
            "with_warnings": sum(1 for i in ok if i.get("compile_warnings")),
            "failed": [i.get("title") for i in items if not i.get("compile_ok")],
            "claim": (f"全部 {len(ok)} 篇都已编译运行验证"
                      + ("，没有警告" if ok and not any(i.get("compile_warnings") for i in ok) else "")
                      if len(ok) == len(items) and items else
                      f"{len(ok)}/{len(items)} 篇通过编译运行验证，其余见各篇「验证状态」")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 代码精读（自编程序模式）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check"):
        s = sub.add_parser(name)
        s.add_argument("--items", required=True); s.add_argument("--records", required=True)
        s.add_argument("--size", type=int, default=DEFAULT_SIZE)
        if name == "build":
            s.add_argument("--lang", default="c"); s.add_argument("--no-run", action="store_true")
            s.add_argument("--timeout", type=float, default=20.0)
            s.add_argument("--workroot"); s.add_argument("--out", required=True)
            s.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    data = json.loads(Path(a.items).read_text(encoding="utf-8"))
    items = data.get("items", data) if isinstance(data, dict) else data
    records = M.load(a.records)
    rep = check_list(items, records, size=a.size)
    if a.cmd == "check":
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 3
    if rep["errors"] and not a.force:
        raise ReadingError("；".join(rep["errors"]))
    verified = verify_all(items, lang=a.lang, run=not a.no_run, timeout=a.timeout,
                          workroot=getattr(a, "workroot", None))
    summary = summarize(verified)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "self_authored",
           "rules": rep, "summary": summary, "items": verified}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "out": a.out, "summary": summary,
                      "errors": rep["errors"], "warnings": rep["warnings"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReadingError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(7)
