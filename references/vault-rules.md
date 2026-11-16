# Obsidian 写入规则（vault-rules）

> 对应 `scripts/vault_write.py`。**默认 dry-run，必须显式 `--yes` 才落盘。**

## 一、写入前必须确认

1. Vault 路径由学习者提供或确认。路径不存在、不是目录、或没有 `.obsidian` → **停下来问**，
   退出码 2，不写入任何位置（`resolve_vault`）。
2. 用 `plan` 子命令先把计划给学习者看：会复用哪些已有目录、会建哪些新目录、目标文件名是什么。
3. 只允许写 Vault 内部：`safe_join()` 会拒绝任何解析后跑到 Vault 外的路径（含 `../`）。

## 二、目录策略

- **默认根目录**：不新建任何目录，文件直接落在 Vault 根。
- `--in-subdirs` 时：**优先复用** Vault 里已有的同名目录（`练习记录` / `代码精读` / `学习档案`，
  深度 ≤3 搜索，跳过 `.obsidian` `.git` `.trash` `.claudian` 等）；没有才新建
  `Programming Learning Coach/{练习记录,代码精读,学习档案}`。
- 任何情况下都不改动、不移动、不重命名 Vault 里已有的目录或文件。

## 二点五、写在 Vault 根目录（默认）还是子目录

- **默认：直接写进 Vault 根目录** —— 学习者现有的《10月18日练习.md》《C语言精读10篇.md》就放在根目录，
  新文档与它们并列，Obsidian 里最容易找到。
- 加 `--in-subdirs`：改写进 `练习记录 / 代码精读 / 学习档案` 子目录（优先复用 Vault 里已有的同名目录，
  没有才建 `Programming Learning Coach/…`）；档案的 `_backup/` 也随之放在档案目录下。
- 两种布局的重名规则、备份规则、拒绝覆盖规则完全一样；都要先经学习者确认，未确认前一律 dry-run。

## 三、文件命名

| 类型 | 文件名 | 示例 |
| :-- | :-- | :-- |
| 练习文档 | `X月X日练习.md` | `11月1日练习.md` |
| 精读文档 | `X月X日代码精读.md` | `11月1日代码精读.md` |

- **年份只写进 YAML frontmatter 的 `date: 2026-11-01`**，文件名里不写年份。
- 月日不补零（`11月1日`，不是 `11月01日`）。
- **重名处理**：`11月1日练习(2).md`、`(3).md` …… **绝不覆盖**已有文件
  （`unique_path()`；即使逻辑出错也会在写入前二次检查并抛错）。

## 四、学习档案（历史记录保护）

档案文件固定两个：`学习档案/学习进度.md`、`学习档案/知识点掌握记录.md`。

更新方式只能是「追加」或「带备份的更新」：

1. 若文件已存在 → 先复制到 `学习档案/_backup/<名称>.bak.<YYYYmmdd-HHMMSS>.md`（`shutil.copy2`，保留时间戳）；
2. 新文件内容 = 头部说明 + 本次最新状态 + `## 历史记录（更新于 …）` + **上一次的完整内容**（逐字保留）；
3. 返回值里有 `old_bytes_preserved: true` 与 `deleted_anything: false` 作为自检；
4. 任何情况下都不删除文件、不删除历史段落。多次更新会把历史逐层向下嵌套，这是有意的（可追溯）。

## 五、双向链接

- 知识点在正文中一律写成 `[[知识点]]`；
- 练习文档的 frontmatter 里 `knowledge_points: [变量, printf输出]` 用纯名字（不带方括号），
  避免 YAML 里出现嵌套方括号；
- 建议在 Vault 里为每个知识点建同名笔记（可后续由学习者或 obsidian-image-notes 技能补充），
  链接暂时悬空是正常的，不影响使用。

## 六、命令速查

```bash
# 1) 看计划（不写入）
python scripts/vault_write.py plan --vault "<Vault 路径>"

# 2) 写练习文档（默认写进 Vault 根目录）
python scripts/vault_write.py note --vault "<Vault 路径>" --kind practice \
       --date 2026-11-01 --content work/11月1日练习.md --yes
#    想放子目录就加 --in-subdirs

# 3) 写精读文档
python scripts/vault_write.py note --vault "<Vault 路径>" --kind reading \
       --date 2026-11-01 --content work/11月1日代码精读.md --yes      # 同样落在根目录

# 4) 更新学习档案（自动备份 + 保留历史）
python scripts/vault_write.py archive --vault "<Vault 路径>" --name 学习进度.md \
       --body work/progress-body.md --yes
python scripts/vault_write.py archive --vault "<Vault 路径>" --name 知识点掌握记录.md \
       --body work/kp-body.md --yes
```
