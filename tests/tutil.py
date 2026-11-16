#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试公共工具：临时目录、fixture 生成（含用标准库现做的 docx）、样例题目构造器。"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TESTS_DIR.parent
SCRIPTS = SKILL_ROOT / "scripts"
TEMPLATES = SKILL_ROOT / "templates"
sys.path.insert(0, str(SCRIPTS))

import mastery as M            # noqa: E402
import session_store as S      # noqa: E402
import sandbox_run as SBX      # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PDF_MIN = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"

DOCX_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""
DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""


def make_docx(path: Path, paragraphs: list[tuple[str, str]]) -> Path:
    """paragraphs: [(style, text)]，style 为 '' 或 'Heading1'。用标准库 zipfile 现做真 docx。"""
    body = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>' + "".join(body) + '</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", DOCX_CT)
        z.writestr("_rels/.rels", DOCX_RELS)
        z.writestr("word/document.xml", doc)
    return path


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def tmp(prefix: str = "plc-test-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def rm(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def has_toolchain(lang: str) -> bool:
    return bool(SBX.detect_toolchains().get(lang))


# --------------------------------------------------------------------- 样例笔记
NOTES_MD = """# C语言基础

## 数据类型
C语言的数据类型包括 int、char、float、double。用 sizeof 可以查看占用字节数。

```c
#include <stdio.h>
int main(void) {
    printf("%zu\\n", sizeof(int));
    return 0;
}
```

## 顺序结构与输出
printf 是格式化输出函数，%d 输出整数，%c 输出字符。
注意：scanf 需要取地址符 &，这是易错点。

```c
int a = 10;
printf("%d\\n", a);
```
"""

NOTES_TXT = """循环结构笔记
for 循环用来重复执行一段代码，while 循环在条件为真时重复。
循环里可以用 break 跳出、continue 跳过本次。
嵌套循环要注意内外层次。
"""


def fixture_notes(dirpath: Path) -> list[Path]:
    """md + txt + docx + pdf + png + 未知格式，覆盖降级路径。"""
    md = write(dirpath / "C语言.md", NOTES_MD)
    txt = write(dirpath / "循环.txt", NOTES_TXT)
    docx = make_docx(dirpath / "函数.docx", [
        ("Heading1", "函数"),
        ("", "函数由函数名、形参、返回值组成，用 return 返回结果。"),
        ("", "递归需要出口，例如阶乘函数。"),
        ("Heading2", "常见错误"),
        ("", "形参和实参顺序写反是易错点。"),
    ])
    pdf = dirpath / "讲义.pdf"
    pdf.write_bytes(PDF_MIN)
    png = dirpath / "板书.png"
    png.write_bytes(PNG_1PX)
    unk = write(dirpath / "notes.xyz", "int main(){ return 0; }")
    return [md, txt, docx, pdf, png, unk]


def make_records(states: dict[str, str], *, lang: str = "c", estimated: bool = False) -> dict:
    """states: {知识点: 状态名}。"""
    data = {"version": 1, "cold_start": True, "records": {}}
    for kp, st in states.items():
        idx = M.IDX[st]
        rec = M.blank(kp, lang)
        rec.update({"state": st, "state_index": idx, "estimated": estimated,
                    "note_evidence": [f"笔记:fixture.md:{idx+1} 测试数据"]})
        if idx >= 1:
            rec["note_basis"] = "测试构造"
        data["records"][kp] = rec
    data["cold_start"] = all(r["state_index"] <= M.IDX["待检验"] and not r["practice_evidence"]
                             for r in data["records"].values())
    return data


# --------------------------------------------------------------------- 样例题目
SCENARIOS = [
    ("秒数换算", "读入一个总秒数 t（0<=t<=1000000），把它换算成「X小时Y分Z秒」输出。"),
    ("三位数拆分", "读入一个三位正整数 n，输出它的百位、十位、个位数字以及三个数字之和。"),
    ("阶梯水费", "读入本月用水量 w（整数吨），按 0~10 吨每吨 2 元、10~20 吨每吨 3 元、"
                 "超过 20 吨每吨 5 元计费，输出应缴水费，保留两位小数。"),
    ("字符信息分析", "读入一个非空白字符，输出它本身、ASCII 码、大小写转换结果和字符类别。"),
    ("摄氏转华氏", "读入摄氏温度 c，按 F = c / 5.0 * 9 + 32 输出华氏温度，保留两位小数。"),
    ("奇偶与符号", "读入一个整数 n，输出它是奇数还是偶数，以及是正数、负数还是零。"),
    ("最大公约数", "读入两个正整数 a 和 b，用辗转相减法求最大公约数并输出。"),
    ("成绩评级", "读入一个 0~100 的整数成绩，按 90/80/70/60 分界输出 A/B/C/D/E 等级。"),
    ("数位计数", "读入一个正整数 n，输出它是几位数以及各位数字之和。"),
    ("时刻差", "读入两个用秒表示的时刻，输出它们相差多少秒、多少分、多少小时。"),
]
TEXT_STEMS = [
    "阅读下面的程序，不运行代码，写出每一行的输出结果。",
    "分析程序的输出，注意空格、换行与字符的区别，逐行写出结果。",
    "写出程序运行后的全部输出，并列出每次输出时各个变量的值。",
    "阅读代码，判断哪些语句合法、哪些会导致编译错误，并说明原因。",
    "下面的程序想完成指定任务但有多处问题，找出全部问题并给出修正后的完整程序。",
]


def _idx(qid: str) -> int:
    digits = "".join(ch for ch in str(qid) if ch.isdigit())
    return (int(digits) - 1) % 10 if digits else abs(hash(qid)) % 10


DEF_SOLUTION = '#include <stdio.h>\nint main(void){int n;scanf("%d",&n);printf("%d\\n",n*2);return 0;}'
DEF_CODE_TO_READ = ('#include <stdio.h>\n\nint main(void)\n{\n    int a = 7;          // 整型\n'
                    '    double b = 2.5;     // 双精度浮点型\n    char c = \'A\';       // \'A\' 的 ASCII 是 65\n\n'
                    '    printf("%d %.1f %c\\n", a, b, c);   // %d 整数 / %.1f 一位小数 / %c 字符\n'
                    '    printf("%d %d\\n", c, c + 32);      // char 参与运算先提升为 int：65 和 97\n'
                    '    return 0;\n}\n')
DEF_EXPECTED = "7 2.5 A\n65 97"


def make_question(qid: str, difficulty: int, kps: list[str], *, lang: str = "c",
                  required: list[str] | None = None, combined: bool = False,
                  solution: str | None = None, tests: list[dict] | None = None,
                  desc: str | None = None, qtype: str = "programming",
                  related_notes: list | None = None) -> dict:
    """代码作答题型（编程题 / 改错题 / 综合挑战题）的样例构造器。"""
    angle, scenario = SCENARIOS[_idx(qid)]
    stem = desc or f"{scenario}要求：只使用已学过的语法，处理好边界输入与输入非法的情况。"
    q = {
        "id": qid, "title": f"题目{qid}", "question_type": qtype, "language": lang,
        "difficulty": difficulty, "est_minutes": 10 + difficulty * 3, "is_combined": combined,
        "knowledge_points": kps,
        "required_knowledge_points": required if required is not None else kps[:1],
        "stem": stem, "description": stem, "angle": angle,
        "input_format": "一行整数 n（1<=n<=100）",
        "output_format": "一行整数",
        "constraints": ["只能使用已学知识点", "不使用未学过的语法"],
        "examples": [{"input": "3\n", "output": "6\n"}],
        "edge_cases": ["n=1", "n=100"],
        "test_input": "3",
        "related_notes": related_notes if related_notes is not None else
            [{"target": "C语言", "note": "相关语法"},
             {"target": "C语言精读10篇", "alias": "精读第 1 篇", "note": "对应精读篇目"}],
        "hints": {"L1": f"考察 [[{kps[0]}]]", "L2": "先想循环边界", "L3": "读取 n；累加；输出"},
        "reference_solution": DEF_SOLUTION if solution is None else solution,
        "reference_solution_language": lang,
        "tests": tests if tests is not None else [{"name": "样例", "stdin": "3\n", "expected_stdout": "6\n"}],
        "explanation": "1. 读入 n；\n2. 计算 2n；\n3. 输出并换行。",
        "better_approach": "在你现有写法基础上减少一次判断",
        "better_approach_kps": kps[:1],
    }
    if qtype == "comprehensive":
        q["self_check_questions"] = ["整数除法是否影响结果？", "最值初值是否合理？", "格式符与类型是否匹配？"]
        q["self_check_answers"] = ["会，需要浮点除法", "合理，用了首个元素作初值", "匹配"]
    return q


def make_text_question(qid: str, difficulty: int, kps: list[str], *, lang: str = "c",
                       qtype: str = "output_prediction", code: str | None = None,
                       expected: str | None = None, stem: str | None = None,
                       requirements: list | None = None) -> dict:
    """文本作答题型（输出预测 / 追踪 / 代码分析 / 概念辨析）的样例构造器。"""
    return {
        "id": qid, "title": f"读程序{qid}·{SCENARIOS[_idx(qid)][0]}", "question_type": qtype,
        "language": lang, "angle": f"读程序·{SCENARIOS[_idx(qid)][0]}",
        "difficulty": difficulty, "est_minutes": 8 + difficulty * 2, "is_combined": False,
        "knowledge_points": kps, "required_knowledge_points": kps[:1],
        "stem": stem or TEXT_STEMS[_idx(qid) % len(TEXT_STEMS)],
        "requirements": requirements if requirements is not None else ["解释每一行的结果是怎么算出来的"],
        "code_to_read": (DEF_CODE_TO_READ.replace("int a = 7;", f"int a = {7 + _idx(qid)};")
                         .replace("double b = 2.5;", f"double b = {2.5 + _idx(qid)};")
                         if code is None else code),
        "related_notes": [{"target": "C语言", "note": "数据类型与 printf"}],
        "hints": {"L1": f"考察 [[{kps[0]}]]", "L2": "先看格式符，再看实参类型",
                  "L3": "逐行：格式符 → 实参类型 → 值"},
        "answer_kind": "text",
        "expected_answer": DEF_EXPECTED if expected is None else expected,
        "answer_points": ["char 参与运算先提升为 int"],
        "explanation": "1. 第 1 行：%d 输出 7，%.1f 输出 2.5，%c 输出 A。\n2. 第 2 行：'A' 是 65，+32 得 97。",
    }


def new_session(root: Path, sid: str = "t1", *, to_phase: str = "scope_confirmed",
                language: str = "c") -> dict:
    S.create(root, sid, language)
    S.set_field(root, sid, scope_confirmed=True)
    order = ["analysis", "scope_confirmed", "questioning", "awaiting_submission", "grading"]
    for ph in order:
        S.advance(root, sid, ph)
        if ph == to_phase:
            break
    return S.load_state(root, sid)


# --------------------------------------------------------------------- 样例作答
CORRECT_C = """#include <stdio.h>
int main(void) {
    int n;
    if (scanf("%d", &n) != 1) return 1;
    printf("%d\\n", n * 2);
    return 0;
}
"""

WRONG_C = """#include <stdio.h>
int main(void) {
    int n;
    scanf("%d", n);
    printf("%d\\n", n * 3)
    return 0;
}
"""

SPIN_PY = "while True:\n    pass\n"
BIG_PY = "print('A' * 5_000_000)\n"
ENVSPY_PY = ("import os, json\n"
             "print(json.dumps({'secret': os.environ.get('PLC_SECRET_MARKER'),\n"
             "                  'keys': sorted(os.environ.keys()),\n"
             "                  'proxy': os.environ.get('http_proxy')}))\n")
NET_PY = ("import json, socket\n"
          "ok = True\n"
          "try:\n"
          "    socket.create_connection(('1.1.1.1', 80), 3).close()\n"
          "except Exception as e:\n"
          "    ok = False\n"
          "print(json.dumps({'connected': ok}))\n")
