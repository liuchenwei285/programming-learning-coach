#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话存储与答案隔离（机械保证，不靠模型自觉）。

三件事：
1) 阶段门 PHASES：流程必须按顺序推进，禁止跳步（尤其禁止跳过「等待提交」）。
2) 答案隔离：标准答案/测试用例只写入 .private/<session_id>/answers.private.json，
   公开题目文件 questions.public.json 在写入前会被剥离所有私有字段。
3) 读取权限：read_private() 只允许在批改及之后的阶段调用；出题阶段调用直接抛错。

用法示例：
  python session_store.py create --root .plc --sid s1 --language c
  python session_store.py phase  --root .plc --sid s1
  python session_store.py advance --root .plc --sid s1 --to questioning
  python session_store.py inspect --root .plc --sid s1     # 只显示公开信息 + 私有文件哈希
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

PHASES = [
    "intake",              # 接收笔记
    "analysis",            # 分析进度
    "scope_confirmed",     # 展示知识点范围并经学习者确认
    "questioning",         # 出题（答案写入 .private，不输出）
    "awaiting_submission", # 暂停，等待提交答案
    "grading",             # 批改
    "practice_doc",        # 生成练习文档
    "recommend",           # 推荐 10 份代码精读
    "reading_doc",         # 生成精读文档
    "archive_updated",     # 更新学习档案
]

# 任何出现在题目对象里的这些键都必须留在私有文件中
PRIVATE_KEYS = {
    "reference_solution", "reference_solution_language", "tests", "test_cases",
    "explanation", "better_approach", "expected_output", "expected_stdout",
    "answer", "solution", "hints_L3_pseudocode_full_answer",
    # 文本作答题型（输出预测/追踪/分析/辨析）的期望答案与评分要点
    "expected_answer", "answer_points", "self_check_answers",
}

# 只有进入这些阶段后才允许读取私有答案
PRIVATE_ALLOWED_PHASES = {"grading", "practice_doc", "recommend", "reading_doc", "archive_updated"}


class SessionError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 路径
def session_dir(root: str | os.PathLike, sid: str) -> Path:
    if not sid or any(c in sid for c in '\\/:*?"<>|') or sid in {".", ".."}:
        raise SessionError(f"非法 session_id: {sid!r}")
    return Path(root) / ".private" / sid


def _paths(root, sid) -> dict:
    d = session_dir(root, sid)
    return {
        "dir": d,
        "state": d / "state.json",
        "public": d / "questions.public.json",
        "private": d / "answers.private.json",
        "submissions": d / "submissions",
        "events": d / "events.json",
        "grading": d / "grading.json",
    }


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, obj, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    if private:
        _harden(path)


def _harden(path: Path) -> None:
    """尽力收紧私有文件权限（Windows 无 POSIX 权限位，属best-effort）。"""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, NotImplementedError):
        pass


# --------------------------------------------------------------------------- 生命周期
def create(root, sid: str | None = None, language: str = "c", vault: str | None = None) -> dict:
    sid = sid or time.strftime("plc-%Y%m%d-%H%M%S")
    p = _paths(root, sid)
    if p["state"].exists():
        raise SessionError(f"会话已存在: {sid}（不会覆盖，请换一个 session_id）")
    p["dir"].mkdir(parents=True, exist_ok=True)
    p["submissions"].mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": sid,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": "intake",
        "phase_history": [{"phase": "intake", "at": time.strftime("%Y-%m-%d %H:%M:%S")}],
        "language": language,
        "vault": vault,
        "scope_confirmed": False,
        "scope_confirmation": None,
        "notes_report": None,
        "records": None,
        "timings": [],
    }
    _write_json(p["state"], state)
    _harden(p["dir"])
    return {"session_id": sid, **{k: str(v) for k, v in p.items()}}


def load_state(root, sid) -> dict:
    st = _read_json(_paths(root, sid)["state"])
    if st is None:
        raise SessionError(f"会话不存在: {sid}")
    return st


def save_state(root, sid, state: dict) -> None:
    _write_json(_paths(root, sid)["state"], state)


def phase(root, sid) -> str:
    return load_state(root, sid)["phase"]


def advance(root, sid, to_phase: str, *, force: bool = False) -> dict:
    """按 PHASES 顺序推进。跳步直接报错（force 仅用于人工纠错，会记录在案）。"""
    st = load_state(root, sid)
    cur = st["phase"]
    if to_phase not in PHASES:
        raise SessionError(f"未知阶段: {to_phase}")
    i_cur, i_to = PHASES.index(cur), PHASES.index(to_phase)
    if i_to <= i_cur:
        raise SessionError(f"阶段不可回退/重复: {cur} -> {to_phase}")
    if i_to > i_cur + 1 and not force:
        raise SessionError(
            f"禁止跳步: 当前 {cur}，目标 {to_phase}，中间缺失 {PHASES[i_cur + 1:i_to]}。"
            f"（流程必须逐步推进，尤其不能跳过 awaiting_submission）"
        )
    # 硬门：进入 grading 前必须有提交记录
    if to_phase == "grading":
        subs = list_submissions(root, sid)
        if not subs:
            raise SessionError("禁止批改：还没有任何提交。必须等学习者提交答案后才能进入 grading。")
    # v3：范围自动锁定（T1~T3 可用、T4 按未学排除），进入该阶段即自动置位，不再要求人工确认
    if to_phase == "scope_confirmed":
        st["scope_confirmed"] = True
    # 硬门：进入 questioning 前必须确认过知识点范围
    if to_phase == "questioning" and not st.get("scope_confirmed"):
        raise SessionError("禁止出题：知识点范围尚未由学习者确认（scope_confirmed=false）。")
    st["phase"] = to_phase
    st.setdefault("phase_history", []).append({"phase": to_phase, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_state(root, sid, st)
    return st


# --------------------------------------------------------------------------- 题目 / 答案
def strip_private(obj):
    """递归剥离私有键，返回可公开的副本。"""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if k not in PRIVATE_KEYS}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def assert_no_private(payload) -> None:
    """公开载荷里若仍含私有键或私有值，直接抛错。"""
    bad_keys = []

    def walk(o, path="$"):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in PRIVATE_KEYS:
                    bad_keys.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(payload)
    if bad_keys:
        raise SessionError(f"公开题目中含答案字段: {bad_keys}")


def write_questions(root, sid, questions: list) -> dict:
    """写公开题目（已剥离答案）+ 私有答案（单独文件）。"""
    p = _paths(root, sid)
    public = []
    private = {}
    for q in questions:
        public.append(strip_private(q))
        priv = {k: v for k, v in q.items() if k in PRIVATE_KEYS}
        if priv:
            private[q.get("id", f"q{len(private)+1}")] = priv
    assert_no_private(public)
    _write_json(p["public"], {"session_id": sid, "questions": public})
    _write_json(p["private"], {"session_id": sid, "answers": private}, private=True)
    return {"public_path": str(p["public"]), "private_path": str(p["private"]),
            "questions": len(public), "answers": len(private)}


def read_public(root, sid) -> list:
    data = _read_json(_paths(root, sid)["public"], {"questions": []})
    assert_no_private(data)
    return data["questions"]


def read_private(root, sid) -> dict:
    st = load_state(root, sid)
    if st["phase"] not in PRIVATE_ALLOWED_PHASES:
        raise SessionError(
            f"禁止读取标准答案：当前阶段 {st['phase']}，只有进入 {sorted(PRIVATE_ALLOWED_PHASES)} 后才允许。"
        )
    data = _read_json(_paths(root, sid)["private"], {"answers": {}})
    return data["answers"]


def private_digest(root, sid) -> str:
    p = _paths(root, sid)["private"]
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------- 提交
EXT_BY_LANG = {"c": ".c", "cpp": ".cpp", "c++": ".cpp", "python": ".py", "py": ".py",
               "java": ".java", "javascript": ".js", "js": ".js", "text": ".txt"}


def save_submission(root, sid, qid: str, code: str, language: str | None = None,
                    submitted_by_learner: bool = True) -> Path:
    p = _paths(root, sid)
    if not qid or any(c in qid for c in '\\/:*?"<>|'):
        raise SessionError(f"非法题号: {qid!r}")
    p["submissions"].mkdir(parents=True, exist_ok=True)
    ext = EXT_BY_LANG.get((language or load_state(root, sid).get("language") or "text").lower(), ".txt")
    path = p["submissions"] / f"{qid}{ext}"
    path.write_text(code, encoding="utf-8", newline="\n")
    idx_path = p["submissions"] / "index.json"
    idx = _read_json(idx_path, {})
    idx[qid] = {"file": path.name, "language": language, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "submitted_by_learner": submitted_by_learner, "sha256": hashlib.sha256(code.encode()).hexdigest()[:16]}
    _write_json(idx_path, idx)
    st = load_state(root, sid)
    if st["phase"] == "questioning":
        st["phase"] = "awaiting_submission"
        st["phase_history"].append({"phase": "awaiting_submission", "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        save_state(root, sid, st)
    return path


def save_submission_file(root, sid, qid: str, file_path: str, language: str | None = None) -> Path:
    code = Path(file_path).read_text(encoding="utf-8")
    return save_submission(root, sid, qid, code, language)


def list_submissions(root, sid) -> dict:
    return _read_json(_paths(root, sid)["submissions"] / "index.json", {})


def load_submission(root, sid, qid: str) -> str | None:
    info = list_submissions(root, sid).get(qid)
    if not info:
        return None
    return (_paths(root, sid)["submissions"] / info["file"]).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- 掌握度事件
def append_events(root, sid, events: list) -> int:
    p = _paths(root, sid)["events"]
    data = _read_json(p, {"events": []})
    data["events"].extend(events)
    _write_json(p, data)
    return len(data["events"])


def read_events(root, sid) -> list:
    return _read_json(_paths(root, sid)["events"], {"events": []})["events"]


def set_field(root, sid, **fields) -> dict:
    st = load_state(root, sid)
    st.update(fields)
    save_state(root, sid, st)
    return st


# --------------------------------------------------------------------------- 耗时与范围确认
def add_timing(root, sid, stage: str, seconds: float, *, cached: bool = False, note: str = "") -> dict:
    st = load_state(root, sid)
    entry = {"stage": stage, "seconds": round(float(seconds), 4), "cached": bool(cached),
             "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if note:
        entry["note"] = note
    st.setdefault("timings", []).append(entry)
    save_state(root, sid, st)
    return entry


def timings(root, sid) -> list:
    return load_state(root, sid).get("timings", [])


def timing_table(root, sid) -> str:
    ts = timings(root, sid)
    if not ts:
        return "（还没有耗时记录）"
    lines = ["| 阶段 | 耗时(s) | 缓存命中 |", "| :-- | --: | :-- |"]
    for t in ts:
        lines.append(f'| {t["stage"]} | {t["seconds"]} | {"是" if t.get("cached") else "否"} |')
    lines.append(f'| **合计** | **{round(sum(t["seconds"] for t in ts), 3)}** | |')
    return "\n".join(lines)


def set_scope_confirmation(root, sid, payload: dict) -> dict:
    """记录范围是怎么确认的：auto（无歧义项）/ learner（学习者答复）/ answers（歧义表逐项判定）。"""
    payload = dict(payload)
    payload["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st = load_state(root, sid)
    prev = st.get("scope_confirmation")
    if prev:
        payload["previous"] = prev          # 保留上一次确认，可追溯
    st["scope_confirmation"] = payload
    st["scope_confirmed"] = True
    save_state(root, sid, st)
    return st


# --------------------------------------------------------------------------- 自检
def inspect(root, sid) -> dict:
    """给模型/人看的安全视图：绝不包含标准答案内容，只给哈希。"""
    st = load_state(root, sid)
    qs = read_public(root, sid)
    return {
        "session_id": sid,
        "phase": st["phase"],
        "phase_history": st.get("phase_history", []),
        "language": st.get("language"),
        "scope_confirmed": st.get("scope_confirmed"),
        "question_ids": [q.get("id") for q in qs],
        "question_titles": [q.get("title") for q in qs],
        "submitted": sorted(list_submissions(root, sid).keys()),
        "private_answer_digest": private_digest(root, sid),
        "private_readable_now": st["phase"] in PRIVATE_ALLOWED_PHASES,
        "note": "标准答案与测试用例不在本视图中；只有进入批改阶段后由 grading.py 读取。",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 会话与答案隔离")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(s):
        s.add_argument("--root", default=".plc")
        s.add_argument("--sid", required=True)

    s = sub.add_parser("create"); common(s); s.add_argument("--language", default="c"); s.add_argument("--vault")
    s = sub.add_parser("phase"); common(s)
    s = sub.add_parser("advance"); common(s); s.add_argument("--to", required=True); s.add_argument("--force", action="store_true")
    s = sub.add_parser("inspect"); common(s)
    s = sub.add_parser("confirm-scope"); common(s)
    s = sub.add_parser("submit"); common(s); s.add_argument("--qid", required=True)
    s.add_argument("--code"); s.add_argument("--file"); s.add_argument("--language")
    s = sub.add_parser("questions"); common(s)
    a = ap.parse_args(argv)

    if a.cmd == "create":
        print(json.dumps(create(a.root, a.sid, a.language, getattr(a, "vault", None)), ensure_ascii=False, indent=2))
    elif a.cmd == "phase":
        print(phase(a.root, a.sid))
    elif a.cmd == "advance":
        st = advance(a.root, a.sid, a.to, force=a.force)
        print(json.dumps({"phase": st["phase"]}, ensure_ascii=False))
    elif a.cmd == "inspect":
        print(json.dumps(inspect(a.root, a.sid), ensure_ascii=False, indent=2))
    elif a.cmd == "confirm-scope":
        set_field(a.root, a.sid, scope_confirmed=True)
        print(json.dumps({"scope_confirmed": True}, ensure_ascii=False))
    elif a.cmd == "submit":
        if a.file:
            path = save_submission_file(a.root, a.sid, a.qid, a.file, a.language)
        elif a.code is not None:
            path = save_submission(a.root, a.sid, a.qid, a.code, a.language)
        else:
            code = sys.stdin.read()
            path = save_submission(a.root, a.sid, a.qid, code, a.language)
        print(json.dumps({"saved": str(path)}, ensure_ascii=False))
    elif a.cmd == "questions":
        print(json.dumps(read_public(a.root, a.sid), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SessionError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(4)
