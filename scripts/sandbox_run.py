#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沙箱执行：临时目录 + 超时 + 输出上限 + 网络阻断 + 不继承环境变量 + 用后清理。

诚实原则：语言工具链缺失或无法真正断网时，如实写进结果里（network_blocked=false + warnings），
绝不假装跑过。跑不了就 mode="static_review"，并输出「本次未运行，仅静态审查」。

用法：
  python sandbox_run.py --lang c --code sub.c --tests tests.json --timeout 5 --max-output 20000
  python sandbox_run.py --toolchains
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_OUTPUT = 20000
BLACKHOLE = "http://127.0.0.1:9"
EXT = {"c": ".c", "cpp": ".cpp", "python": ".py", "py": ".py", "java": ".java",
       "javascript": ".js", "js": ".js"}


# --------------------------------------------------------------------------- 工具链
def _which(name: str) -> str | None:
    return shutil.which(name)


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0][:120]
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def detect_toolchains() -> dict:
    tc = {}
    for lang, exe in (("c", "gcc"), ("cpp", "g++"), ("python", sys.executable or "python"),
                      ("java", "javac"), ("javascript", "node")):
        path = exe if (lang == "python" and exe and Path(exe).exists()) else _which(exe)
        if path:
            flag = "--version" if exe != "node" else "--version"
            tc[lang] = {"exe": path, "version": _version([path, flag])}
        else:
            tc[lang] = None
    tc["_python"] = {"exe": sys.executable, "version": sys.version.split()[0]}
    return tc


# --------------------------------------------------------------------------- 隔离
def _posix_net_guard() -> str | None:
    for cand in (("unshare", ["unshare", "-rn", "--"]), ("firejail", ["firejail", "--net=none", "--quiet", "--"])):
        if _which(cand[0]):
            return cand[0]
    return None


def build_env(tmpdir: Path, extra_paths: list[str] | None = None) -> tuple[dict, list[str]]:
    """不继承 os.environ，只给运行进程所需的最小集合。"""
    env = {}
    allow = []
    if os.name == "nt":
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        base = [str(Path(sysroot) / "System32"), str(Path(sysroot)), sysroot]
        env.update({"SystemRoot": sysroot, "windir": sysroot, "COMSPEC": str(Path(sysroot) / "System32" / "cmd.exe"),
                    "PATHEXT": ".COM;.EXE;.BAT;.CMD"})
        allow += ["SystemRoot", "windir", "COMSPEC", "PATHEXT"]
    else:
        base = ["/usr/local/bin", "/usr/bin", "/bin"]
        env.update({"HOME": str(tmpdir), "LANG": "C.UTF-8"})
        allow += ["HOME", "LANG"]
    for p in extra_paths or []:
        if p and p not in base:
            base.insert(0, p)
    env["PATH"] = os.pathsep.join(dict.fromkeys(base))
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(tmpdir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    allow += ["PATH", "TMPDIR", "TEMP", "TMP", "PYTHONIOENCODING", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE"]
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "ftp_proxy", "FTP_PROXY"):
        env[k] = BLACKHOLE
    env["no_proxy"] = ""
    env["NO_PROXY"] = ""
    allow += ["http_proxy", "https_proxy", "all_proxy", "no_proxy", "（大写同名）"]
    return env, allow


def _kill_tree(proc: subprocess.Popen) -> bool:
    """杀掉子进程（并尽力杀整棵进程树）。返回是否确认已终止 —— 不确认就如实报告。"""
    if proc.poll() is not None:
        return True
    try:
        proc.kill()                       # Windows: TerminateProcess / POSIX: SIGKILL
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    if proc.poll() is None:               # 直接 kill 无效时才动整棵树（taskkill 可能因权限被拒）
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=5)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    return proc.poll() is not None


def _limits():
    if os.name == "posix":
        import resource

        def pre():
            os.setsid()
            for res, val in ((resource.RLIMIT_CPU, 20), (resource.RLIMIT_AS, 1 << 30),
                             (resource.RLIMIT_FSIZE, 32 << 20), (resource.RLIMIT_NPROC, 128)):
                try:
                    resource.setrlimit(res, (val, val))
                except (ValueError, OSError):
                    pass
        return pre
    return None


def run_capped(cmd: list[str], *, cwd, env, stdin_text: str = "", timeout: float = DEFAULT_TIMEOUT,
               max_output: int = DEFAULT_MAX_OUTPUT) -> dict:
    """跑一条命令：超时杀进程树、输出超上限截断并杀进程。"""
    kwargs = {}
    pre = _limits()
    if pre:
        kwargs["preexec_fn"] = pre
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    caps = {"stdout": bytearray(), "stderr": bytearray()}
    trunc = {"stdout": False, "stderr": False}
    killed_for_output = {"v": False}

    def reader(stream, key):
        try:
            while True:
                chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
                if not chunk:
                    break
                room = max_output - len(caps[key])
                if room > 0:
                    caps[key].extend(chunk[:room])
                    if len(chunk) > room:
                        trunc[key] = True
                else:
                    trunc[key] = True
                if trunc[key]:
                    killed_for_output["v"] = True
                    _kill_tree(proc)
                    break
        except Exception:
            pass

    def feed():
        try:
            if stdin_text:
                proc.stdin.write(stdin_text.encode("utf-8", "replace"))
            proc.stdin.close()
        except Exception:
            pass

    ts = [threading.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True),
          threading.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True),
          threading.Thread(target=feed, daemon=True)]
    for t in ts:
        t.start()
    timed_out = False
    terminated = True
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminated = _kill_tree(proc)
    if killed_for_output["v"] and proc.poll() is None:
        terminated = _kill_tree(proc)
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        try:
            stream.close()                # 解除 reader 线程的阻塞读
        except Exception:
            pass
    for t in ts:
        t.join(timeout=0.4)
    dur = round((time.monotonic() - started) * 1000)
    return {"exit_code": proc.returncode, "timed_out": timed_out, "duration_ms": dur,
            "stdout": caps["stdout"].decode("utf-8", "replace"),
            "stderr": caps["stderr"].decode("utf-8", "replace"),
            "stdout_truncated": trunc["stdout"], "stderr_truncated": trunc["stderr"],
            "killed_for_output_limit": killed_for_output["v"], "process_terminated": terminated}


# --------------------------------------------------------------------------- 静态审查
C_STATIC_RULES = [
    ("assign_in_condition", r"\bif\s*\(\s*[A-Za-z_]\w*\s*=(?!=)", "high", "if 里用了单个 =（赋值），应为 =="),
    ("scanf_missing_amp", r"\bscanf\s*\(\s*\"[^\"]*%[dific][^\"]*\"\s*,\s*(?!&)[A-Za-z_]\w*\s*\)", "high",
     "scanf 变量前缺少 &"),
    ("void_main", r"\bvoid\s+main\s*\(", "medium", "void main 非标准，应用 int main(void)"),
    ("gets_used", r"\bgets\s*\(", "high", "gets 已废弃且会溢出，改用 fgets"),
    ("array_oob_risk", r"\[\s*-?\d+\s*\]\s*=", "low", "对固定下标赋值，注意是否越界"),
]


def static_review(code: str, lang: str) -> dict:
    issues = []
    lang = (lang or "").lower()
    if lang in {"python", "py"}:
        try:
            compile(code, "<submission>", "exec")
        except SyntaxError as e:
            issues.append({"rule": "syntax_error", "severity": "blocker", "line": e.lineno,
                           "col": e.offset, "detail": f"{e.msg}", "snippet": (e.text or "").strip()[:160]})
        except Exception as e:
            issues.append({"rule": "compile_error", "severity": "blocker", "detail": str(e)})
    elif lang in {"c", "cpp"}:
        if code.count("{") != code.count("}"):
            issues.append({"rule": "unbalanced_braces", "severity": "blocker",
                           "detail": f'大括号不配对：{{={code.count("{")} }}={code.count("}")}'})
        if code.count("(") != code.count(")"):
            issues.append({"rule": "unbalanced_parens", "severity": "high", "detail": "圆括号不配对"})
        if "printf" in code and "#include <stdio.h>" not in code:
            issues.append({"rule": "missing_stdio", "severity": "high", "detail": "用了 printf 但没 #include <stdio.h>"})
        if "malloc" in code and "#include <stdlib.h>" not in code:
            issues.append({"rule": "missing_stdlib", "severity": "medium", "detail": "用了 malloc 但没 #include <stdlib.h>"})
        if not re.search(r"\bint\s+main\s*\(", code):
            issues.append({"rule": "no_main", "severity": "blocker", "detail": "没有 int main(...)"})
        for name, pat, sev, why in C_STATIC_RULES:
            for m in re.finditer(pat, code, re.M):
                issues.append({"rule": name, "severity": sev, "detail": why,
                               "line": code[:m.start()].count("\n") + 1, "snippet": m.group(0)[:120]})
    else:
        issues.append({"rule": "no_static_rules", "severity": "info",
                       "detail": f"暂无 {lang} 的静态规则，仅做人工审查"})
    return {"performed": True, "lang": lang, "issues": issues,
            "issue_count": len(issues),
            "blockers": [i for i in issues if i["severity"] == "blocker"]}


# --------------------------------------------------------------------------- 比较
def _norm_out(s: str) -> list[str]:
    """判题用的归一化：统一 CRLF、去掉每行尾随空白、去掉结尾空行。

    Windows 上 C 程序写 \n 到管道会变成 \r\n，逐行比较前必须归一化，
    否则多行输出的题目会莫名其妙判错（真实踩过的坑）。
    """
    lines = [ln.rstrip() for ln in (s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def compare(expected: str, actual: str, mode: str = "trim") -> bool:
    if mode == "exact":
        return expected == actual
    if mode == "tokens":
        return expected.split() == actual.split()
    if mode == "contains":
        return expected.strip() in actual
    if mode == "regex":
        return re.search(expected, actual) is not None
    return _norm_out(expected) == _norm_out(actual)


# --------------------------------------------------------------------------- 执行
def run_tests(code: str, lang: str, tests: list[dict], *, timeout: float = DEFAULT_TIMEOUT,
              max_output: int = DEFAULT_MAX_OUTPUT, workroot: str | None = None,
              compile_timeout: float = 30.0) -> dict:
    lang = (lang or "").lower()
    lang = "python" if lang == "py" else lang
    tc = detect_toolchains()
    tool = tc.get(lang)
    result = {
        "ran": False, "mode": "static_review", "lang": lang,
        "statement": "本次未运行，仅静态审查",
        "reason": None, "toolchain": None, "compile": None, "cases": [],
        "passed": 0, "failed": 0, "total": len(tests),
        "isolation": {"tmpdir": True, "cleaned": False, "env_inherited": False,
                      "env_allowlist": [], "network_blocked": False, "network_method": None,
                      "timeout_s": timeout, "max_output_bytes": max_output},
        "warnings": [], "static_review": static_review(code, lang),
    }
    if not tool:
        result["reason"] = f"未检测到 {lang} 的工具链，已降级为静态审查"
        result["warnings"].append(result["reason"])
        return result
    if not tests:
        result["reason"] = "没有测试用例（仅静态审查）"
        result["warnings"].append(result["reason"])
        return result

    tmp = Path(tempfile.mkdtemp(prefix="plc-sbx-", dir=workroot)) if workroot else Path(tempfile.mkdtemp(prefix="plc-sbx-"))
    net_guard = None if os.name == "nt" else _posix_net_guard()
    try:
        src_name = "submission" + EXT.get(lang, ".txt")
        src = tmp / src_name
        src.write_text(code, encoding="utf-8", newline="\n")
        extra_paths = [str(Path(tool["exe"]).parent)]
        env, allow = build_env(tmp, extra_paths)
        result["isolation"]["env_allowlist"] = allow
        result["isolation"]["network_method"] = (f"{net_guard}+proxy-blackhole" if net_guard else "proxy-blackhole-only")
        result["isolation"]["network_blocked"] = bool(net_guard)
        if not net_guard:
            result["warnings"].append(
                "网络隔离为 best-effort：仅注入黑洞代理（127.0.0.1:9）。当前平台无法真正断网，"
                "因此 network_blocked=false —— 不做「已断网」的虚假声明。")
        result["toolchain"] = tool

        run_cmd = None
        if lang in {"c", "cpp"}:
            cc = tool["exe"]
            exe_path = tmp / ("program.exe" if os.name == "nt" else "program")
            flags = [cc, "-O0", "-std=c11" if lang == "c" else "-std=c++17", "-Wall", "-Wextra",
                     "-o", str(exe_path), str(src), "-lm"]
            comp = run_capped(flags, cwd=tmp, env=env, timeout=compile_timeout, max_output=max_output)
            result["compile"] = {"cmd": " ".join(Path(f).name if f.endswith(EXT.get(lang, "")) or f == cc else f
                                                 for f in flags),
                                 "exit_code": comp["exit_code"], "stderr": comp["stderr"][:4000],
                                 "warnings": comp["stderr"].strip()[:2000] or None}
            if comp["exit_code"] != 0 or not exe_path.exists():
                result["reason"] = "编译失败，无法运行测试用例（仍完成静态审查）"
                result["warnings"].append(result["reason"])
                return result
            run_cmd = [str(exe_path)]
        elif lang == "python":
            run_cmd = [tool["exe"], str(src)]
        elif lang in {"javascript", "js"}:
            run_cmd = [tool["exe"], str(src)]
        else:
            result["reason"] = f"暂不支持在沙箱中执行 {lang}，已降级为静态审查"
            result["warnings"].append(result["reason"])
            return result

        result["ran"] = True
        result["mode"] = "executed"
        result["statement"] = "已在沙箱中运行全部测试用例"
        for i, t in enumerate(tests, 1):
            cmd = list(run_cmd) + [str(x) for x in (t.get("args") or [])]
            if net_guard == "unshare":
                cmd = ["unshare", "-rn", "--"] + cmd
            elif net_guard == "firejail":
                cmd = ["firejail", "--net=none", "--quiet", "--"] + cmd
            r = run_capped(cmd, cwd=tmp, env=env, stdin_text=t.get("stdin", "") or "",
                           timeout=float(t.get("timeout", timeout)), max_output=max_output)
            mode = t.get("compare", "trim")
            ok = compare(t.get("expected_stdout", ""), r["stdout"], mode) if not r["timed_out"] else False
            if r["exit_code"] not in (0, None) and not r["timed_out"]:
                ok = False
            case = {"name": t.get("name", f"case{i}"), "stdin": t.get("stdin", ""),
                    "args": t.get("args", []), "expected_stdout": t.get("expected_stdout", ""),
                    "actual_stdout": r["stdout"], "stderr": r["stderr"][:2000],
                    "exit_code": r["exit_code"], "timed_out": r["timed_out"],
                    "duration_ms": r["duration_ms"], "compare_mode": mode, "passed": ok,
                    "output_truncated": r["stdout_truncated"] or r["stderr_truncated"],
                    "process_terminated": r.get("process_terminated", True)}
            result["cases"].append(case)
            result["passed" if ok else "failed"] += 1
            if not case["process_terminated"]:
                result["warnings"].append(
                    f'用例 {case["name"]} 超时后进程未能确认终止（当前平台权限限制），已如实标记 '
                    f'process_terminated=false，请手工检查是否有残留进程。')
        result["isolation"]["process_terminated_all"] = all(
            c.get("process_terminated", True) for c in result["cases"])
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        result["isolation"]["cleaned"] = not tmp.exists()
        result["isolation"]["tmpdir_path"] = str(tmp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PLC 沙箱执行")
    ap.add_argument("--lang"); ap.add_argument("--code"); ap.add_argument("--tests")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-output", type=int, default=DEFAULT_MAX_OUTPUT)
    ap.add_argument("--workroot", default=None)
    ap.add_argument("--toolchains", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.toolchains:
        print(json.dumps(detect_toolchains(), ensure_ascii=False, indent=2))
        return 0
    code = Path(a.code).read_text(encoding="utf-8") if a.code else sys.stdin.read()
    tests = json.loads(Path(a.tests).read_text(encoding="utf-8")) if a.tests else []
    tests = tests.get("tests", tests) if isinstance(tests, dict) else tests
    res = run_tests(code, a.lang or "text", tests, timeout=a.timeout,
                    max_output=a.max_output, workroot=a.workroot)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
