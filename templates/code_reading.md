---
{{frontmatter}}
---

# {{title}}

{{selection_basis}}

**配套笔记：** {{companion_note}}

**读法：** {{how_to_read}}

| # | 主题 | 对应你的练习 |
| :-- | :-- | :-- |
{{#each items}}
| {{index}} | {{topic_cell}} | {{related_exercises}} |
{{/each}}

---

{{#each items}}
## 第 {{index}} 篇｜{{title}}

**为什么读：** {{why}}

{{code_block}}

**输入：** {{input_spec}}

**先预测（别急着运行）：**

{{predict_block}}

**实际输出：**

{{output_block}}

**验证状态：** {{verify_line}}

**读代码时盯住这几点：**

{{watch_points}}

**相关笔记：** {{related_notes}}

---

{{/each}}

## 暂时先不要读的

{{not_yet}}
