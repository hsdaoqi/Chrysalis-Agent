# TUI 使用指南

TUI 是 Chrysalis 的终端界面，基于 Textual。你可以把它理解成“带仪表盘的命令行 Agent”：普通 CLI 只把结果打印出来，TUI 会把模型流式输出、工具调用、工具结果、文件 diff、TODO 状态、权限确认和上下文占用都展示出来。

如果你刚开始学习 Agent，TUI 很适合观察“模型到底是怎么一步步做事的”。

## 启动

安装 TUI 依赖：

```bash
pip install -e ".[tui]"
```

启动：

```bash
chrysalis --tui
```

注意：项目当前注册的入口是 `chrysalis --tui`，不是 `chrysalis-tui`。

如果启动失败并提示找不到 `textual`，说明没有安装 TUI extra：

```bash
pip install -e ".[tui]"
```

如果你想使用语音输入，还需要：

```bash
pip install -e ".[voice]"
```

## 界面区域

TUI 的核心区域可以按任务执行流理解：

| 区域 | 作用 |
| --- | --- |
| 主对话区 | 显示用户输入、模型最终回答和历史回放 |
| 流式输出 | 模型尚未调用工具时的实时文本会临时显示在主区域 |
| Turn 面板 | 每一次工具调用都会创建一个可折叠面板，包含参数、结果、错误和 diff |
| TODO 面板 | Agent 使用 `todo_write` 拆解任务后，会显示当前目标、待办和完成状态 |
| 状态栏 | 显示 `ready`、`thinking`、`executing <tool>`、`approval`、`recording` 等状态 |
| 输入栏 | 输入自然语言任务、命令或权限选择 |

工具调用完成后，Turn 面板标题会显示工具名和摘要；如果工具修改了文件，TUI 会把 diff 追加到最近的工具面板里。

一个简单观察方法：

```text
主对话区看“用户和最终回答”
工具面板看“Agent 做了哪些动作”
TODO 面板看“当前任务计划”
状态栏看“现在处于 thinking / executing / approval 哪个状态”
```

如果你想学习 Agent 原理，最值得看的是工具面板。因为它告诉你模型不是凭空回答，而是先读了哪些文件、跑了哪些命令、遇到了什么错误。

## 代码里是怎么串起来的

TUI 不是自己“理解”Agent，它只是把 `Kernel` 的事件翻译成 Textual 组件。核心路径在这几个文件：

| 文件 | 作用 |
| --- | --- |
| `chrysalis/tui/bridge.py` | 把 `Kernel.run()` 放到后台线程里，接住 stream、tool、working 和 permission 事件 |
| `chrysalis/tui/app.py` | 真正的界面层，负责渲染消息、面板、快捷键和命令 |
| `chrysalis/tui/events.py` | 定义 `StreamChunk`、`ToolCallStarted`、`FileDiff`、`PermissionRequested` 等事件类型 |
| `chrysalis/tui/widgets/*` | `TodoPanel`、`ToolPanel`、`StreamDisplay`、`DiffView` 等具体组件 |

如果你沿着代码看，TUI 的一次任务大致是：

```text
Input.Submitted
  -> ChrysalisApp.on_input_submitted()
  -> ChrysalisApp._run_agent()
  -> AgentBridge.run_task()
  -> Kernel.run()
  -> on_stream_chunk / on_tool_call / on_working_change / on_permission_request
  -> ChrysalisApp.on_stream_chunk()
  -> ChrysalisApp.on_tool_call_started()
  -> ChrysalisApp.on_tool_call_completed()
  -> ChrysalisApp.on_file_diff()
  -> ChrysalisApp.on_agent_done()
```

这也是为什么你会看到：

- 流式输出先出现在主区域，再被折叠成工具面板。
- TODO 面板跟着 `WorkingMemory.todo_snapshot()` 自动刷新。
- 权限确认时输入框会变成“选择操作”的入口。
- 文件修改后会自动追加 diff。

TUI 本身不直接调用工具，也不直接请求模型。它做的是 UI 层工作：

```text
用户在 TUI 输入任务
  -> TUI 把任务交给 AgentBridge
  -> AgentBridge 在线程里调用 Kernel.run()
  -> Kernel / AgentLoop / Tools 正常运行
  -> 回调把事件发回 TUI
  -> TUI 渲染事件
```

这意味着 TUI 和 CLI 的 Agent 行为应该一致，只是展示方式更丰富。

## 快捷键

| 快捷键 | 作用 |
| --- | --- |
| `Ctrl+C` | 任务运行中表示取消；空闲时退出 TUI |
| `Ctrl+L` | 清屏 |
| `Ctrl+G` | 打开历史用户问题列表，选择后跳转 |
| `Ctrl+R` | 语音输入：按一次开始录音，再按一次停止并转写 |
| `Tab` | 补全命令或在补全项中选择 |
| `Esc` | 关闭弹窗；权限确认中按下会拒绝本次请求 |

语音输入需要安装：

```bash
pip install -e ".[voice]"
```

语音输入的逻辑是：按 `Ctrl+R` 开始录音，再按一次停止录音并转写。转写结果会进入输入框，你确认后再提交任务。

## 常用命令

TUI 当前直接处理这些命令：

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示快捷键和命令帮助 |
| `/session` | 查看会话列表 |
| `/session new` | 新建会话 |
| `/session load <n>` | 加载第 n 个会话 |
| `/session delete <n>` | 删除第 n 个会话 |
| `/permissions` | 查看权限等级和永久授权 |
| `/cron` | 查看 cron 定时任务 |
| `/cron list` | 列出 cron 定时任务 |
| `/cron create @path` | 从 JSON 文件创建 cron 任务 |
| `/cron tick` | 手动执行到期任务 |
| `/cron run <id>` | 手动执行指定任务 |
| `/cron pause <id>` | 暂停任务 |
| `/cron resume <id>` | 恢复任务 |
| `/cron remove <id>` | 删除任务 |

任务队列命令 `/queue` 和 `/add <task>` 在普通交互模式中最稳定：

```bash
chrysalis --interactive
```

TUI 里退出请使用 `Ctrl+C`。

如果你不确定当前 TUI 支持哪些命令，输入：

```text
/help
```

它会展示当前版本注册的命令和快捷键。

## 会话工作流

查看会话：

```text
/session
```

加载第一条会话：

```text
/session load 1
```

加载后 TUI 会回放该会话的 canonical history，把历史用户消息、工具调用、工具结果和最终回答重新渲染到界面中。

新建干净会话：

```text
/session new
```

删除会话：

```text
/session delete 2
```

会话文件保存在 `data/sessions/`，TUI 与 CLI、桌面端共用同一套会话存储。

加载会话后，TUI 会尝试把 canonical history 重新渲染成可读对话。你可能会看到历史工具调用和工具结果，这不是重复执行工具，而是在回放已有历史。

## 权限确认

当工具需要确认时，TUI 会进入 `approval` 状态，并显示工具名、风险摘要和可选操作。常见选项：

```text
允许本次
永久允许
拒绝
详细说明
```

操作方式：

1. 用上下方向键选择，按 `Enter` 提交。
2. 也可以直接输入选项文本，比如 `允许本次`。
3. 按 `Esc` 会拒绝本次请求。

`详细说明` 会展示工具名、风险等级和参数预览，但不会执行操作。看完后可以再选择允许或拒绝。

如果你选择“永久允许”，TUI 会把授权写入 `data/permissions.json`。以后同类请求可能会自动通过。对写文件、运行代码这类操作，请先确认路径和参数。

## TODO 面板

Agent 在复杂任务中会调用 `todo_write` 维护任务列表。TUI 会把这些 TODO 渲染为底部面板：

- `pending` 项展示在前面。
- `completed` 项自动移动到底部。
- 待办较多时只展示头尾，避免挤占主对话区。
- 当前 active TODO 会用箭头标识。

这份 TODO 属于当前任务的 Working Memory。它不是长期会话记录，每次新任务开始时都会重置。

如果你看到 TODO 面板为空，不代表任务没有进行，只是模型当前没有调用 `todo_write`。复杂任务中它更常出现，简单问答通常不需要。

## 文件 diff

当工具 `file_write` 或 `file_patch` 修改文件时，TUI 会在工具执行前保存文件旧内容，执行后比较新旧内容，并把 unified diff 追加到最近的 Turn 面板。

如果你在 TUI 中看到 diff，说明文件已经被工具成功修改。对于高风险修改，建议让 Agent 继续运行测试或读回文件确认。

diff 的作用是让你快速知道“这次工具到底改了哪里”。它不是版本控制系统的替代品。如果你想看完整变更，仍然建议使用：

```bash
git diff
```

## Cron 命令

TUI 的 cron 命令复用 `chrysalis.kernel` 中的交互命令实现。创建任务推荐写一个 JSON 文件，再通过 `@path` 引用：

```json
{
  "id": "daily-doc-check",
  "name": "Daily docs check",
  "schedule": {
    "type": "periodic",
    "period": "daily",
    "start_at": "2026-06-01T09:00"
  },
  "prompt": "检查 docs 目录是否有过期内容，并给出简短报告。"
}
```

创建：

```text
/cron create @workspace/daily-doc-check.json
```

手动触发到期任务：

```text
/cron tick
```

所有 cron 任务定义保存在 `data/cron/jobs/`，执行输出保存在 `data/cron/output/`。

## 推荐上手任务

### 任务 A：观察只读工具调用

```text
请阅读 README.md 和 docs/index.md，说明它们分别面向谁。
```

你应该能看到 `file_read` 工具调用。

### 任务 B：观察 TODO

```text
请先制定一个三步计划，再检查 docs/guide 目录的文档是否适合新手阅读。
```

你可能会看到 `todo_write` 更新 TODO 面板。

### 任务 C：观察权限确认

```text
请在 workspace/tui-test.txt 写入一句测试文本。
```

在 `balanced` 权限下，这类写文件任务通常会触发确认。

## 常见问题

| 问题 | 说明 |
| --- | --- |
| TUI 空白或启动失败 | 先确认安装了 `pip install -e ".[tui]"` |
| 语音输入没反应 | 需要安装 `voice` extra，并确认麦克风权限 |
| 工具面板很多看不过来 | 长任务正常现象，可以重点看失败的工具和 diff |
| 权限弹窗按 Esc 后任务停止 | Esc 表示拒绝本次请求，Agent 会尝试换方案或结束 |
| 加载会话后出现旧工具信息 | 这是历史回放，不是重新执行工具 |

一句话总结：**TUI 的价值不是让 Agent 更聪明，而是让 Agent 的行动过程变得可观察。**
