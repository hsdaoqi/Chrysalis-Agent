# 工作记忆

工作记忆是 Chrysalis 在**单次任务**内使用的短期上下文。它不像 LLM History 那样保存完整会话，也不像 `memory/` 那样长期沉淀经验。它只回答一个问题：

```text
这个任务现在做到哪一步了，下一步应该怎么继续？
```

## 代码位置

| 文件 | 作用 |
| --- | --- |
| `chrysalis/working.py` | `WorkingMemory` 和 `TodoItem` 的数据结构与更新逻辑 |
| `chrysalis/tools/agent_tools.py` | `todo_write`、`update_working_checkpoint`、`start_long_term_update` 工具 |
| `chrysalis/agent_loop.py` | 真正把工具结果写进 `WorkingMemory` 的地方 |
| `chrysalis/context_engine.py` | 把工作记忆转换成 prompt 注入模型 |

## 第 1 步：任务开始时重置

每次 `AgentLoop.run(task)` 开始时都会执行：

```text
self.working.reset()
```

这意味着：

- 工作记忆不跨任务保存。
- 一个会话里连续问多个问题，每个问题都有自己的短期状态。
- 如果需要跨任务保存，应该进入 LLM History、`memory/` 或 `skills/`。

## 第 2 步：工作记忆里保存什么

`WorkingMemory` 里主要有这些字段：

| 字段 | 作用 |
| --- | --- |
| `key_info` | 当前任务最重要的事实、进度或结论 |
| `related_sop` | 相关 SOP 文件名，例如 `verify_sop.md` |
| `long_term_update_requested` | 是否请求把本次经验沉淀为长期经验 |
| `todo_goal` | 当前 TODO 的总目标 |
| `todos` | 待办列表 |
| `rounds_since_todo` | 距离上次 TODO 提醒过去几轮 |

这些字段都不应该塞大段历史。它们应该短、准、可继续。

## 第 3 步：工具怎么提出更新请求

Agent 不能直接拿到 `WorkingMemory` 对象。它只能调用工具。对应工具在 `chrysalis/tools/agent_tools.py`：

| 工具 | 返回标记 | 含义 |
| --- | --- | --- |
| `todo_write` | `_todo` | 请求更新 TODO |
| `update_working_checkpoint` | `_checkpoint` | 请求更新关键进度 |
| `start_long_term_update` | `_long_term` | 请求标记长期沉淀 |
| `ask_user` | `need_user` | 请求暂停并询问用户 |

举例，`todo_write` 返回的不是“最终回答”，而是：

```json
{
  "ok": true,
  "_todo": true,
  "message": "TODO list updated",
  "todo_action": "set",
  "goal": "完善 docs",
  "todos": ["重写安装指南", "补充工具调用", "补充技能库"]
}
```

## 第 4 步：AgentLoop 真正改写工作记忆

真正处理这些标记的位置是：

```text
chrysalis/agent_loop.py::AgentLoop._handle_agent_tool_side_effects()
```

它的逻辑可以理解为：

```text
如果 observation 有 _todo:
  -> working.update_todos(...)

每轮工具调用后:
  -> working.tick_round()

如果 observation 有 _checkpoint:
  -> working.update_checkpoint(...)

如果 observation 有 _long_term:
  -> working.request_long_term_update(...)

如果工作记忆变化:
  -> on_working_change(working.todo_snapshot())
```

这个设计很重要：工具只表达“想更新什么”，状态副作用集中在 `AgentLoop`。这样 TUI、桌面端和 CLI 都能看到一致的工作记忆状态。

## 第 5 步：TODO 如何更新

`WorkingMemory.update_todos()` 支持这些动作：

| action | 效果 |
| --- | --- |
| `set` / `replace` | 用新列表替换整个 TODO |
| `append` | 追加 TODO |
| `update` | 按 id 或 title 合并更新 |
| `complete` | 把匹配项标记为完成 |
| `clear` / `reset` | 清空 TODO |
| `reorder` | 按传入顺序重排 |

完成项会自动移动到底部：

```text
pending A
pending C
completed B
```

这让 UI 里最重要的未完成项始终靠前。

## 第 6 步：TODO 提醒如何触发

每次工具调用后，`AgentLoop` 会执行：

```text
self.working.tick_round()
```

如果存在未完成 TODO，并且超过 `todo_reminder_interval`，`WorkingMemory.todo_reminder_prompt()` 会生成一段提醒：

```text
## TODO Reminder
Plan first, then execute.
- goal: ...
- [pending] ...
```

`ContextEngine` 会把这段提醒注入系统上下文，逼模型回到任务计划上。

## 第 7 步：工作记忆怎么进入 prompt

`ContextEngine._memory_sections()` 会调用：

```text
working.to_prompt()
working.todo_reminder_prompt()
```

然后把结果放进最终 system prompt 的 `## Context Engine` 段落里。

`working.to_prompt()` 大概会渲染成：

```text
## 当前短期工作记忆
- key_info: 已完成安装指南重写，正在补工具教程
- related_sop: verify_sop.md
- todo_goal: 完善 docs
- todos:
  - [pending] 补充技能库
  - [completed] 重写安装指南
```

## 第 8 步：UI 如何显示它

TUI 和桌面端并不是自己解析模型回答来猜 TODO。它们接收的是 `on_working_change` 回调。

在 TUI 中：

- `AgentBridge._on_working_change()` 把 snapshot 发成 `WorkingChange` 事件。
- `ChrysalisApp.on_working_change()` 调 `TodoPanel.set_snapshot()`。

桌面端同理：

- `ElectronRuntime._on_working_change()` 把 snapshot 发送给 Electron 主进程。
- renderer 根据 runtime event 刷新 TODO / 工作状态视图。

所以 UI 看到的是结构化状态，不是从文字里抠出来的。

## 和长期记忆、技能库的关系

`start_long_term_update` 不会直接写 `memory/`。它只是设置：

```text
working.long_term_update_requested
```

任务结束后，`AgentLoop.run()` 会看这条标记。若任务成功，`SkillCurator.maybe_create_draft()` 可能根据它生成一个技能草稿。

也就是说：

- 工作记忆负责“本次任务正在做什么”。
- 长期记忆负责“跨任务稳定事实和 SOP”。
- 技能库负责“可复用工作流”。

## 如果你要扩展工作记忆

按这个流程写：

1. 在 `WorkingMemory` 里加字段和更新方法。
2. 在 `agent_tools.py` 里加一个工具，让模型能请求更新它。
3. 在 `AgentLoop._handle_agent_tool_side_effects()` 里处理工具返回标记。
4. 在 `WorkingMemory.to_prompt()` 里决定如何渲染给模型。
5. 如果 UI 要显示它，就在 `on_working_change` 的 snapshot 里暴露结构化字段。

这样新增状态才会贯穿“工具 -> AgentLoop -> ContextEngine -> UI”的完整链路。
