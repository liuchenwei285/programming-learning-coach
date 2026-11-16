#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""programming-learning-coach 统一入口。

流程（严格按顺序，阶段门由 session_store 强制，跳步会被拒绝）：
  prepare(解析+分析+自动锁定范围) -> questions(固定10题) -> submit[/hint]
      -> grade-render(批改+练习文档) -> reading(自编精读,逐篇编译运行) 或 verify(开源片段)
      -> render reading -> archive

用法：
  python plc_cli.py prepare --notes <笔记路径> --sid s1 --records rec.json
  python plc_cli.py refs --stage questioning     # 本阶段只需要读哪些 references
  python plc_cli.py questions build --questions q.json --records rec.json --sid s1
  python plc_cli.py submit --sid s1 --qid Q1 --file 答案.c
  python plc_cli.py submit-dir --sid s1 --dir 我的答案/          # 一次性提交，文件名即题号
  python plc_cli.py hint --sid s1 --qid Q1 --level L1            # 提示逐级申请，不能跳级
  python plc_cli.py grade-render --sid s1 --records rec.json --notes n.json --out 练习.md
  python plc_cli.py status --sid s1 / next --sid s1              # 进度与耗时
  python plc_cli.py <subcommand> [args...]                       # 子命令参数原样传给对应脚本
  python plc_cli.py scope-promote --records rec.json --kp <知识点> # 弱证据项改为已学，下一轮生效
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analyze          # noqa: E402
import grading          # noqa: E402
import mastery as M     # noqa: E402
import parse_notes      # noqa: E402
import questions        # noqa: E402
import reading          # noqa: E402
import render_md        # noqa: E402
import session_store as S  # noqa: E402
import vault_write      # noqa: E402
import verify_sources   # noqa: E402

NEXT_ACTION = {
    "intake": "接收笔记（一条命令跑完解析+分析）：python plc_cli.py prepare --notes <路径> --sid <sid> --records work/records.json",
    "analysis": "prepare 已一次跑完解析+分析并自动锁定范围：把 T1~T3 可直接出题清单展示给学习者；若有 T4 歧义项，合并成一张表一次性问完，无需确认，直接出题；弱证据项已按未学排除",
    "scope_confirmed": "出题（固定 10 题，难度从低到高，最后 1 题综合挑战）：python plc_cli.py questions build --questions work/q.json --records work/records.json --sid <sid>；题量≠10、超纲、机械重复都会直接报错",
    "questioning": "出题写入中：完成后阶段自动进入 awaiting_submission，然后停下来等学习者提交",
    "awaiting_submission": "等待提交：submit --qid Q1 --file 答案.c（逐题）/ submit-dir --dir 我的答案/（一次性）/ hint --qid Q1 --level L1（提示逐级）",
    "grading": "批改+出练习文档（一次进程）：python plc_cli.py grade-render --sid <sid> --records work/records.json --notes work/notes.json --meta work/meta.json --out work/X月X日练习.md",
    "practice_doc": "写进 Vault：python plc_cli.py vault note --kind practice --date YYYY-MM-DD --content <doc> --yes",
    "recommend": "精读 10 篇：默认自编程序模式 python plc_cli.py reading build --items work/reading.json --records work/records.json --out work/reading-verified.json（每篇都会真的编译运行）；已学指针/结构体后改开源片段模式 python plc_cli.py verify --recs work/recs.json --out work/verified.json",
    "reading_doc": "生成精读文档：render reading --items work/reading-verified.json --meta ... --out X月X日代码精读.md（开源模式用 render reading-opensource）→ vault note --kind reading --yes",
    "archive_updated": "本次闭环完成：档案已更新（学习进度 / 知识点掌握记录，先备份再更新，历史永久保留）",
}

ALIASES = {
    "parse": ("parse_notes", parse_notes.main),
    "analyze": ("analyze", analyze.main),
    "mastery": ("mastery", M.main),
    "questions": ("questions", questions.main),
    "grade": ("grading", grading.main),
    "render": ("render_md", render_md.main),
    "vault": ("vault_write", vault_write.main),
    "verify": ("verify_sources", verify_sources.main),
    "sandbox": ("sandbox_run", __import__("sandbox_run").main),
    "session": ("session_store", S.main),
    "reading": ("reading", reading.main),
}


def cmd_new(argv) -> int:
    ap = argparse.ArgumentParser(prog="plc_cli.py new")
    ap.add_argument("--root", default=".plc")
    ap.add_argument("--sid")
    ap.add_argument("--language", default="c")
    ap.add_argument("--vault")
    a = ap.parse_args(argv)
    info = S.create(a.root, a.sid, a.language, a.vault)
    print(json.dumps({"ok": True, **info, "next": NEXT_ACTION["intake"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_submit(argv) -> int:
    ap = argparse.ArgumentParser(prog="plc_cli.py submit")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    ap.add_argument("--qid", required=True); ap.add_argument("--file")
    ap.add_argument("--code"); ap.add_argument("--language")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    if a.file:
        path = S.save_submission_file(a.root, a.sid, a.qid, a.file, a.language)
    elif a.code is not None:
        path = S.save_submission(a.root, a.sid, a.qid, a.code, a.language)
    else:
        path = S.save_submission(a.root, a.sid, a.qid, sys.stdin.read(), a.language)
    st = S.load_state(a.root, a.sid)
    S.add_timing(a.root, a.sid, f"submit:{a.qid}", time.perf_counter() - t0)
    print(json.dumps({"ok": True, "saved": str(path), "phase": st["phase"],
                      "submitted": sorted(S.list_submissions(a.root, a.sid).keys()),
                      "note": "已收到提交。标准答案仍未读取，等全部提交后再批改。"},
                     ensure_ascii=False, indent=2))
    return 0


HINT_ORDER = ["L1", "L2", "L3"]


def cmd_submit_dir(argv) -> int:
    """一次性提交：目录里按题号命名的文件（Q1.c / Q2.py …）全部收进来。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py submit-dir")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    ap.add_argument("--dir", required=True); ap.add_argument("--language")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    ids = {q["id"]: (q.get("language") or a.language) for q in S.read_public(a.root, a.sid)}
    saved, unknown = [], []
    for f in sorted(Path(a.dir).iterdir()):
        if not f.is_file():
            continue
        if f.stem in ids:
            S.save_submission_file(a.root, a.sid, f.stem, str(f), ids[f.stem])
            saved.append(f.name)
        else:
            unknown.append(f.name)
    st = S.load_state(a.root, a.sid)
    S.add_timing(a.root, a.sid, "submit_dir", time.perf_counter() - t0, note=f"{len(saved)} files")
    print(json.dumps({"ok": True, "saved": saved, "unknown_files": unknown,
                      "submitted": sorted(S.list_submissions(a.root, a.sid).keys()),
                      "missing": sorted(set(ids) - set(S.list_submissions(a.root, a.sid))),
                      "phase": st["phase"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_hint(argv) -> int:
    """提示三级：只在申请时逐级给，不能跳级，永远不给完整答案。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py hint")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    ap.add_argument("--qid", required=True); ap.add_argument("--level", required=True, choices=HINT_ORDER)
    a = ap.parse_args(argv)
    st = S.load_state(a.root, a.sid)
    if st["phase"] != "awaiting_submission":
        print(json.dumps({"ok": False, "error": f'当前阶段 {st["phase"]} 不提供提示（提示只在等待提交期间使用）'},
                         ensure_ascii=False))
        return 9
    q = next((x for x in S.read_public(a.root, a.sid) if x["id"] == a.qid), None)
    if not q:
        print(json.dumps({"ok": False, "error": f"没有这道题：{a.qid}"}, ensure_ascii=False))
        return 9
    hints = q.get("hints") or {}
    log = st.setdefault("hint_log", [])
    granted = [e["level"] for e in log if e["qid"] == a.qid]
    idx = HINT_ORDER.index(a.level)
    if idx > 0 and HINT_ORDER[idx - 1] not in granted:
        print(json.dumps({"ok": False, "granted": granted,
                          "error": f"必须先申请 {HINT_ORDER[idx-1]} 才能申请 {a.level}（提示逐级给，不跳级）"},
                         ensure_ascii=False))
        return 9
    if a.level in granted:
        print(json.dumps({"ok": False, "granted": granted,
                          "error": f"{a.level} 已经给过了，不重复提供"}, ensure_ascii=False))
        return 9
    text = hints.get(a.level)
    if not text:
        print(json.dumps({"ok": False, "error": f"本题没有准备 {a.level} 提示"}, ensure_ascii=False))
        return 9
    log.append({"qid": a.qid, "level": a.level, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    S.save_state(a.root, a.sid, st)
    print(json.dumps({"ok": True, "qid": a.qid, "level": a.level, "hint": text,
                      "granted": [e["level"] for e in log if e["qid"] == a.qid],
                      "rule": "L1 只说考点，L2 给方向，L3 给伪代码；标准答案永远不会在这里出现"},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(argv) -> int:
    ap = argparse.ArgumentParser(prog="plc_cli.py status")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    a = ap.parse_args(argv)
    info = S.inspect(a.root, a.sid)
    info["next"] = NEXT_ACTION.get(info["phase"], "")
    info["timings"] = S.timings(a.root, a.sid)
    info["timing_table"] = S.timing_table(a.root, a.sid)
    info["scope_confirmation"] = S.load_state(a.root, a.sid).get("scope_confirmation")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_next(argv) -> int:
    ap = argparse.ArgumentParser(prog="plc_cli.py next")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid")
    a = ap.parse_args(argv)
    if not a.sid:
        print(json.dumps({"phases": S.PHASES, "actions": NEXT_ACTION,
                          "stage_refs": STAGE_REFS}, ensure_ascii=False, indent=2))
        return 0
    ph = S.phase(a.root, a.sid)
    print(json.dumps({"phase": ph, "next": NEXT_ACTION.get(ph, ""),
                      "read_only_these_refs": [str(REFS_DIR / n) for n in STAGE_REFS.get(ph, [])],
                      "timing_table": S.timing_table(a.root, a.sid)}, ensure_ascii=False, indent=2))
    return 0


STAGE_REFS = {
    "intake": ["note-parsing.md"],
    "analysis": ["note-parsing.md", "mastery-rules.md"],
    "scope_confirmed": ["mastery-rules.md"],
    "questioning": ["question-rules.md", "hint-levels.md"],
    "awaiting_submission": ["hint-levels.md"],
    "grading": ["grading-template.md", "mastery-rules.md"],
    "practice_doc": ["grading-template.md", "vault-rules.md"],
    "recommend": ["code-reading-rules.md"],
    "reading_doc": ["code-reading-rules.md", "vault-rules.md"],
    "archive_updated": ["mastery-rules.md", "vault-rules.md"],
}
REFS_DIR = Path(__file__).resolve().parent.parent / "references"


def cmd_refs(argv) -> int:
    """只列出当前阶段需要读的 references，避免一次性加载全部规则文件。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py refs")
    ap.add_argument("--stage")
    ap.add_argument("--root", default=".plc")
    ap.add_argument("--sid")
    a = ap.parse_args(argv)
    stage = a.stage or (S.phase(a.root, a.sid) if a.sid else None)
    if not stage:
        print(json.dumps({"stages": STAGE_REFS, "refs_dir": str(REFS_DIR)}, ensure_ascii=False, indent=2))
        return 0
    names = STAGE_REFS.get(stage)
    if names is None:
        print(json.dumps({"ok": False, "error": f"未知阶段 {stage}", "stages": list(STAGE_REFS)},
                         ensure_ascii=False))
        return 1
    files = [{"path": str(REFS_DIR / n), "bytes": (REFS_DIR / n).stat().st_size} for n in names]
    print(json.dumps({"ok": True, "stage": stage, "read_only_these": files,
                      "total_bytes": sum(f["bytes"] for f in files),
                      "note": "本阶段只读这些；其它 references 等到对应阶段再读，能显著减少上下文与耗时"},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_prepare(argv) -> int:
    """一次进程跑完：建会话 + 解析笔记(带缓存) + 进度分析(带缓存) + 输出范围表与歧义表。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py prepare")
    ap.add_argument("--notes", action="append", required=True, help="笔记文件或目录，可重复")
    ap.add_argument("--root", default=".plc")
    ap.add_argument("--sid", required=True)
    ap.add_argument("--language", default="c")
    ap.add_argument("--records", required=True)
    ap.add_argument("--report-out", default="notes-report.json")
    ap.add_argument("--analysis-out", default="analysis.json")
    ap.add_argument("--cache-dir", default=".plc/cache")
    ap.add_argument("--glossary")
    ap.add_argument("--vault")
    a = ap.parse_args(argv)
    t_all = time.perf_counter()
    try:
        S.create(a.root, a.sid, a.language, a.vault)
        created = True
    except S.SessionError:
        created = False                      # 会话已存在则复用，绝不覆盖

    t0 = time.perf_counter()
    rep = parse_notes.parse(a.notes, a.glossary, a.cache_dir)
    t_parse = time.perf_counter() - t0
    Path(a.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report_out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
    S.add_timing(a.root, a.sid, "parse_notes", t_parse, cached=bool((rep.get("cache") or {}).get("hit")))

    records = M.load(a.records)
    t0 = time.perf_counter()
    res = analyze.analyze_cached(rep, records, a.glossary, a.cache_dir)
    t_analyze = time.perf_counter() - t0
    Path(a.analysis_out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.analysis_out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8", newline="\n")
    M.save(a.records, records)
    S.add_timing(a.root, a.sid, "analyze", t_analyze, cached=bool((res.get("cache") or {}).get("hit")))

    if S.phase(a.root, a.sid) == "intake":
        S.advance(a.root, a.sid, "analysis")

    # 范围自动锁定：T1~T3 可用，T4 一律按未学排除；记录到会话与学习档案，不要求学习者回复
    excluded = res.get("excluded_weak_kps") or []
    prev = (records.get("scope_notes") or {}).get("excluded_weak") or []
    new_excluded = [k for k in excluded if k not in prev]
    records["scope_notes"] = {"excluded_weak": excluded, "new_excluded": new_excluded,
                              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    for kp in excluded:                      # 自动记为「未学」（学习者可用 scope-promote 改回）
        if kp not in M.confirmed_decisions(records):
            M.set_confirmed_scope(records, res.get("notes_fingerprint", ""),
                                  {kp: {"decision": "not_learned", "tier": 4}})
    M.save(a.records, records)
    S.set_scope_confirmation(a.root, a.sid, {
        "mode": "auto-lock", "excluded_weak": excluded, "new_excluded": new_excluded,
        "usable": res.get("usable_kps", []), "notes_fingerprint": res.get("notes_fingerprint", "")})
    if S.phase(a.root, a.sid) == "analysis":
        S.advance(a.root, a.sid, "scope_confirmed")
    S.add_timing(a.root, a.sid, "prepare_total", time.perf_counter() - t_all,
                 note=f"session_created={created}")
    print(json.dumps({
        "ok": True, "session_id": a.sid, "session_created": created,
        "phase": S.phase(a.root, a.sid),
        "report": a.report_out, "analysis": a.analysis_out, "records": a.records,
        "counts": res["counts"], "cold_start": res["cold_start"],
        "tier_summary": {k: len(v) for k, v in res["tier_summary"].items()},
        "usable_kps": res["usable_kps"],
        "excluded_weak_kps": res.get("excluded_weak_kps", []),
        "new_excluded": new_excluded,
        "not_learned_kps": [x["kp"] for x in res["not_learned"]],
        "scope_current": res["scope_current"], "notes_unchanged": res.get("notes_unchanged"),
        "cache": {"parse_hit": bool((rep.get("cache") or {}).get("hit")),
                  "analysis_hit": bool((res.get("cache") or {}).get("hit"))},
        "timings": S.timings(a.root, a.sid),
        "next": "范围已自动锁定，直接出 10 题（无需确认）",
        "summary_lines": analyze.summary_lines(res)}, ensure_ascii=False, indent=2))
    return 0


def cmd_scope_confirm(argv) -> int:
    """v3：范围自动锁定，无需人工确认。保留此命令仅为兼容旧流程。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py scope-confirm")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    ap.add_argument("--records"); ap.add_argument("--analysis")
    ap.add_argument("--learner-confirmed", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--answers")
    a = ap.parse_args(argv)
    if S.phase(a.root, a.sid) == "analysis":
        S.advance(a.root, a.sid, "scope_confirmed")
    print(json.dumps({"ok": True, "phase": S.phase(a.root, a.sid), "mode": "auto-lock",
                      "note": "范围已自动锁定（T1~T3 可用，T4 按未学排除），无需人工确认。",
                      "next": NEXT_ACTION["scope_confirmed"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_scope_promote(argv) -> int:
    """把某个弱证据知识点「改为已学」，下一轮出题生效。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py scope-promote")
    ap.add_argument("--records", required=True)
    ap.add_argument("--kp", action="append", required=True, help="知识点名，可重复")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid")
    a = ap.parse_args(argv)
    records = M.load(a.records)
    for kp in a.kp:
        M.set_confirmed_scope(records, (records.get("confirmed_scope") or {}).get("notes_fingerprint", ""),
                              {kp: {"decision": "learned", "source": "learner-promote"}})
    M.save(a.records, records)
    notes = records.get("scope_notes") or {}
    remaining = [k for k in (notes.get("excluded_weak") or []) if k not in a.kp]
    print(json.dumps({"ok": True, "promoted": a.kp, "remaining_excluded": remaining,
                      "note": "下一轮 prepare/出题时这些知识点会纳入可用范围。"}, ensure_ascii=False, indent=2))
    return 0


def cmd_grade_render(argv) -> int:
    """一次进程跑完：批改（沙箱/逐行对照/人工判定）+ 渲染练习文档 + 推进阶段。"""
    ap = argparse.ArgumentParser(prog="plc_cli.py grade-render")
    ap.add_argument("--root", default=".plc"); ap.add_argument("--sid", required=True)
    ap.add_argument("--records", required=True); ap.add_argument("--notes")
    ap.add_argument("--meta"); ap.add_argument("--analysis")
    ap.add_argument("--out", required=True); ap.add_argument("--grading-out")
    ap.add_argument("--template-dir")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--max-output", type=int, default=20000)
    ap.add_argument("--workroot")
    ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args(argv)
    records = M.load(a.records)
    notes = json.loads(Path(a.notes).read_text(encoding="utf-8")) if a.notes else {}
    meta = json.loads(Path(a.meta).read_text(encoding="utf-8")) if a.meta else {}
    analysis = json.loads(Path(a.analysis).read_text(encoding="utf-8")) if a.analysis else {}

    t0 = time.perf_counter()
    rep = grading.grade_all(a.root, a.sid, records, notes, run=not a.no_run, timeout=a.timeout,
                            max_output=a.max_output, workroot=a.workroot)
    t_grade = time.perf_counter() - t0
    M.save(a.records, records)
    S.add_timing(a.root, a.sid, "grade", t_grade,
                 note=f'ran={rep["sandbox_available"]} submitted={rep["overall"]["submitted"]}')
    if a.grading_out:
        Path(a.grading_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.grading_out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8", newline="\n")

    questions = S.read_public(a.root, a.sid)
    subs = {qid: S.load_submission(a.root, a.sid, qid) for qid in S.list_submissions(a.root, a.sid)}
    t0 = time.perf_counter()
    doc = render_md.render_practice(grading=rep, questions=questions, submissions=subs,
                                    records=records, analysis=analysis, meta=meta,
                                    template_dir=a.template_dir)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(doc, encoding="utf-8", newline="\n")
    S.add_timing(a.root, a.sid, "render_practice", time.perf_counter() - t0,
                 note=f"{len(doc.encode('utf-8'))} bytes")
    if S.phase(a.root, a.sid) == "grading":
        S.advance(a.root, a.sid, "practice_doc")
    print(json.dumps({"ok": True, "phase": S.phase(a.root, a.sid), "out": a.out,
                      "bytes": len(doc.encode("utf-8")),
                      "frontmatter_errors": render_md.validate_frontmatter(doc),
                      "overall": {k: rep["overall"][k] for k in
                                  ("submitted", "unsubmitted_ids", "score_submitted_only",
                                   "score_including_unsubmitted_as_zero", "dimensions_missing")},
                      "pending_model_input": {r["id"]: r["pending_model_input"] for r in rep["results"]
                                              if r["pending_model_input"]},
                      "timings": S.timings(a.root, a.sid),
                      "timing_table": S.timing_table(a.root, a.sid)}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    local = {"new": cmd_new, "scope-confirm": cmd_scope_confirm, "submit": cmd_submit,
             "submit-dir": cmd_submit_dir, "hint": cmd_hint, "status": cmd_status,
             "next": cmd_next, "prepare": cmd_prepare, "grade-render": cmd_grade_render,
             "refs": cmd_refs, "scope-promote": cmd_scope_promote}
    if cmd in local:
        return local[cmd](rest)
    if cmd == "grade" and "--root" in rest:
        return ALIASES["grade"][1](["grade"] + rest)
    if cmd in ALIASES:
        return ALIASES[cmd][1](rest)
    print(json.dumps({"ok": False, "error": f"未知子命令 {cmd}",
                      "available": sorted(set(list(ALIASES) + list(local)))}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (S.SessionError, questions.QuestionError, grading.GradingError,
            vault_write.VaultError, M.MasteryError, render_md.IncompleteDocument,
            reading.ReadingError) as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(4)
