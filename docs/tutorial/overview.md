# Agent 原理概述

Chrysalis 不是单纯的聊天壳，而是一个本地 Agent 运行时。它的核心不是“把用户输入发给模型”，而是把一次任务拆成：

```text
配置加载 -> 上下文组装 -> 模型推理 -> 工具执行 -> 结果回灌 -> 会话保存 -> 经验沉淀
```

这一页按代码执行顺序讲。你可以一边看文档，一边打开对应文件跟着走。

## 推荐阅读顺序

如果你是第一次读这个项目，建议按这个顺序：

1. [快速开始](/guide/quickstart)：先知道怎么跑起来。
2. 本页：看清整体链路。
3. [LLM History](/tutorial/llm-history)：理解会话数据格式。
4. [工具调用](/tutorial/tools)：理解 Agent 怎么真正做事。
5. [工作记忆](/tutorial/working-memory)：理解单次任务里的进度管理。
6. [长期记忆](/tutorial/long-term-memory)：理解 `memory/` 的 SOP 和事实库。
7. [技能库](/tutorial/skills)：理解 `skills/` 如何沉淀可复用工作流。
8. [上下文压缩](/tutorial/context-compaction)：理解长会话为什么不会直接爆上下文。

## 第 0 步：命令入口

入口定义在 `pyproject.toml`：

```toml
[project.scripts]
chrysalis = "chrysalis.kernel:main"
chrysalis-gateway = "chrysalis.gateway.main:main"
```

所以当你运行：

```bash
chrysalis "总结 README.md"
```

真正进入的是 `chrysalis/kernel.py::main()`。

`main()` 做三件事：

1. 用 `argparse` 解析 `--interactive`、`--tui`、`--quiet` 和任务文本。
2. 根据模式分发到 `run_interactive()`、`launch_tui()` 或单次任务。
3. 对单次任务创建 `Kernel(progress=...)`，调用 `Kernel.run(task)`。

这一步只负责入口分流，不直接处理模型、工具或记忆。

## 第 1 步：Kernel 装配运行时

`Kernel` 是顶层装配器，位置是 `chrysalis/kernel.py`。

`Kernel.__init__()` 会创建这些核心对象：

| 对象 | 创建位置 | 作用 |
| --- | --- | --- |
| `AgentConfig` | `configs/config.py` | 加载目录、模型、权限、最大轮数等配置 |
| `SessionStore` | `chrysalis/session_store.py` | 管理 `data/sessions/*.json` |
| `UsageTracker` | `chrysalis/llm/usage.py` | 记录 token、turn 和费用估算 |
| `LLMClient` | `chrysalis/llm/__init__.py::create_client()` | 单模型或多模型 Failover 客户端 |
| `PermissionEngine` | `chrysalis/permission.py` | 判断任务和工具是否需要确认 |
| `AgentLoop` | `chrysalis/agent_loop.py` | 真正执行观察 - 行动循环 |

装配完成后，`Kernel` 就是一个可以反复调用的本地 Agent 实例。

## 第 2 步：Kernel.run 处理任务边界

`Kernel.run(task)` 不是直接调用模型，它先处理任务边界：

1. `self.llm.reset_task_usage()`：重置本次任务 usage。
2. `_resolve_pending_user_action(task)`：如果上一轮因为权限、登录或用户操作暂停，这里判断用户是否已经完成。
3. `self.loop.run(run_task, session_context=extra_context)`：进入真正的 AgentLoop。
4. `self.tracker.end_task(...)`：记录耗时、模型、token 和费用。
5. `result["context"] = self.llm.context_usage()`：返回上下文使用情况。

所以 `Kernel.run()` 是“任务级边界”：它管一次任务的开始、结束、恢复、统计和返回值。

## 第 3 步：AgentLoop.run 建立当前任务上下文

`AgentLoop` 在 `chrysalis/agent_loop.py`。

每次任务开始时：

```text
self.working.reset()
self._tool_trace = []
self.history_info.append("[USER]: ...")
```

这说明：

- `WorkingMemory` 是单次任务级别的，每次任务都会清空。
- `_tool_trace` 也是本次任务级别的，用于后面生成 skill draft。
- `history_info` 是轻量文本锚点，帮助 `ContextEngine` 组装会话连续性。

然后 `AgentLoop.run()` 会做任务权限判断：

```text
permission = self.permission_engine.assess_task(task, session_context=session_context)
```

如果任务本身看起来就是危险操作，会在这里被拦截，而不是等到工具执行时才拦。

## 第 4 步：ContextEngine 组装系统上下文

上下文组装在 `chrysalis/context_engine.py::ContextEngine.assemble()`。

它输入：

- `base_system`：系统提示词。
- `task`：当前任务。
- `working`：当前任务的工作记忆。
- `history_lines`：轻量历史锚点。
- `session_context`：用户刚刚批准、拒绝或完成的运行时上下文。

它输出：

- `system`：最终发给模型的系统提示词。
- `anchor`：可选的会话锚点。
- `included`：本次包含了哪些上下文段。

实际组装顺序是：

```text
system prompt
  -> memory/global_mem_insight.txt
  -> WorkingMemory.to_prompt()
  -> TODO reminder
  -> session_context
  -> related memory files
  -> related skills
```

注意最后一项：当前代码已经接入 `SkillStore`。`ContextEngine._related_skills()` 会根据任务、工作记忆和 SOP 线索调用 `SkillStore.context_for_task()`，把相关 active skill 的摘要注入 prompt。

## 第 5 步：LLMClient 统一消息格式

`AgentLoop` 只给 `LLMClient.chat()` 一组很简单的消息，例如：

```python
[{"role": "user", "content": task}]
```

但内部历史不能这么简单，因为它还要保存工具调用、图片、thinking、tool_result 等 block。

所以 `chrysalis/llm/client.py::LLMClient._merge_user_message()` 会把消息合并成 canonical 格式：

```python
{
    "role": "user",
    "blocks": [
        {"type": "tool_result", ...},
        {"type": "image", ...},
        {"type": "text", ...},
    ],
}
```

这份 canonical history 是 Chrysalis 内部的统一协议。后面再由 `protocols.py` 转成 OpenAI 或 Anthropic 的实际请求格式。

## 第 6 步：BaseSession 负责历史、压缩和协议分发

`BaseSession` 在 `chrysalis/llm/session.py`。

`BaseSession.ask()` 的顺序是：

1. 把 canonical user message 追加进 `self.history`。
2. 调用 `CompactionManager.apply_preflight()` 做预压缩。
3. 如果仍然接近上限，构造 LLM summary 请求。
4. 调用 `_ask_with_reactive_retry()` 发起真实模型请求。
5. 如果遇到 context limit error，做 reactive compact 后重试一次。
6. 成功后通过 `_append_assistant()` 把 assistant blocks 写回 history。

这层的重点是：**模型调用不是无状态请求，它始终围绕同一份 canonical history 转。**

## 第 7 步：模型返回 tool_call 后进入工具闭环

在 function calling 模式里，`AgentLoop._run_function_calling()` 每轮只处理一个工具调用：

```text
LLM response
  -> response.tool_calls[0]
  -> json.loads(arguments)
  -> _execute_tool_with_guards()
  -> compact_observation()
  -> dumps_observation()
  -> 继续问模型
```

真正执行工具前会先走：

```text
PermissionEngine.assess_tool()
```

如果需要确认，TUI / 桌面端会通过回调显示权限请求；CLI 交互模式则把请求作为 `need_user` 返回，让用户输入选择。

工具真正执行的位置是：

```python
run_tool(tool_name, args, self.workspace)
```

这个 `run_tool` 来自 `chrysalis/tools/registry.py`，它会按工具名找到被 `@tool(...)` 注册的函数。

## 第 8 步：工具结果可能改写工作记忆

不是所有工具结果都只是“给模型看”。有些工具会返回内部标记：

| 标记 | 来源工具 | 后续处理 |
| --- | --- | --- |
| `_todo` | `todo_write` | 调用 `WorkingMemory.update_todos()` |
| `_checkpoint` | `update_working_checkpoint` | 调用 `WorkingMemory.update_checkpoint()` |
| `_long_term` | `start_long_term_update` | 调用 `WorkingMemory.request_long_term_update()` |

这个逻辑集中在：

```text
AgentLoop._handle_agent_tool_side_effects()
```

也就是说，`todo_write` 工具本身只是返回“我想更新 TODO”的数据，真正修改 `WorkingMemory` 的地方在 `AgentLoop`。这样设计的好处是：工具函数简单，AgentLoop 统一管理状态副作用。

## 第 9 步：任务结束后保存会话并尝试沉淀技能

当模型返回最终文本时，`AgentLoop` 返回：

```python
{"ok": True, "final": content}
```

随后：

- `LLMClient` 通过 `on_history_changed` 触发 `SessionStore.save()`。
- `Kernel.run()` 追加 usage、context 等任务统计。
- 如果任务成功，`AgentLoop.run()` 会调用 `_maybe_create_skill_draft()`。

技能草稿生成逻辑在：

```text
chrysalis/skills/curator.py::SkillCurator.maybe_create_draft()
```

它只在任务成功且满足条件时创建草稿：

- Agent 调用了 `start_long_term_update`。
- 或任务 turn 足够多。
- 或工具调用次数足够多。

草稿不会立刻变成 active skill，必须审核后通过 `skill_promote` 提升。

## 关键对象总表

| 对象 | 文件 | 你什么时候需要改它 |
| --- | --- | --- |
| `Kernel` | `chrysalis/kernel.py` | 改入口行为、交互命令、任务级统计或恢复逻辑 |
| `AgentLoop` | `chrysalis/agent_loop.py` | 改工具循环、工作记忆副作用、权限回调、技能草稿触发 |
| `LLMClient` | `chrysalis/llm/client.py` | 改 canonical message 合并、usage 记录、模型调用入口 |
| `BaseSession` | `chrysalis/llm/session.py` | 改历史生命周期、上下文压缩前后处理、provider 分发 |
| `ContextEngine` | `chrysalis/context_engine.py` | 改长期记忆、工作记忆、技能和会话上下文注入 |
| `WorkingMemory` | `chrysalis/working.py` | 改 TODO、短期检查点、任务内状态 |
| `SessionStore` | `chrysalis/session_store.py` | 改会话文件结构、列表、重命名、置顶、删除 |
| `PermissionEngine` | `chrysalis/permission.py` | 改权限等级、敏感路径、工具确认策略 |
| `SkillStore` | `chrysalis/skills/store.py` | 改技能存储结构、搜索、查看、提升、归档 |
| `SkillCurator` | `chrysalis/skills/curator.py` | 改自动生成技能草稿的条件和内容 |
| `Tool Registry` | `chrysalis/tools/registry.py` | 改工具注册和 schema 生成方式 |

## 如果你要写新功能

按目标选入口：

| 目标 | 推荐入口 |
| --- | --- |
| 新增一个工具 | 新建或修改 `chrysalis/tools/*.py`，用 `@tool(...)` 注册 |
| 新增一个交互命令 | 修改 `chrysalis/kernel.py::run_interactive()` 或 TUI 对应命令处理 |
| 改 TUI 展示 | 修改 `chrysalis/tui/app.py` 和 `chrysalis/tui/widgets/*` |
| 改桌面端展示 | 修改 `desktop-electron/src/*`，必要时同步 `chrysalis/electron_runtime.py` 的事件协议 |
| 增加一种长期经验注入方式 | 修改 `ContextEngine._memory_sections()` |
| 让成功任务自动沉淀得更聪明 | 修改 `SkillCurator._should_create()` 和 `_render_body()` |
| 调整模型历史压缩策略 | 修改 `chrysalis/llm/context.py` |

一句话总结：**Chrysalis 的主线是 Kernel 装配，AgentLoop 行动，LLMClient 通信，ContextEngine 供给上下文，Tools 执行，SessionStore 保存，SkillStore 沉淀。**
