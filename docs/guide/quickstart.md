# 快速开始

这一页按真实使用流程走一遍：确认配置、跑第一个任务、进入连续会话、处理权限、管理会话和队列。每一步后面我都会尽量把它对到实际代码，方便你知道“这句话对应哪一层实现”。

## 1. 确认模型配置

完成安装后，先确认 `.env` 或 `configs/llm_models.json` 已经配置好模型。最小 `.env` 示例：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.example.com/v1
CHRYSALIS_MODEL=your-model
CHRYSALIS_PERMISSION_LEVEL=balanced
```

如果你不确定当前会读取哪一份配置，回到 [配置说明](/guide/configuration) 查看优先级。

## 2. 跑第一个一次性任务

在项目根目录执行：

```bash
chrysalis "请读取 README.md，概括这个项目能做什么"
```

一次性任务会在结束后输出 JSON。常见字段：

| 字段 | 说明 |
| --- | --- |
| `ok` | 是否成功 |
| `final` | 最终回答 |
| `elapsed_ms` | 本次任务耗时 |
| `usage` | token、turn 和费用估算 |
| `context` | 当前会话上下文占用和压缩状态 |

如果你只想拿最终 JSON，不想看每轮进度摘要：

```bash
chrysalis --quiet "总结 README.md"
```

## 3. 进入连续交互模式

连续交互适合一个主题反复追问，也适合使用会话、队列、权限和 cron 命令：

```bash
chrysalis --interactive
```

进入后会看到：

```text
Chrysalis 交互模式。输入 /exit 或 退出 结束。
chrysalis>
```

你可以直接输入自然语言任务：

```text
chrysalis> 帮我列出 docs 目录目前有哪些页面
```

## 4. 管理会话

会话保存的是 LLM canonical history，路径是 `data/sessions/`。常用命令：

```text
/session              查看最近会话
/session new          新建会话
/session load <编号>  加载第 n 个会话
/session delete <编号> 删除第 n 个会话
```

示例：

```text
chrysalis> /session
chrysalis> /session load 1
chrysalis> 继续刚才的项目文档整理任务
```

会话列表里的标题来自会话中第一条用户消息，后续桌面端也可以重命名和置顶。

## 5. 使用任务队列

交互模式内置一个文件队列，存储在 `data/task_queue.json`。它适合把多个独立任务排队执行。

添加任务：

```text
chrysalis> /add 总结 docs/guide/installation.md
chrysalis> /add 检查 docs/tutorial/overview.md 是否缺少架构图
```

查看队列：

```text
chrysalis> /queue
```

执行队列中的下一个 pending 任务：

```text
chrysalis>
```

也就是在提示符处直接按回车。执行成功会标记为 `done`，失败会标记为 `failed`，并把结果摘要写回 `data/task_queue.json`。

## 6. 处理权限确认

默认权限等级是 `balanced`。当 Agent 准备执行可能改变本地状态的动作时，例如写文件、运行脚本、执行浏览器 JS、截图或派生子 Agent，会先返回权限请求。

你通常会看到四类选择：

| 选择 | 含义 |
| --- | --- |
| `允许本次` | 只允许当前这一次操作 |
| `永久允许` | 写入 `data/permissions.json`，以后同类操作自动通过 |
| `拒绝` | 不执行该操作，让 Agent 换方案 |
| `详细说明` | 查看工具、风险等级和参数预览 |

查看当前授权：

```text
chrysalis> /permissions
```

如果你拒绝了一次操作，Chrysalis 会把这个决定作为额外上下文交回 Agent，让它避开该路径继续。

## 7. 切换到 TUI

TUI 适合长任务，因为它会把每一轮工具调用折叠成面板，还会展示 TODO、diff、上下文占用和流式输出。

```bash
chrysalis --tui
```

如果提示缺少依赖：

```bash
pip install -e ".[tui]"
```

详细说明见 [TUI 使用指南](/guide/tui)。

## 8. 启动桌面端

桌面端适合管理多个会话、拖拽附件、预览工作区文件和调整模型配置。

```powershell
cd desktop-electron
npm install
npm run dev
```

详细说明见 [桌面端指南](/guide/desktop)。

## 9. 一个完整任务内部会发生什么

一次任务大致按这个顺序运行：

```text
用户输入
  -> Kernel 装配配置、会话、权限、LLM 和 AgentLoop
  -> AgentLoop 重置 WorkingMemory
  -> ContextEngine 组装 system prompt、长期记忆、工作记忆和会话上下文
  -> LLMClient 调用模型
  -> 模型返回 tool_call
  -> PermissionEngine 判断是否需要用户确认
  -> Tool Registry 执行工具
  -> Observation 被压缩后送回模型
  -> 模型继续推理，直到给出 final
  -> SessionStore 保存 canonical history
  -> UsageTracker 写入 token / turn / 费用统计
```

把它对到代码里，大致是这样的：

| 发生的事 | 代码位置 |
| --- | --- |
| `chrysalis "任务"` 进入入口 | `pyproject.toml` -> `chrysalis.kernel:main` |
| 解析 `--interactive` / `--tui` / 单次任务 | `chrysalis/kernel.py::main()` |
| 顶层装配配置、会话、权限、LLM | `chrysalis/kernel.py::Kernel.__init__()` |
| 开始一轮任务并处理待恢复的用户操作 | `chrysalis/kernel.py::Kernel.run()` |
| 重置工作记忆并进入观察 - 行动循环 | `chrysalis/agent_loop.py::AgentLoop.run()` |
| 组装系统提示词、记忆和会话锚点 | `chrysalis/context_engine.py::ContextEngine.assemble()` |
| 把简化消息变成 canonical history 并请求模型 | `chrysalis/llm/client.py::LLMClient.chat()` |
| 在每次请求前做上下文压缩 | `chrysalis/llm/session.py::BaseSession.ask()` |
| 真正执行工具前做权限判断 | `chrysalis/agent_loop.py::AgentLoop._execute_tool_with_guards()` |
| 把成功结果写回会话文件 | `chrysalis/session_store.py::SessionStore.save()` |
| 如果这次任务够大，尝试生成技能草稿 | `chrysalis/skills/curator.py::SkillCurator.maybe_create_draft()` |

如果你是照着代码写自己的扩展，最常见的三种起点是：

- 想加一个新能力，就改 `chrysalis/tools/*.py`。
- 想让任务记住更多上下文，就改 `chrysalis/working.py` 或 `chrysalis/context_engine.py`。
- 想让成功任务自动沉淀成复用方案，就看 `chrysalis/skills/curator.py` 和 `chrysalis/skills/store.py`。

如果你想理解每一步的代码结构，继续阅读 [Agent 原理概述](/tutorial/overview)。
