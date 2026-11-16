#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""笔记解析：md / txt / docx 用标准库；pdf 与 OCR 为可选依赖，缺失时明确降级；图片交给模型直接阅读。

输出统一 JSON（供 analyze.py 使用），每个知识点都带「依据」（文件 + 行号 + 片段）。
用法：
  python parse_notes.py --input <笔记路径> --json out.json
  python parse_notes.py --input a.md --input b.docx --glossary refs/knowledge-glossary.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glossary as G  # noqa: E402

DEFAULT_LANGUAGE = "c"          # 笔记里检测不到具体语言时，按这个语言过滤（可用 --language 改）
TEXT_EXT = {".md", ".markdown", ".txt", ".text"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VECTOR_EXT = {".svg"}
SKIP_DIRS = {".obsidian", ".git", ".trash", ".claudian", "node_modules", "__pycache__",
             ".private", ".plc", "cache", "sessions"}
# 工具自身产生的文件（记录/分析/缓存/会话）不是笔记，扫描目录时必须跳过，
# 否则第二次 prepare 会把 records.json 里的知识点名当成"新笔记"重新解析一遍
SKIP_FILES = {"records.json", "analysis.json", "notes-report.json", "questions.json",
              "questions.public.json", "answers.private.json", "state.json", "index.json",
              "build.json", "grading.json", "verified.json", "reading-verified.json"}
SKIP_EXTS = {".json", ".pyc", ".pyo", ".log", ".tmp", ".bak"}
PITFALL_RE = re.compile(r"(易错|常见错误|陷阱|注意[:：]|注意事项|坑|不要忘|容易错|mistake|pitfall|warning|gotcha)", re.I)
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+#-]*)\s*$")
HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
WML = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --------------------------------------------------------------------------- 读取
def read_text_bytes(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1(兜底)"


def parse_docx(path: Path) -> tuple[str, list[str]]:
    """用标准库 zipfile + ElementTree 解析 docx，返回 (纯文本, 标题列表)。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(r"word/(document|header\d*|footnote)s?\.xml$", n)]
        if "word/document.xml" not in names:
            names = ["word/document.xml"] + [n for n in names if n != "word/document.xml"]
        chunks, headings = [], []
        for name in names:
            if name not in z.namelist():
                continue
            root = ET.fromstring(z.read(name))
            for p in root.iter(f"{WML}p"):
                style = ""
                ppr = p.find(f"{WML}pPr")
                if ppr is not None:
                    st = ppr.find(f"{WML}pStyle")
                    if st is not None:
                        style = st.get(f"{WML}val", "")
                text = "".join(t.text or "" for t in p.iter(f"{WML}t"))
                chunks.append(text)
                if re.match(r"(Heading|heading|标题)\s*\d?", style) and text.strip():
                    headings.append(text.strip())
        return "\n".join(chunks), headings


def parse_pdf(path: Path) -> tuple[str | None, str]:
    """可选依赖：pypdf -> PyPDF2 -> pdfminer。都没有则返回 (None, 降级说明)。"""
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages), "pypdf"
    except ImportError:
        pass
    except Exception as e:  # 文件损坏等
        return None, f"pypdf 解析失败: {e}"
    try:
        import PyPDF2  # type: ignore
        reader = PyPDF2.PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages), "PyPDF2"
    except ImportError:
        pass
    except Exception as e:
        return None, f"PyPDF2 解析失败: {e}"
    try:
        from pdfminer.high_level import extract_text  # type: ignore
        return extract_text(str(path)), "pdfminer"
    except ImportError:
        return None, ("未安装 PDF 解析库（pip install pypdf），已降级：不解析该 PDF 正文。"
                      "可改为把内容导出为 md/txt，或把关键页截图发给我由模型直接阅读。")
    except Exception as e:
        return None, f"pdfminer 解析失败: {e}"


def collect_inputs(paths: list[str], limit: int = 500) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                for fn in sorted(filenames):
                    if fn.startswith(".") or fn in SKIP_FILES:
                        continue
                    if Path(fn).suffix.lower() in SKIP_EXTS:
                        continue
                    out.append(Path(dirpath) / fn)
        elif p.exists():
            out.append(p)
        else:
            out.append(p)   # 交给上层记 warning：路径不存在
    seen, uniq = set(), []
    for p in out:
        k = str(p.resolve()) if p.exists() else str(p)
        if k not in seen:
            seen.add(k); uniq.append(p)
        if len(uniq) >= limit:
            break
    return uniq


# --------------------------------------------------------------------------- 结构抽取
def extract_code_blocks(lines: list[str], source: str) -> list[dict]:
    blocks, cur, lang, start = [], None, "", 0
    fence = None
    for i, line in enumerate(lines, 1):
        m = FENCE_RE.match(line)
        if m and cur is None and m.group(1)[0] in "`~":
            fence, lang, start, cur = m.group(1)[0], (m.group(2) or "").lower(), i, []
            continue
        if cur is not None and fence and re.match(r"^\s*" + re.escape(fence) + r"{3,}\s*$", line):
            code = "\n".join(cur)
            blocks.append({"source": source, "line": start, "lang": lang or guess_lang(code), "code": code})
            cur, fence = None, None
            continue
        if cur is not None:
            cur.append(line)
    if cur:
        code = "\n".join(cur)
        blocks.append({"source": source, "line": start, "lang": lang or guess_lang(code),
                       "code": code, "warning": "代码块未闭合（文件结尾）"})
    return blocks


def guess_lang(code: str) -> str:
    if re.search(r"^\s*#include\s*<", code, re.M) or re.search(r"\bprintf\s*\(", code) or re.search(r"\bint\s+main\s*\(", code):
        return "c"
    if re.search(r"^\s*def\s+\w+\s*\(", code, re.M) or re.search(r"^\s*import\s+\w+", code, re.M) or "print(" in code:
        return "python"
    if "#include <iostream>" in code or "std::" in code:
        return "cpp"
    if "public static void main" in code:
        return "java"
    if "function " in code or "=>" in code:
        return "javascript"
    return ""


def extract_headings(lines: list[str], source: str, extra: list[str] | None = None) -> list[dict]:
    out = []
    for i, line in enumerate(lines, 1):
        m = HEAD_RE.match(line)
        if m:
            out.append({"source": source, "line": i, "level": len(m.group(1)), "text": m.group(2).strip()})
    for h in extra or []:
        out.append({"source": source, "line": 0, "level": 1, "text": h})
    return out


def detect_languages(blocks: list[dict], text: str) -> dict:
    langs: dict[str, int] = {}
    for b in blocks:
        if b["lang"]:
            langs[b["lang"]] = langs.get(b["lang"], 0) + len(b["code"].splitlines()) + 1
    if re.search(r"^\s*#include\s*<", text, re.M) or "printf" in text:
        langs["c"] = langs.get("c", 0) + 3
    if re.search(r"^\s*def\s+\w+\s*\(|print\(", text, re.M):
        langs["python"] = langs.get("python", 0) + 3
    return dict(sorted(langs.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- 主解析
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def inputs_fingerprint(files: list[Path], glossary_path: str | None = None) -> str:
    """笔记内容 + 词库版本的指纹：任一变化都会让缓存失效。"""
    h = hashlib.sha256()
    for p in files:
        h.update(str(p).encode("utf-8", "replace"))
        if p.exists():
            h.update(str(p.stat().st_size).encode())
            h.update(_sha256(p).encode())
        else:
            h.update(b"missing")
    g = Path(glossary_path) if glossary_path else G.DEFAULT_GLOSSARY
    if g.exists():
        h.update(_sha256(g).encode())
    return h.hexdigest()


def parse(paths: list[str], glossary_path: str | None = None, cache_dir: str | Path | None = None,
          language: str | None = None) -> dict:
    files = collect_inputs(paths)
    fp = inputs_fingerprint(files, glossary_path)
    if language:
        import hashlib as _h
        fp = _h.sha256((fp + "|lang=" + language).encode()).hexdigest()
    if cache_dir:
        cdir = Path(cache_dir)
        cdir.mkdir(parents=True, exist_ok=True)
        cfile = cdir / f"notes-{fp[:16]}.json"
        if cfile.exists():
            try:
                cached = json.loads(cfile.read_text(encoding="utf-8"))
                cached["cache"] = {"hit": True, "path": str(cfile), "fingerprint": fp}
                return cached
            except (json.JSONDecodeError, OSError):
                pass                                # 缓存坏了就重算，不影响正确性
    report = _parse_uncached(files, glossary_path, language)
    report["fingerprint"] = fp
    if cache_dir:
        cfile = Path(cache_dir) / f"notes-{fp[:16]}.json"
        payload = {k: v for k, v in report.items() if k != "cache"}
        cfile.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
        report["cache"] = {"hit": False, "path": str(cfile), "fingerprint": fp}
    else:
        report["cache"] = {"hit": False, "path": None, "fingerprint": fp}
    return report


def _parse_uncached(files: list[Path], glossary_path: str | None = None,
                    language: str | None = None) -> dict:
    entries = G.load(glossary_path)
    report: dict = {
        "version": 1, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": [], "text_sources": [], "image_sources": [], "code_blocks": [],
        "headings": [], "pitfalls": [], "languages": {}, "knowledge": [],
        "warnings": [], "degraded": [],
    }
    all_knowledge, all_text = [], []
    for p in files:
        rel = str(p)
        ext = p.suffix.lower()
        if not p.exists():
            report["warnings"].append(f"路径不存在，已跳过：{rel}")
            report["inputs"].append({"path": rel, "format": ext.lstrip("."), "status": "missing"})
            continue
        size = p.stat().st_size
        if ext in IMAGE_EXT or ext in VECTOR_EXT:
            report["image_sources"].append({"path": rel, "bytes": size, "status": "needs_model_vision",
                                            "note": "图片由模型直接阅读并转写；脚本不做 OCR（未安装 OCR 依赖时同样走此路径）"})
            report["inputs"].append({"path": rel, "format": ext.lstrip("."), "status": "needs_model_vision", "bytes": size})
            continue
        if ext == ".docx":
            try:
                text, doc_headings = parse_docx(p)
                fmt, status, note = "docx", "ok", "标准库 zipfile+ElementTree 解析"
            except Exception as e:
                text, doc_headings, fmt, status, note = "", [], "docx", "failed", f"docx 解析失败：{e}"
        elif ext == ".pdf":
            text, note = parse_pdf(p)
            fmt, status = "pdf", ("ok" if text is not None else "degraded")
            text = text or ""
            doc_headings = []
            if status == "degraded":
                report["degraded"].append({"path": rel, "format": "pdf", "reason": note})
        elif ext in {".doc", ".pptx", ".xlsx", ".rtf"}:
            fmt, status, note = ext.lstrip("."), "degraded", f"{ext} 暂不支持：请另存为 md/txt/docx/pdf 后再发给我"
            text, doc_headings = "", []
            report["degraded"].append({"path": rel, "format": fmt, "reason": note})
        elif ext in TEXT_EXT or ext == "":
            text, enc = read_text_bytes(p)
            fmt, status, note, doc_headings = ext.lstrip(".") or "txt", "ok", f"encoding={enc}", []
        else:
            fmt, status, note = ext.lstrip(".") or "unknown", "degraded", f"未知格式 {ext}，按纯文本尝试读取"
            try:
                text, _ = read_text_bytes(p); doc_headings = []
            except Exception as e:
                text, note = "", f"读取失败：{e}"
            report["degraded"].append({"path": rel, "format": fmt, "reason": note})

        lines = text.splitlines()
        report["inputs"].append({"path": rel, "format": fmt, "status": status, "bytes": size, "note": note,
                                 "chars": len(text), "lines": len(lines)})
        report["text_sources"].append({"path": rel, "lines": len(lines), "chars": len(text)})
        blocks = extract_code_blocks(lines, rel)
        report["code_blocks"].extend(blocks)
        report["headings"].extend(extract_headings(lines, rel, doc_headings))
        for i, line in enumerate(lines, 1):
            if PITFALL_RE.search(line) and line.strip():
                s = line.strip()
                report["pitfalls"].append({"source": rel, "line": i,
                                           "text": s[:200] + ("…" if len(s) > 200 else "")})
        code_lines = set()
        for b in blocks:
            code_lines.update(range(b["line"], b["line"] + b["code"].count("\n") + 3))
        all_knowledge.append(G.match_lines(entries, lines, rel, code_lines=code_lines))
        all_text.append(text)

    # 语言过滤放在全局：先检测语言，再决定哪些词条可以参与匹配（C 笔记不匹配 Python 词条）
    report["languages"] = detect_languages(report["code_blocks"], "\n".join(all_text))
    allowed = G.allowed_langs(report["languages"])
    if allowed is None:
        # 纯文字笔记（没有代码块、也没有 printf/#include 之类特征）：
        # 按 --language 指定的语言过滤，避免 C 笔记把 Python 词条也匹配进来
        allowed = {"*", language or DEFAULT_LANGUAGE}
        report["language_fallback"] = sorted(allowed - {"*"})
        report["allowed_glossary_langs"] = sorted(allowed)
        kept, dropped = [], []
        for chunk in all_knowledge:
            keep = [f for f in chunk if f["lang"] in allowed]
            kept.append(keep)
            dropped.extend(f["kp"] for f in chunk if f["lang"] not in allowed)
    else:
        report["allowed_glossary_langs"] = sorted(allowed)
        kept, dropped = [], []
        for chunk in all_knowledge:
            keep = [f for f in chunk if f["lang"] in allowed]
            kept.append(keep)
            dropped.extend(f["kp"] for f in chunk if f["lang"] not in allowed)
    report["knowledge"] = G.merge(kept)
    report["filtered_out_kp"] = sorted(set(dropped))
    tiers = {}
    for k in report["knowledge"]:
        tiers.setdefault(k["tier"], []).append(k["kp"])
    report["tier_summary"] = {f"T{t}": sorted(v) for t, v in sorted(tiers.items())}
    if report["image_sources"]:
        report["warnings"].append(
            f'有 {len(report["image_sources"])} 张图片笔记需要模型直接阅读后补充依据（脚本不猜测图片内容）')
    if not report["text_sources"] and not report["image_sources"]:
        report["warnings"].append("没有解析到任何可用笔记内容")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 笔记解析")
    ap.add_argument("--input", action="append", required=True, help="文件或目录，可重复")
    ap.add_argument("--glossary", default=None)
    ap.add_argument("--language", default=None,
                    help="笔记检测不到具体语言时的兜底语言（c / python …），默认 c")
    ap.add_argument("--cache-dir", default=None,
                    help="解析缓存目录（如 .plc/cache）；笔记内容与词库都没变时直接复用上次结果")
    ap.add_argument("--json", default=None, help="输出 JSON 路径；缺省打印到 stdout")
    ap.add_argument("--summary", action="store_true", help="只打印摘要")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    rep = parse(a.input, a.glossary, a.cache_dir, a.language)
    rep["elapsed_seconds"] = round(time.perf_counter() - t0, 3)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(text + "\n", encoding="utf-8", newline="\n")
    if a.summary or a.json:
        print(json.dumps({
            "inputs": len(rep["inputs"]), "ok": sum(1 for i in rep["inputs"] if i["status"] == "ok"),
            "degraded": [d["path"] for d in rep["degraded"]],
            "images_need_vision": [i["path"] for i in rep["image_sources"]],
            "languages": rep["languages"], "knowledge_points": len(rep["knowledge"]),
            "code_blocks": len(rep["code_blocks"]), "warnings": rep["warnings"],
            "tier_summary": {k: len(v) for k, v in (rep.get("tier_summary") or {}).items()},
            "cache_hit": (rep.get("cache") or {}).get("hit"),
            "elapsed_seconds": rep.get("elapsed_seconds"),
            "fingerprint": (rep.get("cache") or {}).get("fingerprint", "")[:16],
            "out": a.json,
        }, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
