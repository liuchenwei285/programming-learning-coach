#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五级掌握度状态机。核心原则：笔记里出现过 ≠ 已掌握，掌握度只由练习表现决定。

状态：未学习(0) / 学习中(1) / 待检验(2) / 基本掌握(3) / 熟练掌握(4)

升级规则（与 references/mastery-rules.md 一致）：
  * 一次答对最多升到「待检验」；
  * ≥2 道不同题目答对 → 「基本掌握」；
  * ≥3 道不同题目答对 且 跨 ≥2 次练习 且 含 1 次组合题答对 → 「熟练掌握」；
  * 答错 → 降一级（不低于「未学习」）。
冷启动（无练习记录）：只按笔记标「学习中 / 待检验」，并强制 estimated=true。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


STATES = ["未学习", "学习中", "待检验", "基本掌握", "熟练掌握"]
IDX = {s: i for i, s in enumerate(STATES)}
NOTE_MAX_STATE = "待检验"          # 仅凭笔记能达到的上限
DEMOTE_FLOOR_WITH_NOTES = True    # 有笔记依据时，答错最低降到「学习中」而不是「未学习」
LEARNED_STATES = {"学习中", "待检验", "基本掌握", "熟练掌握"}   # 出题可用范围
SCOPED_MIN_INDEX = 1


class MasteryError(RuntimeError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def blank(kp: str, lang: str = "*") -> dict:
    return {
        "kp": kp, "lang": lang, "state": "未学习", "state_index": 0,
        "estimated": True, "note_evidence": [], "practice_evidence": [],
        "correct_questions": [], "wrong_questions": [], "sessions": [],
        "combined_correct": False, "correct_count": 0, "wrong_count": 0,
        "history": [], "updated_at": None,
    }


def load(path: str | Path | None) -> dict:
    if not path or not Path(path).exists():
        return {"version": 1, "cold_start": True, "updated_at": None, "records": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("records", {})
    data.setdefault("cold_start", not data["records"])
    return data


def save(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = 1
    data["updated_at"] = _now()
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(p)


# ------------------------------------------------------------------- 范围确认（写进学习档案）
def confirmed_decisions(data: dict) -> dict:
    """{知识点: "learned"|"not_learned"}：学习档案里已经确认过的判定。"""
    items = ((data.get("confirmed_scope") or {}).get("items") or {})
    return {kp: v.get("decision") for kp, v in items.items()
            if isinstance(v, dict) and v.get("decision") in ("learned", "not_learned")}


def set_confirmed_scope(data: dict, notes_fingerprint: str, items: dict) -> dict:
    """把学习者的判定写进档案。旧判定不删除：变更进 history，可追溯。

    items: {知识点: {"decision": "learned"|"not_learned", "tier": int, "reason": str}}
    """
    scope = data.setdefault("confirmed_scope", {"items": {}, "history": []})
    prev = scope.get("items") or {}
    changed = {k: {"old": prev.get(k), "new": v} for k, v in items.items() if prev.get(k) != v}
    merged = dict(prev)
    merged.update(items)
    scope["items"] = merged
    scope["notes_fingerprint"] = notes_fingerprint or scope.get("notes_fingerprint", "")
    scope["confirmed_at"] = _now()
    if changed:
        scope.setdefault("history", []).append({"at": _now(), "notes_fingerprint": notes_fingerprint,
                                                "changed": changed})
    return scope


def confirmed_scope_is_current(data: dict, notes_fingerprint: str) -> bool:
    """笔记指纹与上次确认时一致 → 直接沿用，不再询问。"""
    scope = data.get("confirmed_scope") or {}
    return bool(notes_fingerprint) and scope.get("notes_fingerprint") == notes_fingerprint \
        and bool(scope.get("confirmed_at"))


def record(data: dict, kp: str, lang: str = "*") -> dict:
    return data["records"].setdefault(kp, blank(kp, lang))


def state_of(data: dict, kp: str) -> str:
    r = data["records"].get(kp)
    return r["state"] if r else "未学习"


def in_scope(data: dict, kp: str) -> bool:
    """出题可用：状态必须是「学习中」及以上。未学习不得作为解题必要条件。"""
    r = data["records"].get(kp)
    return bool(r) and r["state_index"] >= SCOPED_MIN_INDEX


# ------------------------------------------------------------------- 冷启动（笔记 -> 初始估计）
# 证据等级 -> 初始状态：T1 小标题系统讲解 / T2 真实代码示例 -> 待检验；T3 正文明确提及 -> 学习中
TIER_STATE = {1: "待检验", 2: "待检验", 3: "学习中", 4: "学习中"}
TIER_BASIS = {1: "笔记中有小标题系统讲解（T1）", 2: "笔记中有真实代码示例（T2）",
              3: "笔记正文明确提及≥3次（T3）", 4: "弱证据，经学习者确认算已学（T4）"}


def from_note_knowledge(data: dict, knowledge: list[dict], *, overwrite: bool = False,
                        confirmed: dict | None = None) -> dict:
    """把笔记解析结果写进档案。

    硬约束：
    * 只产生「学习中 / 待检验」，且 estimated=True（笔记出现过 ≠ 已掌握）；
    * T1~T3 直接写入（不逐项询问）；
    * T4 弱证据只有被学习者确认 learned 才写入；未确认或判为 not_learned 的一律不写入，
      状态保持「未学习」，因此**不能**成为出题范围。
    """
    confirmed = confirmed if confirmed is not None else confirmed_decisions(data)
    for k in knowledge:
        kp = k["kp"]
        tier = int(k.get("tier") or 4)
        decision = confirmed.get(kp)
        if tier >= 4 and decision != "learned":
            continue                     # 歧义项未确认 -> 不进档案 -> 未学习 -> 禁止出题
        rec = record(data, kp, k.get("lang", "*"))
        rec["tier"] = tier
        rec["tier_reason"] = k.get("tier_reason", "")
        rec["confirmed_by_learner"] = bool(decision)
        if rec["state_index"] >= IDX["基本掌握"] and not overwrite:
            continue            # 已由练习确认的掌握度，绝不被笔记降级或改写
        basis = TIER_BASIS.get(tier, "笔记中仅提及")
        target = IDX[TIER_STATE.get(tier, "学习中")]
        rec["note_evidence"] = [f'{Path(str(e["source"])).name}:{e["line"]} {e["snippet"]}'
                                for e in k.get("evidence", [])]
        rec["note_basis"] = basis
        if rec["state_index"] < target and not rec["practice_evidence"]:
            rec["state"] = STATES[target]
            rec["state_index"] = target
        rec["estimated"] = True
        if rec["state_index"] > IDX[NOTE_MAX_STATE] and not rec["practice_evidence"]:
            raise MasteryError(f"内部错误：仅凭笔记不可高于 {NOTE_MAX_STATE}（{kp}）")
    data["cold_start"] = not any(r["practice_evidence"] for r in data["records"].values())
    return data


# ------------------------------------------------------------------- 练习事件 -> 升降级
def evidence_target(rec: dict) -> int:
    """按累计练习证据算出「应得」状态索引。"""
    cq = set(rec.get("correct_questions", []))
    ss = set(rec.get("sessions", []))
    if len(cq) >= 3 and len(ss) >= 2 and rec.get("combined_correct"):
        return IDX["熟练掌握"]
    if len(cq) >= 2:
        return IDX["基本掌握"]
    if cq:
        return IDX["待检验"]
    return rec["state_index"]


def apply_event(rec: dict, ev: dict) -> dict:
    kp = ev.get("kp") or rec["kp"]
    qid = ev.get("question_id")
    sid = ev.get("session_id")
    combined = bool(ev.get("combined"))
    before = rec["state_index"]
    if ev.get("correct"):
        rec.setdefault("practice_evidence", [])
        rec["practice_evidence"].append({"type": "practice", "session_id": sid, "question_id": qid,
                                         "result": "correct", "combined": combined, "at": _now()})
        if qid and qid not in rec["correct_questions"]:
            rec["correct_questions"].append(qid)
        if sid and sid not in rec["sessions"]:
            rec["sessions"].append(sid)
        rec["correct_count"] = rec.get("correct_count", 0) + 1
        if combined:
            rec["combined_correct"] = True
        target = evidence_target(rec)
        # 「一次答对最多升到待检验」：低于待检验时，单次答对封顶在待检验
        cap = IDX[NOTE_MAX_STATE] if before < IDX[NOTE_MAX_STATE] else target
        rec["state_index"] = max(before, min(target, cap))
        rec["estimated"] = False
        action = "promote" if rec["state_index"] > before else ("keep" if rec["state_index"] == before else "demote")
    else:
        rec.setdefault("practice_evidence", [])
        rec["practice_evidence"].append({"type": "practice", "session_id": sid, "question_id": qid,
                                         "result": "wrong", "combined": combined, "at": _now()})
        if qid and qid not in rec["wrong_questions"]:
            rec["wrong_questions"].append(qid)
        rec["wrong_count"] = rec.get("wrong_count", 0) + 1
        # 答错降一级；但「未学习」= 完全没有依据，笔记教过的知识点不能因为答错就变成没学过
        floor = 1 if (DEMOTE_FLOOR_WITH_NOTES and (rec.get("note_evidence") or rec.get("note_basis"))) else 0
        rec["state_index"] = max(floor, before - 1)
        rec["estimated"] = False
        action = "demote"
    rec["state"] = STATES[rec["state_index"]]
    rec["updated_at"] = _now()
    rec.setdefault("history", []).append({"at": _now(), "session_id": sid, "question_id": qid,
                                          "correct": bool(ev.get("correct")), "combined": combined,
                                          "from": STATES[before], "to": rec["state"], "action": action})
    return rec


def apply_events(data: dict, events: list[dict], glossary: dict | None = None) -> dict:
    for ev in events:
        kp = ev.get("kp")
        if not kp:
            raise MasteryError(f"事件缺少 kp: {ev}")
        lang = (glossary or {}).get(kp, "*")
        apply_event(record(data, kp, lang), ev)
    data["cold_start"] = not any(r["practice_evidence"] for r in data["records"].values())
    return data


# ------------------------------------------------------------------- 派生视图
def weak_points(data: dict) -> list[dict]:
    """薄弱点：答错过题目，或练过但一次都没答对。

    注意：不是「状态 ≤ 待检验」就算薄弱 —— 冷启动时几乎所有知识点都在待检验，
    那样会把整张表都标红，失去指导意义。
    """
    out = []
    for r in data["records"].values():
        wrong = r.get("wrong_questions") or []
        practiced = bool(r.get("practice_evidence"))
        never_correct = not r.get("correct_questions")
        if wrong:
            reason = f'答错过 {len(wrong)} 道题（{", ".join(str(x) for x in wrong[:3])}）'
        elif practiced and never_correct:
            reason = "练过但还没有答对过"
        else:
            continue
        out.append({"kp": r["kp"], "state": r["state"], "wrong_questions": wrong, "reason": reason})
    return sorted(out, key=lambda x: (-len(x["wrong_questions"]), x["kp"]))


def describe(data: dict) -> str:
    """给学习者看的知识点范围确认表（出题前必须展示）。"""
    groups = {s: [] for s in STATES}
    for r in data["records"].values():
        groups[r["state"]].append(r)
    lines = ["| 知识点 | 状态 | 依据 | 是否初始估计 |", "| :-- | :-- | :-- | :-- |"]
    for s in STATES:
        for r in sorted(groups[s], key=lambda x: x["kp"]):
            basis = r.get("note_basis") or ""
            if r["practice_evidence"]:
                last = r["practice_evidence"][-1]
                basis = (basis + "；" if basis else "") + f'练习 {last.get("session_id")} {last.get("question_id")} {"答对" if last.get("result")=="correct" else "答错"}'
            lines.append(f'| [[{r["kp"]}]] | {s} | {basis or "—"} | {"是" if r.get("estimated") else "否"} |')
    return "\n".join(lines)


def export_progress_md(data: dict) -> str:
    """学习档案：学习进度.md 的正文（追加式更新，不覆盖历史）。"""
    lines = [f'更新时间：{data.get("updated_at") or _now()}', ""]
    if data.get("cold_start"):
        lines.append("> ⚠️ 冷启动：尚无练习记录，下列状态仅为**初始估计**（依据笔记出现情况），"
                     "掌握度以练习表现为准；首次练习难度偏保守。")
        lines.append("")
    counts = {s: 0 for s in STATES}
    for r in data["records"].values():
        counts[r["state"]] += 1
    lines.append("| 状态 | 数量 |")
    lines.append("| :-- | :-- |")
    for s in STATES:
        lines.append(f"| {s} | {counts[s]} |")
    lines += ["", describe(data), ""]
    return "\n".join(lines)


def export_kp_md(data: dict) -> str:
    """学习档案：知识点掌握记录.md 的正文。"""
    lines = ["| 知识点 | 状态 | 答对题 | 答错题 | 参与练习 | 组合题答对 | 最近变化 |",
             "| :-- | :-- | :-- | :-- | :-- | :-- | :-- |"]
    for r in sorted(data["records"].values(), key=lambda x: (-x["state_index"], x["kp"])):
        last = r["history"][-1] if r.get("history") else None
        chg = f'{last["from"]} → {last["to"]}（{last["at"][:10]}）' if last else "初始估计" if r.get("estimated") else "—"
        lines.append(f'| [[{r["kp"]}]] | {r["state"]} | {len(r.get("correct_questions",[]))} | '
                     f'{len(r.get("wrong_questions",[]))} | {len(r.get("sessions",[]))} | '
                     f'{"是" if r.get("combined_correct") else "否"} | {chg} |')
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="五级掌握度状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--out", required=True)
    s = sub.add_parser("from-notes"); s.add_argument("--knowledge", required=True); s.add_argument("--records"); s.add_argument("--out", required=True)
    s = sub.add_parser("apply-events"); s.add_argument("--events", required=True); s.add_argument("--records", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("describe"); s.add_argument("--records", required=True)
    s = sub.add_parser("export"); s.add_argument("--records", required=True); s.add_argument("--kind", choices=["progress", "kp"], default="progress")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        data = {"version": 1, "cold_start": True, "records": {}}
        save(a.out, data); print(json.dumps({"ok": True, "out": a.out}, ensure_ascii=False))
    elif a.cmd == "from-notes":
        knowledge = json.loads(Path(a.knowledge).read_text(encoding="utf-8"))
        if isinstance(knowledge, dict):
            knowledge = knowledge.get("knowledge", [])
        data = load(a.records)
        from_note_knowledge(data, knowledge)
        save(a.out or a.records, data)
        print(json.dumps({"ok": True, "cold_start": data["cold_start"],
                          "records": len(data["records"])}, ensure_ascii=False))
    elif a.cmd == "apply-events":
        events = json.loads(Path(a.events).read_text(encoding="utf-8"))
        if isinstance(events, dict):
            events = events.get("events", [])
        data = load(a.records)
        apply_events(data, events)
        save(a.out, data)
        print(json.dumps({"ok": True, "records": len(data["records"]),
                          "weak_points": [w["kp"] for w in weak_points(data)]}, ensure_ascii=False))
    elif a.cmd == "describe":
        print(describe(load(a.records)))
    elif a.cmd == "export":
        data = load(a.records)
        print(export_progress_md(data) if a.kind == "progress" else export_kp_md(data))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MasteryError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(4)
