---
{{frontmatter}}
---

# {{title}}

**配套笔记：** {{companion_note}}

**本次情况：** {{date}}｜共 {{question_count}} 题（已提交 {{submitted_count}}，未完成 {{unsubmitted_count}}）｜得分 {{score_text}}｜{{execution_text}}

**练习前进度：** {{progress_analysis}}

{{#if sandbox_note}}
**沙箱与隔离：** {{sandbox_note}}
{{/if}}

**本次重点：**

{{key_points}}

{{#if pending_model_input}}
> ⚠️ 待补充（保持空缺，不编造）：{{pending_model_input}}
{{/if}}
{{#if unfinished_ids}}
> ⚠️ 未完成题目：{{unfinished_ids}} —— 标记为「未完成」，不计入得分，也不编造答案。
{{/if}}

---

{{#each questions}}
## 第 {{index}} 题｜{{title}}

**题型：** {{type_cn}}　**难度：** {{difficulty}}/5　**预计：** {{est_minutes}} 分钟　**知识点：** {{kp_links}}

### 原题

{{stem}}

{{code_block}}

{{requirements_block}}

### 我的答案

{{my_answer_block}}

### 标准答案

{{reference_block}}

{{explanation}}

{{result_block}}

### 注意事项及易错点

{{pitfalls}}

**相关笔记：** {{related_notes}}

---

{{/each}}

# 总结

## 一、知识点速查表

{{kp_table}}

**按主题查笔记：** {{topic_links}}

## 二、目前最需要改进的 {{improve_count}} 点

{{improvements}}

## 三、做得好的地方

{{good_points}}

## 四、未掌握知识点与掌握度变化

{{mastery_change}}

## 五、下一步建议

{{next_steps}}

