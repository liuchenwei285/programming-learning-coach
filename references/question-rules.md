# 出题规则（question-rules）

> 对应 `scripts/questions.py`。结构与题型对齐范例《10月18日练习》。违规项由代码强制拦截（非零退出）。

## 〇、题量：固定 10 道（硬规则）

- 每次出题**固定 10 道，不多不少**。`questions.check_paper()` 与 `build()` 都以 `len(qs) == 10` 为硬校验，
  不足或超出直接报错；`build` 报错时什么都不写。
- 可用知识点少于 10 个时：**不得减题量、不得降低标准、不得偷偷超纲**，改为换角度出题
  （不同场景 / 不同边界条件 / 不同输入输出方式 / 不同知识点组合），并**在出题前先告诉学习者**
  「可用知识点偏少，10 题会有角度上的重叠」（`paper.scope_scarce`、`must_tell_learner`）。
- 同一「知识点组合 + 出题角度」重复 ≥3 次会给角度重叠警告；每题必须写 `angle`（一句场景说明）。

## 一、题型系统（7 种）

| 题型 id | 中文 | 作答 | 沙箱判定 | 必填（公开） | 必填（私有） | 10题配比 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| `output_prediction` | 输出预测题 | 文本 | — | `code_to_read` | `expected_answer` | 1~3 |
| `trace` | 程序追踪题 | 文本 | — | `code_to_read` | `expected_answer` | 0~2 |
| `fix_bug` | 改错题 | 代码 | ✅ | `code_to_read` | `reference_solution` `tests` | 0~2 |
| `code_analysis` | 代码分析题 | 文本 | — | `code_to_read` `requirements` | `expected_answer` | 0~2 |
| `concept` | 概念辨析题 | 文本 | — | `requirements` | `expected_answer` | 0~2 |
| `programming` | 编程题 | 代码 | ✅ | `input_format` `output_format` `examples` | `reference_solution` `tests` | 2~5 |
| `comprehensive` | 综合挑战题 | 代码 | ✅ | 同编程题 + `self_check_questions` | `reference_solution` `tests` | 1（放最后） |

- **文本作答**（输出预测/追踪/分析/辨析）：批改时与 `expected_answer` **逐行对照**（空白折叠，Tab 与空格等价），
  自动得出「第几行不一致」；错误原因与修改建议仍由教练写。
- **代码作答**（改错/编程/综合）：在沙箱里编译并运行 `tests`。
- 配比超出上限 = **错误**；低于下限 = 警告。整卷都是编程题会警告（应混入读程序题，最能暴露概念误解）。

## 二、每题的文档结构（模板已固定，不要自创）

```
## 第 N 题｜<主题>

**题型：** 输出预测题　**难度：** 2/5　**预计：** 10 分钟　**知识点：** [[格式占位符]]、[[数据类型]]

### 原题
<题干：阅读下面的程序，不运行代码，写出每一行的输出结果。>
<待读代码（读程序题必填，代码里带中文注释逐行解释）>
<编号小问 1. 2. 3. / **要求：** 列表 / **输入：** **输出：** / **示例：** / **测试输入：** / **提示：** / **挑战：** / **自检三问**>

### 我的答案
### 标准答案        （代码块 ```text 或 ```c + 编号逐条讲解）
### 注意事项及易错点 （第一条必须是「你这道题错在哪里」，其余是这类题通用的坑）
**相关笔记：** [[C语言精读10篇|精读第 1 篇]]（整数除法与类型转换）、[[C语言]]（数据类型、printf）
```

## 三、硬性规则

1. **严格不超纲**：`knowledge_points` 与 `required_knowledge_points` 里出现「未学习」→ 报错。
   难题的难度来自**知识点组合与逻辑设计**，不来自超纲语法。
2. **难度从低到高**（默认 `--order asc`）：第 1 题最易，最后 1 题是 `comprehensive` 综合挑战。
   如确实要「难度从高到低」，加 `--order desc`（此时第 1 题最难、综合挑战题排第 1）。
3. **必填公共字段**：`id` `title` `question_type` `difficulty(1..5)` `est_minutes`
   `knowledge_points` `required_knowledge_points` `stem`（题干）。
4. **覆盖已学知识点**：未覆盖的必须列原因（`--uncovered-reasons`），否则自动占位并给警告。
5. **优先安排薄弱点**：`weak_point_plan()` 检查；未覆盖给警告并要在文档里说明。
6. **不重复原题**：按「题干 + 待读代码 + 输入输出规格」做指纹查重（不看标题），重复 → 报错。
7. **答案隔离**：`reference_solution` `expected_answer` `tests` `explanation` `better_approach`
   `answer_points` `self_check_answers` 全部写入 `.private/<sid>/answers.private.json`；
   公开文件写盘前先做泄漏扫描，命中就**什么都不写**并报错。
8. **每题都要有 `related_notes`**，指向精读篇目或已有笔记（缺失给警告）。
10. **出题后停止**：阶段推进到 `awaiting_submission`，等提交。

## 四、题目 JSON 模板

```json
{
  "id": "Q1", "title": "数据类型与格式化输出", "question_type": "output_prediction",
  "language": "c", "difficulty": 2, "est_minutes": 10, "is_combined": false,
  "knowledge_points": ["格式占位符", "数据类型", "整数除法与取余"],
  "required_knowledge_points": ["格式占位符", "整数除法与取余"],
  "stem": "阅读下面的程序，不运行代码，写出每一行的输出结果。",
  "requirements": ["解释为什么第三行的两个计算结果不同",
                   "解释为什么字符串 \"ABC\" 的长度与 sizeof(\"ABC\") 不同"],
  "code_to_read": "#include <stdio.h>\n\nint main(void)\n{\n    int a = 7;   // 整型\n    ...\n}",
  "related_notes": [{"target": "C语言精读10篇", "alias": "精读第 1 篇", "note": "整数除法与类型转换"},
                    {"target": "C语言", "note": "数据类型、printf"}],
  "hints": {"L1": "考察 [[整数除法与取余]] 与 [[格式占位符]]", "L2": "先分清每条 printf 的参数类型",
            "L3": "逐行：先看格式符 → 再看实参类型 → 再算值"},
  "answer_kind": "text",
  "expected_answer": "7 2.5 A\n65 97\n3 3.5\n1 4",
  "answer_points": ["第 3 行：a/2 是整数除法得 3；a/2.0 有 double 参与得 3.5",
                    "sizeof(\"ABC\") 含 '\\0' 共 4 字节，strlen 才是 3"],
  "explanation": "1. 第 1 行：%d 输出整数 7…\n2. 第 2 行：char 参与运算先提升为 int…",
  "better_approach": null
}
```

编程题把 `answer_kind` 换成 `"code"`，并提供 `input_format` `output_format` `examples`
`tests` `reference_solution`；综合挑战题再加 `self_check_questions`（自检三问）与私有
`self_check_answers`。

## 五、难度设计参考（不超纲的前提下提高难度）

| 难度 | 设计手法 | 常见题型 |
| :-- | :-- | :-- |
| 1 | 单一知识点直接套用 | 输出预测 |
| 2 | 单知识点 + 一处边界/一处辨析 | 输出预测、概念辨析 |
| 3 | 2 个知识点组合，含明显陷阱（整除、`=`/`==`、`&`、大小写） | 程序追踪、改错 |
| 4 | 2-3 个知识点组合，要求解释原因或给完整程序 | 代码分析、编程 |
| 5 | 3+ 知识点组合、需要发现隐藏不变量、边界需推理、多功能清单 + 自检三问 | 综合挑战 |

## 六、提示三级

见 `hint-levels.md`。`hints` 只能是 L1/L2/L3；L3 只给伪代码，过长且形似完整代码会被拦下。
`plc_cli.py hint` 拒绝跳级与重复申请，并把每次提示写进会话 `hint_log`。
