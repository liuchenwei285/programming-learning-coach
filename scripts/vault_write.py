#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Obsidian Vault 写入。安全第一：先确认路径，永不覆盖、永不删除、重名自动加 (2)。

规则（与 references/vault-rules.md 一致）：
* Vault 路径从 config.json 读取；显式传入的路径找不到 .obsidian 就停下来问，绝不猜。
* **默认写进 Vault 根目录**（和学习者已有笔记放在一起）；加 --in-subdirs 才用
  Programming Learning Coach/{练习记录,代码精读,学习档案}，且优先复用 Vault 里已有的同名目录。
* 文件名：X月X日练习.md / X月X日代码精读.md；年份只写进 frontmatter 的 date。
* 重名 -> X月X日练习(2).md、(3).md …… 绝不覆盖已有文件。
* 学习档案（学习进度.md / 知识点掌握记录.md）：先备份到 _backup/，再把旧内容整段保留为「历史记录」。
* 只允许写 Vault 内部；任何 ../ 越界路径直接拒绝。
* 默认 dry-run，必须显式 --yes 才落盘。

用法：
  python vault_write.py plan   --vault "<Vault 路径>"
  python vault_write.py note   --vault "<Vault 路径>" --kind practice --date 2026-11-01 --content doc.md --yes
  python vault_write.py archive --vault "<Vault 路径>" --name 学习进度.md --body body.md --yes
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

SUBDIRS = {"practice": "练习记录", "reading": "代码精读", "archive": "学习档案"}
ROOT_DIR = "Programming Learning Coach"
ARCHIVE_FILES = ("学习进度.md", "知识点掌握记录.md")
BACKUP_DIR = "_backup"
SKIP_DIRS = {".obsidian", ".git", ".trash", ".claudian", "node_modules", ".private"}
KIND_BY_NAME = {"练习": "practice", "代码精读": "reading"}


class VaultError(RuntimeError):
    pass


def configured_vault(path: str | None = None) -> Path:
    """Vault 路径「问一次、记住」：未显式传入时从 config.json 读。"""
    import config as CFG
    v, err = CFG.check_vault(path)
    if err:
        raise VaultError(err)
    return Path(v).resolve()


def looks_like_vault(p: Path) -> bool:
    return (p / ".obsidian").is_dir()


def resolve_vault(path: str | None, *, strict: bool = True) -> Path:
    if not path:
        return configured_vault()          # 配置里已记住，不再询问
    p = Path(path).expanduser()
    if not p.exists():
        raise VaultError(f"路径不存在：{p}。请确认你的 Obsidian Vault 目录。")
    if not p.is_dir():
        raise VaultError(f"不是目录：{p}")
    if strict and not looks_like_vault(p):
        raise VaultError(f"{p} 里没有 .obsidian 目录，看起来不是 Obsidian Vault。"
                         f"请确认路径；确需写入请加 --allow-non-vault（不推荐）。")
    return p.resolve()


def safe_join(base: Path, *parts) -> Path:
    out = (base.joinpath(*[str(x) for x in parts])).resolve()
    if not str(out).startswith(str(base.resolve())):
        raise VaultError(f"拒绝越界写入：{out} 不在 Vault {base} 内")
    return out


def find_existing_dirs(vault: Path, max_depth: int = 3) -> dict:
    """优先复用 Vault 里已有的同名目录（不新建重复结构）。"""
    found = {k: None for k in SUBDIRS}
    stack = [(vault, 0)]
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            if not e.is_dir() or e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            for kind, name in SUBDIRS.items():
                if e.name == name and found[kind] is None:
                    found[kind] = e
            stack.append((e, depth + 1))
    return found


def ensure_dir(vault: Path, kind: str, dry_run: bool = True) -> dict:
    """只准备当前需要的那一个目录 —— 不在学习者的 Vault 里留下多余空目录。"""
    if kind not in SUBDIRS:
        raise VaultError(f"kind 必须是 {list(SUBDIRS)}，当前 {kind!r}")
    existing = find_existing_dirs(vault).get(kind)
    if existing is not None:
        return {"path": existing, "created": False, "reused": True}
    d = safe_join(vault, ROOT_DIR, SUBDIRS[kind])
    if not dry_run:
        d.mkdir(parents=True, exist_ok=True)
    return {"path": d, "created": not dry_run, "reused": False}


def ensure_dirs(vault: Path, dry_run: bool = True) -> dict:
    return {kind: ensure_dir(vault, kind, dry_run) for kind in SUBDIRS}


def date_to_stem(date_str: str, kind: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str or "")
    if not m:
        raise VaultError(f"date 必须是 YYYY-MM-DD（年份写进 frontmatter，文件名只用月日），当前 {date_str!r}")
    _, mm, dd = m.groups()
    suffix = {"practice": "练习", "reading": "代码精读", "archive": "档案"}[kind]
    return f"{int(mm)}月{int(dd)}日{suffix}"


def unique_path(directory: Path, stem: str, suffix: str = ".md") -> Path:
    """重名自动 (2)(3)…，绝不覆盖。"""
    cand = directory / f"{stem}{suffix}"
    n = 2
    while cand.exists():
        cand = directory / f"{stem}({n}){suffix}"
        n += 1
        if n > 999:
            raise VaultError(f"重名过多（>999）：{stem}")
    return cand


def write_note(vault: Path, kind: str, date_str: str, content: str, *, dry_run: bool = True,
               allow_non_vault: bool = False, at_root: bool = True) -> dict:
    if not allow_non_vault:
        vault = resolve_vault(str(vault))
    else:
        vault = Path(vault).resolve()
        if not vault.is_dir():
            raise VaultError(f"目录不存在：{vault}")
    if kind not in SUBDIRS:
        raise VaultError(f"kind 必须是 {list(SUBDIRS)}，当前 {kind!r}")
    if at_root:
        # 默认：直接写进 Vault 根目录（和学习者已有的《10月18日练习.md》放在一起）
        target_dir, info = vault, {"path": vault, "reused": True, "created": False}
    else:
        info = ensure_dir(vault, kind, dry_run=dry_run)
        target_dir = Path(info["path"])
    stem = date_to_stem(date_str, kind)
    path = unique_path(target_dir, stem) if not dry_run else (target_dir / f"{stem}.md")
    if not dry_run:
        if path.exists():
            raise VaultError(f"内部错误：目标文件已存在，拒绝覆盖：{path}")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        except OSError as e:
            raise VaultError(f"Vault 不可写：{target_dir}（{e}）。请检查权限或告诉我新的路径。")
    return {"ok": True, "dry_run": dry_run, "kind": kind, "dir": str(target_dir),
            "at_root": at_root, "reused_existing_dir": info["reused"], "path": str(path),
            "bytes": len(content.encode("utf-8")),
            "overwrite": False,
            "note": "同名文件已自动改名，未覆盖任何已有文件" if not dry_run else "dry-run：未写入任何文件"}


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bdir = path.parent / BACKUP_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = bdir / f"{path.stem}.bak.{ts}{path.suffix}"
    n = 2
    while bak.exists():
        bak = bdir / f"{path.stem}.bak.{ts}-{n}{path.suffix}"
        n += 1
    shutil.copy2(path, bak)
    return bak


def update_archive(vault: Path, name: str, body: str, *, dry_run: bool = True,
                   allow_non_vault: bool = False, at_root: bool = True) -> dict:
    """学习档案：带备份的更新，旧内容整段保留为历史记录（追加式，不丢历史）。"""
    if name not in ARCHIVE_FILES:
        raise VaultError(f"档案文件只能是 {ARCHIVE_FILES}，当前 {name!r}")
    if not allow_non_vault:
        vault = resolve_vault(str(vault))
    else:
        vault = Path(vault).resolve()
    if at_root:
        path = vault / name
    else:
        path = Path(ensure_dir(vault, "archive", dry_run=dry_run)["path"]) / name
    old = path.read_text(encoding="utf-8") if path.exists() else None
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if old is None:
        new_content = f"# {Path(name).stem}\n\n> 本文件由 programming-learning-coach 维护：只追加或带备份更新，历史记录永久保留。\n\n{body.rstrip()}\n"
    else:
        new_content = (f"# {Path(name).stem}\n\n> 本文件由 programming-learning-coach 维护：只追加或带备份更新，历史记录永久保留。\n\n"
                       f"{body.rstrip()}\n\n---\n\n## 历史记录（更新于 {ts}，以下为上一次的全部内容）\n\n{old.rstrip()}\n")
    bak = None
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        bak = _backup(path)
        path.write_text(new_content, encoding="utf-8", newline="\n")
    return {"ok": True, "dry_run": dry_run, "path": str(path), "existed": old is not None,
            "backup": str(bak) if bak else None, "bytes": len(new_content.encode("utf-8")),
            "old_bytes_preserved": (old.rstrip() in new_content) if old else True,
            "deleted_anything": False}


def plan(vault: Path, allow_non_vault: bool = False) -> dict:
    if not allow_non_vault:
        vault = resolve_vault(str(vault))
    else:
        vault = Path(vault).resolve()
    existing = find_existing_dirs(vault)
    dirs = ensure_dirs(vault, dry_run=True)
    return {"vault": str(vault), "is_vault": looks_like_vault(vault),
            "existing_subdirs": {k: (str(v) if v else None) for k, v in existing.items()},
            "planned_dirs": {k: {"path": str(v["path"]), "reused": v["reused"]} for k, v in dirs.items()},
            "archive_files_present": [n for n in ARCHIVE_FILES
                                      if (Path(dirs["archive"]["path"]) / n).exists()],
            "top_level_dirs": [e.name for e in sorted(vault.iterdir())
                               if e.is_dir() and e.name not in SKIP_DIRS][:20],
            "write_policy": ("默认 dry-run；--yes 才写入；默认写进 Vault 根目录（--in-subdirs 可改子目录）；"
                             "重名自动 (2)；档案先备份到 _backup/；不删除任何文件")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC Obsidian 写入")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "note", "archive"):
        s = sub.add_parser(name)
        s.add_argument("--vault", default=None, help="省略则使用 config.json 里记住的路径")
        s.add_argument("--allow-non-vault", action="store_true")
        s.add_argument("--yes", action="store_true", help="真正写入（缺省为 dry-run）")
        s.add_argument("--at-root", action="store_true",
                       help="（默认行为）直接写进 Vault 根目录，不建子目录")
        s.add_argument("--in-subdirs", action="store_true",
                       help="改写进 练习记录/代码精读/学习档案 子目录（优先复用 Vault 里已有的同名目录）")
        if name == "note":
            s.add_argument("--kind", required=True, choices=list(SUBDIRS))
            s.add_argument("--date", required=True)
            s.add_argument("--content", required=True, help="Markdown 文件路径，或 - 表示 stdin")
        if name == "archive":
            s.add_argument("--name", required=True)
            s.add_argument("--body", required=True)
    a = ap.parse_args(argv)
    dry = not a.yes
    a.at_root = not getattr(a, "in_subdirs", False)
    if not a.vault:
        import config as CFG
        a.vault = CFG.vault()
    if a.cmd == "plan":
        print(json.dumps(plan(a.vault, a.allow_non_vault), ensure_ascii=False, indent=2)); return 0
    if a.cmd == "note":
        content = sys.stdin.read() if a.content == "-" else Path(a.content).read_text(encoding="utf-8")
        print(json.dumps(write_note(a.vault, a.kind, a.date, content, dry_run=dry,
                                    allow_non_vault=a.allow_non_vault, at_root=a.at_root),
                         ensure_ascii=False, indent=2))
    else:
        body = sys.stdin.read() if a.body == "-" else Path(a.body).read_text(encoding="utf-8")
        print(json.dumps(update_archive(a.vault, a.name, body, dry_run=dry,
                                        allow_non_vault=a.allow_non_vault, at_root=a.at_root),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VaultError as e:
        print(json.dumps({"ok": False, "error": str(e), "action_needed": "需要学习者确认 Vault 路径"},
                         ensure_ascii=False))
        sys.exit(2)
