#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持久化配置：Vault 路径、默认语言、题量等。

原则：Vault 路径**问一次、记住**。只有路径不存在或不可写时才报错询问。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
FALLBACK_VAULT = "<Vault 路径>"
DEFAULTS = {"vault": FALLBACK_VAULT, "language": "c", "question_count": 10,
            "difficulty_order": "asc", "scope_policy": "t1_t3_usable_t4_excluded"}


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    data = dict(DEFAULTS)
    if p.exists():
        try:
            data.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass                       # 配置损坏就用默认值，不阻断
    return data


def save(cfg: dict, path: str | Path | None = None) -> Path:
    p = Path(path) if path else CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULTS, **cfg}
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return p


def vault(path: str | Path | None = None) -> str:
    """当前 Vault 路径（优先调用方显式传入，其次配置文件，最后内置默认）。"""
    if path:
        return str(path)
    return str(load().get("vault") or FALLBACK_VAULT)


def is_writable(p: str | Path) -> bool:
    """权限判断：先看 os.access，再试写临时文件。

    注意：某些沙箱/受管环境里试写会被拦截（这不是真实权限问题），
    所以 os.access 说可写时直接认为可写；真正的失败由实际写入时的 OSError 兜底报错。
    """
    d = Path(p)
    if not d.is_dir():
        return False
    if os.access(str(d), os.W_OK):
        return True
    try:
        fd, tmp = tempfile.mkstemp(prefix=".plc-write-test-", dir=str(d))
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


def check_vault(path: str | Path | None = None) -> tuple[str, str | None]:
    """返回 (路径, 错误信息)。错误信息为 None 表示可以直接写。"""
    v = vault(path)
    p = Path(v)
    if not p.exists():
        return v, f"配置的 Vault 路径不存在：{v}。请告诉我新的路径（我不会猜）。"
    if not p.is_dir():
        return v, f"配置的 Vault 路径不是目录：{v}。请确认。"
    if not is_writable(p):
        return v, f"配置的 Vault 路径不可写：{v}。请检查权限或告诉我新的路径。"
    return v, None
