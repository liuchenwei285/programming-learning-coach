# Programming Learning Coach

一个面向 Codex / Agent Skill 的**编程学习闭环教练**：读取学习者自己的笔记，判断真实进度，按已学范围出 10 道题，等提交后在沙箱里批改，生成练习文档、代码精读材料，并安全写回 Obsidian 学习档案。

它适合「按我的笔记练习」「帮我出题」「批改我的代码」「整理这次练习到 Obsidian」「更新我的学习档案」「推荐代码精读」这类持续学习任务；不适合一次性问答，也不用于替学习者写生产代码。

## 安装

把整个 `programming-learning-coach/` 目录放到 Codex skills 目录，然后重启 Codex 让它发现新技能：

```bash
# macOS / Linux
git clone https://github.com/liuchenwei285/programming-learning-coach.git \
  ~/.codex/skills/programming-learning-coach

# Windows PowerShell
git clone https://github.com/liuchenwei285/programming-learning-coach.git \
  "$env:USERPROFILE\.codex\skills\programming-learning-coach"
```

依赖：

- **Python 3.9+**，核心功能只用标准库。
- `pypdf`：解析 PDF 笔记；缺失时 PDF 走降级路径并在输出里明确标注。
- `gcc` / `g++`：C/C++ 答案的沙箱编译运行；缺失时改为静态审查。

装好后编辑 `config.json`，把 `vault` 改成自己的 Obsidian Vault 路径（必须是本机真实存在且可写的目录）：

```json
{
  "vault": "<Vault 路径>",
  "language": "c",
  "question_count": 10,
  "difficulty_order": "asc",
  "scope_policy": "t1_t3_usable_t4_excluded",
  "notes": "Vault 路径永久记住：只在路径不存在或不可写时报错询问。改路径请编辑本文件。"
}
```

Windows 路径写在 JSON 里要把反斜杠转义，例如 `"C:\\Notes\\Vault"`。

可选设置 `GITHUB_TOKEN`，提高精读模式 B 的 GitHub API 配额；未设置也能用，会如实标注验证状态。

## 当前 v3 行为

这份实现以 `scripts/plc_cli.py` 和测试中的实际行为为准，核心口径是 **「问一次、记住、之后直接出题」**：

- Obsidian Vault 路径记在 `config.json`；只有路径不存在、不是目录或不可写时才报错询问。改路径直接编辑配置。
- `prepare` 一次完成建会话、笔记解析、进度分析和范围自动锁定；缓存命中时跳过重复解析与分析。
- T1–T3 强证据知识点自动纳入出题范围；T4 弱证据知识点一律按「未学习」排除，不再弹确认表，也不再要求 `scope-confirm`。
- 新出现的弱证据项只给一行提示：`以下按未学排除：……`。学习者明确说「改为已学」后，用 `scope-promote` 在下一轮纳入。
- 进度摘要最多 5 行，随后直接给 10 道题；题面、参考答案和测试用例一次生成，只调用一次 `questions build`。
- 固定 10 题、难度由低到高、严格不超纲；参考答案用最短可运行实现，编程题配置 3–5 个测试用例。答案在提交前隔离，不出现在公开题面里。
- 提交支持逐题或整目录；L1/L2/L3 提示按级申请，不跳级、不提前给完整答案。
- 批改优先在沙箱编译运行；无法运行时明确写「本次未运行，仅静态审查」，不伪造运行结果。
- 代码精读默认是模式 A：10 篇自编短程序，逐篇编译运行并回填真实输出；进阶模式 B 才验证真实开源片段，联网失败时降级并如实标注「未验证」。
- 写回 Obsidian 默认 dry-run，只有显式 `--yes` 才落盘；重名自动加 `(2)`、档案更新先备份并保留历史，不覆盖、不删除已有文件。
- 每个阶段只读取该阶段需要的 `references`；中途不输出过程旁白，结束时给总耗时与分阶段耗时。

## 十阶段流程

| # | 阶段 | 做什么 |
| --- | --- | --- |
| 1 | intake / prepare | 接收笔记，解析 md/txt/docx/pdf/图片，并生成带缓存的进度分析 |
| 2 | auto scope | 自动锁定 T1–T3，排除 T4；新弱证据项只提示一次 |
| 3 | questioning | 生成固定 10 题，答案与测试用例进入私有答案文件 |
| 4 | submissions / hints | 等学习者提交；按需给 L1–L3 提示 |
| 5 | grade / render | 沙箱批改或静态审查，生成练习文档 |
| 6 | practice doc | 经 dry-run 与显式确认后写入 Vault |
| 7 | reading mode A/B | 自编程序逐篇编译运行，或验证开源片段来源 |
| 8 | reading doc | 生成精读文档并写入 Vault |
| 9 | archive | 备份并更新学习档案，历史永久保留 |
| 10 | completion | 输出总耗时与分阶段耗时 |

阶段门由 `scripts/session_store.py` 强制，跳步与回退会被拒绝；会话私有数据默认位于工作目录 `.plc/.private/<session_id>/`，不要放进 Vault。

## CLI 快速开始

以下命令在技能根目录执行；尖括号占位符按自己的文件替换。`plc_cli.py` 不支持 `--help`，不带参数运行会打印用法说明。

```bash
# 1. 解析笔记、分析进度、自动锁定范围
python scripts/plc_cli.py prepare --notes <笔记路径> --sid s1 --records work/records.json --cache-dir .plc/cache

# 2. 查看当前阶段只需要读哪些规则文件
python scripts/plc_cli.py refs --stage questioning

# 3. 生成 10 道题；答案与测试用例自动隔离
python scripts/plc_cli.py questions build --questions work/q.json --records work/records.json --sid s1

# 4. 提交答案；目录里的文件名按题号命名，例如 Q1.c
python scripts/plc_cli.py submit-dir --sid s1 --dir 我的答案/

# 5. 按需逐级申请提示
python scripts/plc_cli.py hint --sid s1 --qid Q1 --level L1

# 6. 批改并渲染练习文档
python scripts/plc_cli.py grade-render --sid s1 --records work/records.json --notes work/notes.json --meta work/meta.json --out work/练习.md

# 7. 学习者明确要求把弱证据项改为已学时，下一轮生效
python scripts/plc_cli.py scope-promote --records work/records.json --kp <知识点>

# 8. 精读模式 A（默认）或模式 B（真实开源片段验证）
python scripts/plc_cli.py reading build --items work/reading.json --records work/records.json --out work/reading-verified.json
python scripts/plc_cli.py verify --recs work/recs.json --out work/verified.json

# 9. 先 dry-run，再确认写回 Vault
python scripts/vault_write.py plan
python scripts/plc_cli.py vault note --kind practice --date YYYY-MM-DD --content <练习文档> --yes
python scripts/plc_cli.py vault archive --name 学习进度.md --body body.md --yes
```

任何时候可用 `python scripts/plc_cli.py status --sid s1` 查看阶段、提示记录与耗时，用 `python scripts/plc_cli.py next --sid s1` 查看下一步。

## 目录结构

```text
programming-learning-coach/
├── SKILL.md                  # Skill 触发描述与十阶段流程说明
├── config.json               # Vault 路径、语言、题量、难度与范围策略
├── references/               # 出题、批改、掌握度、提示、解析、精读、Vault 规则
├── scripts/
│   ├── plc_cli.py            # 统一入口与十阶段命令
│   ├── session_store.py      # 阶段门、私有答案隔离、提示与耗时记录
│   ├── parse_notes.py        # 笔记解析与哈希缓存
│   ├── analyze.py            # 证据分级与范围自动锁定
│   ├── questions.py          # 固定 10 题、题型、难度、查重与泄漏扫描
│   ├── grading.py            # 九段批改与掌握度更新
│   ├── sandbox_run.py        # 临时目录、超时、输出上限、禁网与清理
│   ├── reading.py            # 模式 A 自编程序与真实编译运行
│   ├── verify_sources.py     # 模式 B 开源片段来源验证
│   ├── render_md.py          # 练习文档与精读文档渲染
│   └── vault_write.py        # dry-run、重名处理、备份与历史保护
├── templates/                # 练习与两种精读文档模板
└── tests/                    # 240 个本地用例与 fixtures
```

## 测试

```bash
cd programming-learning-coach
python tests/run_tests.py
```

【已核实】2026-11-16 在 Windows 上运行结果为：共 240 个用例，通过 235，失败 0，跳过 5。5 个跳过项是联网验证用例，受本机网络限制触发 `WinError 10013`，属于环境限制而非用例失败。在自己的环境重跑时结果可能不同。

## 已知文档债

本仓库的 `SKILL.md` 已与 v3 实现对齐：范围由 `prepare` 自动锁定，弱证据项按未学排除，`scope-confirm` 只为兼容旧流程保留、不是必要步骤。若你从更早的副本迁移过来，请以本 README、`scripts/plc_cli.py` 和 `tests/test_14_v3_flow.py` 为准。

## 安全边界

- 不把私有 Vault 路径、邮箱、Token 或私人笔记写进公开仓库；仓库里的示例使用占位符。
- 公开题目会剥离 `reference_solution`、`expected_answer`、`tests` 等字段并做泄漏扫描。
- 沙箱是 best-effort 隔离：有超时、输出上限、临时目录和不继承环境变量，不等同于完整安全容器；不要运行不可信代码。
- Vault 写入默认不落盘；`--in-subdirs` 才使用子目录，档案更新先备份、旧内容保留在「历史记录」，不删除文件。

## 许可

MIT，见 [LICENSE](LICENSE)。
