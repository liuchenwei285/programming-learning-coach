#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码精读推荐（10 份）的来源真实性验证 + 离线降级消毒。

规则（与 references/code-reading-rules.md 一致）：
* 每条推荐必须联网验证：仓库存在、文件路径存在、许可证；结果标「已验证 / 未验证」。
* 不能联网时只给项目名并标「待验证」：不给链接、不给路径、不给作者、不给许可证（宁缺勿造）。
* 不复制第三方整份代码，只给文件路径 + 函数名 + 行为描述。
* 10 份、难度递增、优先弥补本次薄弱点、选片段不选整项目。

用法：
  python verify_sources.py --recs recs.json --out verified.json
  python verify_sources.py --recs recs.json --offline          # 强制离线降级
  python verify_sources.py --recs recs.json --weak-points a.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REQUIRED_FIELDS = ["name", "language", "difficulty", "reason", "knowledge_points",
                   "focus", "method", "ideas", "tasks"]
ONLINE_FIELDS = ["url", "repo", "path", "license", "author", "source"]
UA = "programming-learning-coach/1.0 (skill; stdlib urllib)"
# 可选：设置环境变量 GITHUB_TOKEN 可把配额从 60 次/小时提到 5000 次/小时
TOKEN_ENV = "GITHUB_TOKEN"
API = "https://api.github.com"


class VerifyError(RuntimeError):
    pass


def probe_network(timeout: float = 6.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(f"{API}/zen", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 500), f"GitHub API 可达（HTTP {r.status}）"
    except Exception as e:
        return False, f"网络探测失败：{type(e).__name__}: {e}"


def gh_headers() -> dict:
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get(TOKEN_ENV)
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def gh_get(url: str, timeout: float = 12.0) -> tuple[int, dict | list | None, str]:
    try:
        req = urllib.request.Request(url, headers=gh_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            return r.status, data, ""
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()                      # 显式关闭，避免 ResourceWarning
        return code, None, f"HTTP {code}"
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def split_repo(item: dict) -> tuple[str | None, str | None]:
    repo = item.get("repo")
    if not repo and item.get("url"):
        m = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", item["url"])
        if m:
            repo = f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    if not repo or "/" not in repo:
        return None, None
    owner, name = repo.split("/", 1)
    return owner, name.removesuffix(".git")


_REPO_CACHE: dict[str, tuple[int, dict | None, str]] = {}


def rate_limit(timeout: float = 8.0) -> dict:
    """查 GitHub API 剩余配额（/rate_limit 本身不计入配额）。"""
    st, data, err = gh_get(f"{API}/rate_limit", timeout)
    if st != 200 or not data:
        return {"available": None, "detail": err or f"HTTP {st}"}
    core = (data.get("resources") or {}).get("core") or {}
    return {"available": core.get("remaining"), "limit": core.get("limit"),
            "reset_in_s": (core.get("reset", 0) - int(time.time())) if core.get("reset") else None}


def _repo_lookup(owner: str, name: str, timeout: float):
    key = f"{owner}/{name}".lower()
    if key not in _REPO_CACHE:
        _REPO_CACHE[key] = gh_get(f"{API}/repos/{owner}/{name}", timeout)
    return _REPO_CACHE[key]


def verify_item(item: dict, *, online: bool, timeout: float = 12.0) -> dict:
    out = dict(item)
    if not online:
        return sanitize_offline(out)
    owner, name = split_repo(item)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if not owner:
        out["verification"] = {"status": "未验证", "checked_at": ts, "repo_exists": False,
                               "path_exists": False, "license": None, "http_status": None,
                               "reason": "没有可验证的 GitHub 仓库（owner/name），未编造链接"}
        return out
    st, data, err = _repo_lookup(owner, name, timeout)
    if st == 0:
        out["verification"] = {"status": "未验证", "checked_at": ts, "repo_exists": None,
                               "path_exists": None, "license": None, "http_status": 0,
                               "reason": f"请求失败（{err}）—— 网络不可用或超时，按未验证处理"}
        return sanitize_offline(out)
    if st == 403 or st == 429:
        out["verification"] = {"status": "未验证", "checked_at": ts, "repo_exists": None,
                               "path_exists": None, "license": None, "http_status": st,
                               "reason": "GitHub API 限流（403/429），本次无法验证，标未验证"}
        return out
    if st != 200:
        out["verification"] = {"status": "未验证", "checked_at": ts, "repo_exists": False,
                               "path_exists": False, "license": None, "http_status": st,
                               "reason": f"仓库不存在或不可访问（{err or st}）—— 说明推荐条目有误，必须替换"}
        return out
    license_spdx = ((data or {}).get("license") or {}).get("spdx_id")
    default_branch = (data or {}).get("default_branch") or "HEAD"
    path = item.get("path")
    path_ok, path_reason, path_status = None, "未提供文件路径（推荐整项目？）", None
    if path:
        path_status, pdata, perr = gh_get(
            f"{API}/repos/{owner}/{name}/contents/{urllib.request.quote(path)}?ref={default_branch}", timeout)
        path_ok = path_status == 200
        if path_ok:
            path_reason = ""
        elif path_status in (403, 429):
            path_reason = f"GitHub API 限流（HTTP {path_status}），路径本次无法核验，标未验证"
        elif path_status == 404:
            path_reason = "文件路径不存在（HTTP 404）—— 推荐条目有误，必须替换"
        else:
            path_reason = perr or f"HTTP {path_status}"
        if path_ok and isinstance(pdata, dict) and pdata.get("type") == "dir":
            path_ok = False
            path_reason = "路径是目录而不是文件片段（应给出具体文件）"
    verified = bool(path_ok)
    out["license"] = license_spdx or item.get("license")
    out["repo"] = f"{owner}/{name}"
    out["url"] = item.get("url") or f"https://github.com/{owner}/{name}"
    out["verification"] = {"status": "已验证" if verified else "未验证", "checked_at": ts,
                           "repo_exists": True, "path_exists": path_ok, "license": license_spdx,
                           "default_branch": default_branch,
                           "http_status": path_status, "reason": path_reason or "仓库与文件路径均存在",
                           "size_bytes": (pdata or {}).get("size") if path_ok else None}
    return out


def sanitize_offline(item: dict) -> dict:
    """离线：只保留项目名与学习价值描述，删掉一切无法核实的链接/路径/作者/许可证。"""
    out = {k: v for k, v in item.items() if k not in ONLINE_FIELDS}   # license 也在 ONLINE_FIELDS 里，一并删除
    out["source"] = "（离线，未验证来源）"
    out["verification"] = {"status": "待验证", "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "repo_exists": None, "path_exists": None, "license": None,
                           "http_status": None,
                           "reason": "无法联网验证：按规则只给项目名并标『待验证』，不提供链接/路径/作者，避免编造"}
    return out


def check_rules(recs: list[dict], weak_points: list[str] | None = None) -> dict:
    errors, warnings = [], []
    if len(recs) != 10:
        errors.append(f"必须推荐 10 份，当前 {len(recs)} 份")
    diffs = [r.get("difficulty") for r in recs]
    if any(not isinstance(d, int) or not 1 <= d <= 5 for d in diffs):
        errors.append(f"difficulty 必须是 1..5 的整数：{diffs}")
    elif diffs != sorted(diffs):
        errors.append(f"难度必须递增（从易到难）：{diffs}")
    for i, r in enumerate(recs, 1):
        for f in REQUIRED_FIELDS:
            if not r.get(f):
                errors.append(f"第 {i} 份缺少必填字段 {f}")
        st = (r.get("verification") or {}).get("status")
        if st == "已验证" and not r.get("path"):
            errors.append(f"第 {i} 份《{r.get('name')}》没有具体文件路径 —— 要求选片段而不是整个项目")
        if st != "已验证":
            warnings.append(f"第 {i} 份《{r.get('name')}》状态：{st}（未通过联网验证，文档中不得给出链接与路径）")
        if not isinstance(r.get("tasks"), list) or len(r.get("tasks") or []) < 2:
            errors.append(f"第 {i} 份至少要给 2 条具体阅读任务")
    if weak_points:
        covered = {k for r in recs for k in (r.get("knowledge_points") or [])}
        miss = [w for w in weak_points if w not in covered]
        if miss:
            warnings.append(f"薄弱点未被精读覆盖：{miss}（应优先弥补本次薄弱点）")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "verified": sum(1 for r in recs if (r.get("verification") or {}).get("status") == "已验证"),
            "pending": sum(1 for r in recs if (r.get("verification") or {}).get("status") == "待验证"),
            "unverified": sum(1 for r in recs if (r.get("verification") or {}).get("status") == "未验证")}


def verify_all(recs: list[dict], *, offline: bool | None = None, timeout: float = 12.0,
               weak_points: list[str] | None = None) -> dict:
    reachable, probe = probe_network()
    if reachable:
        probe = f"{probe}；API 剩余配额 {rate_limit().get('available')}"
    if offline is True:
        online, probe = False, f"--offline 强制离线（探测结果：{probe}）"
    elif offline is False:
        online, probe = True, f"--online 强制联网（探测结果：{probe}）"
    else:
        online = reachable
    items = [verify_item(r, online=online, timeout=timeout) for r in recs]
    report = check_rules(items, weak_points)
    return {"mode": "online" if online else "offline", "probe": probe,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "recommendations": items, "rules": report}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 精读来源验证")
    ap.add_argument("--recs", required=True)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--weak-points")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    data = json.loads(Path(a.recs).read_text(encoding="utf-8"))
    recs = data.get("recommendations", data) if isinstance(data, dict) else data
    offline = True if a.offline else (False if a.online else None)
    weak = None
    if a.weak_points:
        w = json.loads(Path(a.weak_points).read_text(encoding="utf-8"))
        weak = w.get("weak_points", w) if isinstance(w, dict) else w
        weak = [x["kp"] if isinstance(x, dict) else x for x in weak]
    res = verify_all(recs, offline=offline, timeout=a.timeout, weak_points=weak)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"ok": res["rules"]["ok"], "mode": res["mode"], "probe": res["probe"],
                          "verified": res["rules"]["verified"], "pending": res["rules"]["pending"],
                          "unverified": res["rules"]["unverified"],
                          "errors": res["rules"]["errors"], "warnings": res["rules"]["warnings"],
                          "out": a.out}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0 if res["rules"]["ok"] else 7


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VerifyError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(7)
