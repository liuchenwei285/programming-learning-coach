---
name: programming-learning-coach
description: Run a closed-loop programming study coach from the learner's own notes - parse notes (md/txt/docx/pdf/images), analyze real progress, generate in-scope exercises, wait for the learner's code, grade it in a sandbox, then write an Obsidian practice note, recommend 10 verified open-source code-reading picks, write the reading note, and update the learning profile. 当用户发来编程学习笔记并说「帮我出题」「批改我的代码」「按我的笔记练习」「更新我的学习档案」「推荐代码精读」「整理这次练习到 Obsidian」，或希望以 C/Python 等任意语言进行学习闭环训练时使用。Do not use for one-off code questions with no note-based progress tracking, or for writing production code for the user.
---

# Programming Learning Coach（编程学习教练）

把学习者的笔记变成一次完整练习闭环：**接收笔记 → 分析进度 → 确认范围 → 出题 → 等提交 → 批改 →
练习文档 → 推荐精读 → 精读文档 → 更新档案**。不限编程语言。

## 0. 开工前必须确认（缺一项就先问，不要猜）

| 信息 | 用途 | 缺失时 |
| :-- | :-- | :-- |
| Obsidian Vault 路径 | 写文档 | 从 `config.json` 读取；路径不存在/不可写时才问 |
| 笔记位置（文件/目录） | 解析 | 必须问或让用户直接发文件 |
| 主要语言 | 出题与工具链 | 从笔记自动检测后**复述给用户确认** |
| 是否可联网 | 精读来源验证 | 用 `verify_sources.py` 自动探测；失败即离线降级 |
| 工具链（gcc/python…） | 沙箱运行 | `sandbox_run.py --toolchains`；缺失就静态审查 |

不擅自修改学习者的任何现有配置与笔记；Vault 默认 **dry-run**，加 `--yes` 才写。

## 1. 严格流程（阶段门由代码强制，跳步会被拒绝）

| # | 阶段 | 做什么 | 命令 |
| :-- | :-- | :-- | :-- |
| 1 | `intake` | 解析笔记（按文件哈希缓存，笔记没变就跳过） | `plc_cli.py prepare --notes <路径> --sid <sid> --records work/records.json --cache-dir .plc/cache` |
| 2 | `analysis` | 进度分析：T1~T3 直接可用、T4 歧义项合并成一张表 | 同上（`prepare` 一次跑完 1+2） |
| 3 | `scope_confirmed` | `prepare` 自动锁定范围：T1~T3 可用、T4 按未学排除；无需人工确认 | `plc_cli.py prepare …`（`scope-confirm` 仅为兼容旧流程保留） |
| 4 | `questioning` | 出题（答案自动进 `.private/`） | `plc_cli.py questions build --questions work/q.json --records … --sid <sid>` |
| 5 | `awaiting_submission` | **停下来等提交**（逐题 / 一次性 / 申请提示） | 逐题 `plc_cli.py submit --sid <sid> --qid Q1 --file 答案.c`；一次性 `plc_cli.py submit-dir --sid <sid> --dir 我的答案/`（文件名即题号，如 `Q1.c`）；提示 `plc_cli.py hint --sid <sid> --qid Q1 --level L1`（逐级，不能跳级，永不给完整答案） |
| 6 | `grading` | 批改（沙箱跑用例 + 九段结构） | `plc_cli.py grade --sid <sid> --records … --notes work/notes.json --out work/grading.json` |
| 7 | `practice_doc` | 生成练习文档并写入 Vault | `plc_cli.py render practice … --out work/X月X日练习.md` → `plc_cli.py vault note --kind practice --yes` |
| 8 | `recommend` | 精读 10 篇（默认自编程序，**逐篇编译运行**） | `plc_cli.py reading build --items work/reading.json --records work/records.json --out work/reading-verified.json`；已学指针/结构体后改开源模式 `plc_cli.py verify --recs … --out …` |
| 9 | `reading_doc` | 生成精读文档并写入 Vault | `plc_cli.py render reading --items work/reading-verified.json --meta … --records … --out X月X日代码精读.md`（开源模式用 `render reading-opensource`）→ `plc_cli.py vault note --kind reading --yes` |
| 10 | `archive_updated` | 更新学习档案（备份 + 保留历史） | `plc_cli.py vault archive --name 学习进度.md --yes`（及 `知识点掌握记录.md`） |

会话状态与阶段：`plc_cli.py status --sid <sid>`、`plc_cli.py next --sid <sid>`。
会话数据放在工作目录的 `.plc/.private/<session_id>/`（**不要**放进 Vault）。

## 2. 硬规则（不可违反；每条都有代码强制）

**流程**
1. 严格按上表顺序推进；`session_store.advance()` 拒绝跳步与回退。
2. **不能跳过"提交答案"**：没有提交记录时进入 `grading` 会直接报错。
3. **不能提前展示答案**：标准答案只存在 `.private/<sid>/answers.private.json`；
   `read_private()` 在批改前的任何阶段调用都会抛错；公开题目写入前剥离私有字段并做泄漏扫描。
4. 缺信息就问，不擅自假设学习者的进度。

**掌握度**（详见 `references/mastery-rules.md`）
5. 笔记里出现过 ≠ 已掌握；仅凭笔记状态上限是「待检验」。
6. 五级：未学习 / 学习中 / 待检验 / 基本掌握 / 熟练掌握。
7. 一次答对最多升到「待检验」；≥2 道不同题答对 → 基本掌握；
   ≥3 道、跨 ≥2 次练习、含 1 次组合题 → 熟练掌握；答错降一级；未提交不产生事件。
8. 冷启动只标「学习中/待检验」，首练难度保守，并明确告知"这是初始估计"。

**出题**（详见 `references/question-rules.md`）
9. 严格不超纲：`required_knowledge_points` / `knowledge_points` 里出现「未学习」→ 报错。
10. **题量固定 10 道，不多不少**：`questions.check_paper` 与 `build` 都硬校验 `len == 10`，
    不足或超出直接报错。可用知识点太少时**不得减题量、不得超纲**，而是换角度（不同场景、不同边界、
    不同输入输出方式、不同组合方式），并在出题前先告诉学习者「可用知识点偏少，10 题会有角度上的重叠」
    （`paper.scope_scarce` + `must_tell_learner` 给出这句话）。
11. **题型限定 7 种**：输出预测题 / 程序追踪题 / 改错题 / 代码分析题 / 概念辨析题 / 编程题 / 综合挑战题。
    题型决定作答形式（文本 or 代码）、必填字段与配比；配比超上限 → 报错，低于下限 → 警告。
12. **难度从低到高**：第 1 题最易，最后 1 题必须是综合挑战题（与范例《10月18日练习》一致）。
    需要「难度从高到低」时加 `--order desc`（此时综合挑战题排第 1）。难题靠知识点组合与逻辑设计。
13. 10 题里必须混入读程序类题（输出预测/追踪）：整卷都是编程题会警告。
14. 尽量覆盖已学知识点（一题可考多个）；覆盖不到的必须列出并说明原因。
15. 优先安排历史错题的薄弱点：有薄弱点时，覆盖薄弱点的题占比 <40% 给警告、一道都没安排直接报错。
    新题**不得与历史题目机械重复**：题干+待读代码+输入输出规格做字符 3-gram 相似度查重，≥0.80 报错。
16. 每题必含：编号、标题、题型、难度、预计时间、知识点、题干、出题角度 `angle`；编程题另需输入/输出/示例/测试用例，
    读程序题另需 `code_to_read`（代码内带中文注释），综合挑战题另需自检三问。
17. 每题必须有 `related_notes`（指向精读篇目或已有笔记），文档里渲染成 `[[笔记|别名]]（说明）`。
18. 出题后停止等待提交。提示三级 L1/L2/L3，只在申请时逐级给，**永远不给完整答案**；
    `plc_cli.py hint` 会拒绝跳级申请与重复申请，并把每次提示记进会话 `hint_log`。

**批改**（详见 `references/grading-template.md`）
18. 每题九段：我的答案 / 正确性 / 错误位置 / 错误原因 / 知识点 / 修改建议 / 标准答案 / 逐步讲解 / 更好写法。
19. **两条判定路径**：代码作答 → 沙箱编译运行测试用例；文本作答（输出预测/追踪/分析/辨析）→
    与期望答案**逐行对照**（Tab 与空格等价，大小写与数字差异算错），自动给出行级错误定位与得分。
20. 保留学习者原有思路，不用超纲写法替换；「更好写法」涉及未学知识点时自动移除并说明。
21. 未提交的题标「未完成」，不编造答案与成绩。
22. 能跑就在沙箱跑；跑不了必须写 **"本次未运行，仅静态审查"**，绝不假装运行过。
23. 沙箱：临时目录、超时、输出上限、禁网（best-effort 时如实标 `network_blocked:false`）、
    不继承环境变量、用后清理。
24. 整体评价六维：知识理解 / 代码编写 / 逻辑思维 / 知识点掌握 / 薄弱环节 / 训练建议。

**笔记解析**（详见 `references/note-parsing.md`）
25. md/txt/docx 用标准库；pdf 与 OCR 为可选依赖，缺失时降级并提示；图片由模型直接阅读。
26. **范围自动锁定，不询问**（证据分级 T1~T5，见 `references/note-parsing.md`）：
    T1 强别名命中 1~4 级小标题 / T2 真实代码示例 / T3 正文提及≥3次 → **直接可出题、不询问**；
    T4 只有泛别名命中、或命中全在预告句/目录列举/排版笔记里、或正文提及<3次 → **按未学排除**，新出现时只给一行 `以下按未学排除：……`；学习者明确说「改为已学」后用 `scope-promote` 在下一轮纳入；
    T5 完全没出现 → 未学习，禁止作为解题必要条件。
27. 已确认范围写进学习档案；**笔记指纹未变直接沿用不再问**；弱证据项只在首次出现时提示一次。
28. 输出分桶（可直接出题 / 需确认 / 已掌握 / 未完全掌握 / 正在学 / 未学）+ 代码示例与典型错误 + 涉及语言，**每项注明依据与证据等级**。

**精读推荐**（详见 `references/code-reading-rules.md`）
29. **模式 A（默认）自编程序 10 篇**：指针/结构体等还是「未学习」时必须用这个模式。
    每篇 = 一个短小完整可编译的程序 + 为什么读（引用你的具体错误）+ 输入 + 先预测 3 问 +
    实际输出 + 读代码盯住几点 + 相关笔记；预习篇放最后并标 `preview_uses`。
30. **模式 A 的「实际输出」必须来自真实运行**：`reading.py` 用 gcc 逐篇编译运行后回填；
    编译失败/超时/没有工具链 → 输出留空并写「未通过验证：原因…不编造运行结果」。
    只有全部通过时文档才能写「所有程序都已编译运行验证，没有警告」。
31. 模式 A 结尾必须有「暂时先不要读的」：按你未学的知识点自动说明为什么现在不读大型开源项目，并给过渡路径。
32. **模式 B（进阶）真实开源片段 10 份**：学完指针/结构体后使用。难度递增、优先补薄弱点、
    选片段不选整项目、必含 12 项字段；联网必须验证仓库存在 + 文件路径存在 + 许可证，标「已验证/未验证」；
    不能联网只给项目名并标「待验证」，**不给链接、不编造路径与作者**；不复制第三方整份代码。

**Obsidian 写入**（详见 `references/vault-rules.md`）
33. 从 `config.json` 读取 Vault 路径；**默认写进 Vault 根目录**（与学习者已有笔记放在一起）；
    加 `--in-subdirs` 才用 `Programming Learning Coach/{练习记录,代码精读,学习档案}`，且优先复用已有同名目录。
34. 文件名 `X月X日练习.md` / `X月X日代码精读.md`；年份写进 frontmatter 的 `date`；
    重名自动 `(2)`，**绝不覆盖**。frontmatter 的列表用块状写法（Obsidian 风格）。
35. 学习档案只追加或带备份更新，历史记录永久保留；不删除任何文件。
36. 练习文档与知识点之间用 `[[知识点]]` 双向链接。
37. **练习文档结构固定**（对齐范例）：标题 + 配套笔记 + 本次情况 + 练习前进度
    → 每题「第 N 题｜主题 / 题型难度知识点 / ### 原题 / ### 我的答案 / ### 标准答案 /
    ### 注意事项及易错点 / **相关笔记：**」→ `# 总结`（一、知识点速查表；二、最需要改进的 N 点；
    三、做得好的地方；四、未掌握知识点与掌握度变化；五、下一步建议）。
    **不得遗漏原题（含待读代码）与学习者的答案**（`render_md.assert_complete()` 会拦截）。

## 3. 目录

```
programming-learning-coach/
├── SKILL.md                        # 本文件：流程 + 35 条硬规则
├── references/                     # 出题(含题型表)/批改/掌握度/提示/解析/精读/Vault 规则 + 知识点词库
├── scripts/
│   ├── plc_cli.py                  # 统一入口
│   ├── session_store.py            # 阶段门 + 答案隔离        ├── questions.py  # 题型/组卷/不超纲/查重/泄漏扫描
│   ├── parse_notes.py  glossary.py  analyze.py  mastery.py    # 解析 → 词库 → 进度分析 → 五级掌握度
│   ├── sandbox_run.py  grading.py  reading.py  verify_sources.py  # 沙箱 / 批改 / 精读A(自编) / 精读B(开源)
│   └── render_md.py  vault_write.py                            # 文档生成 / Vault 安全写入
├── templates/                      # practice.md、code_reading.md（自编）、code_reading_opensource.md（开源）
└── tests/                          # run_tests.py + 12 组测试 + fixtures/（可直接跑的示例题库与精读）
```

`tests/fixtures/` 里的 `questions.example.json`（10 题完整卷）、`reading.example.json`（10 篇精读）、
`notes.example.json`（教练评语）、`practice-meta.example.json`（总结各节内容）是**可直接照抄的格式样例**，
并且被 `test_11_examples.py` 持续校验：参考程序必须通过自己的测试用例、期望答案必须等于 gcc 实跑输出、
10 篇精读必须全部编译运行无警告 —— 示例一旦腐烂测试就会失败。

## 4. 依赖

- Python ≥ 3.9，**仅标准库**（zipfile/xml/urllib/subprocess/tempfile/threading/re/json）。
- 可选：`pypdf`（PDF）、`gcc`/`g++`（C/C++ 沙箱运行）。缺失即降级，不报错中断。
- 可选：环境变量 `GITHUB_TOKEN` 可把精读来源验证的 API 配额从 60 次/小时提到 5000 次/小时；
  没有 token 时若触发限流，对应条目会如实标「未验证（限流）」而不是编造。
- **每阶段只读该阶段需要的 references**：`plc_cli.py refs --stage <阶段>` 给出清单
  （出题只读 question-rules + hint-levels，批改才读 grading-template），不要启动就全部读进来。
- 运行测试：`python tests/run_tests.py`（会跳过需要联网/工具链的用例并说明原因）。
