---
{{frontmatter}}
---

# {{title}}

> 进阶模式：真实开源项目片段（需已掌握指针/结构体等前置知识点）。
> 只给**仓库 + 文件路径 + 函数名 + 行为描述**，不复制第三方整份代码（遵守各仓库许可证）。
> 本次薄弱点：{{weak_points}}
> 网络状态：{{network_note}}

## 精读方法

{{how_to_read}}

## 推荐清单（{{count}} 份）

{{#each items}}
### {{index}}. {{name}}

| 项目 | 内容 |
| :-- | :-- |
| 语言 | {{language}} |
| 难度 | {{difficulty}} / 5 |
| 来源 | {{source}} |
| 链接 | {{link_line}} |
| 文件路径 | `{{path_line}}` |
| 许可证 | {{license_line}} |
| 验证状态 | {{verification.status}}（{{verification.reason}}） |
| 验证时间 | {{verification.checked_at}} |

- **推荐理由**：{{reason}}
- **知识点**：{{kp_links}}
- **阅读重点**：{{focus}}
- **精读方法**：{{method}}
- **编程思想**：{{ideas}}

**具体阅读任务**

{{tasks_md}}

{{/each}}

---

> 验证状态为「未验证 / 待验证」的条目，不得据此引用具体链接、路径或作者；请先自行核实。
