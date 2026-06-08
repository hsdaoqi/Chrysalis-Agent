# 快速开始

这一页按真实使用流程走一遍：确认配置、跑第一个任务、进入连续会话、处理权限、管理会话和队列。每一步后面都会把“你看到的现象”和“代码里发生了什么”对上，方便你从会用慢慢走到会改。

如果你只想先体验，照着命令走就行；如果你想学习 Agent 原理，每一节后面的源码说明也建议看。

## 0. 你现在应该已经完成什么

开始前确认三件事：

1. 已经在项目根目录。
2. 已经安装过 `pip install -e .`。
3. 已经配置好 `.env` 或 `configs/llm_models.json`。

可以用这两个命令快速检查：

```bash
chrysalis --help
python -c "from pathlib import Path; print(Path.cwd())"
```

如果 `chrysalis --help` 不存在，回到 [安装指南](/guide/installation)。如果模型 API 还没配，先看 [配置说明](/guide/configuration)。

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

最简单的测试任务是：

```bash
chrysalis "请只回答：连接测试成功"
```

这个任务不会读写文件，适合验证模型配置。如果失败，先看输出里的错误信息，通常是 API Key、base_url、model 名称或网络问题。

## 2. 跑第一个一次性任务

在项目根目录执行：

```bash
chrysalis "请读取 README.md，概括这个项目能做什么"
```

这个任务比“1+1 等于几”更接近真实 Agent，因为它很可能会触发 `file_read` 工具。你可以观察终端里的进度摘要：

```text
[turn 1] model thinking...
[tool] file_read ...
[turn 2] model answering...
```

具体显示会根据运行模式和 quiet 参数不同而变化，但核心都是：模型先决定读文件，工具读完后，模型再根据内容回答。

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

### 这一步对应的源码

```text
pyproject.toml
  -> chrysalis.kernel:main
  -> Kernel(progress=...)
  -> Kernel.run(task)
  -> AgentLoop.run(task)
```

到这里，Chrysalis 已经完成一次“模型 + 工具 + 会话保存”的最小闭环。

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

连续交互模式适合一个主题连续追问，比如：

```text
chrysalis> 先总结 docs 目录结构
chrysalis> 哪几篇对新手最重要？
chrysalis> 帮我把阅读顺序写成一个清单
```

会话历史会被保存到 `data/sessions/`，所以后面可以加载回来继续。

交互模式也支持用户操作恢复。例如 Agent 需要你登录网页时，可能会返回“请在浏览器中完成登录”。你完成后输入：

```text
chrysalis> 已完成
```

`Kernel._resolve_pending_user_action()` 会把这个状态变成额外上下文交回 Agent，让它继续。

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

### 会话里到底保存了什么

会话文件不是简单聊天文本，而是 canonical history。它会记录：

- 用户输入。
- 模型文字回复。
- 模型发起的工具调用。
- 工具返回的结果。
- 图片、thinking 等 block。

这就是为什么加载旧会话后，Agent 能继续理解之前做过什么。详细结构见 [LLM History](/tutorial/llm-history)。

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

队列适合这些场景：

- 想让 Agent 依次总结多个文件。
- 想把多个独立检查任务排队。
- 想在交互模式里先收集任务，后面逐个执行。

不适合这些场景：

- 每个任务都强依赖上一个任务的临时状态。
- 任务中间需要大量人工判断。
- 任务本身很危险，比如大规模删除或迁移文件。

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

### 权限确认为什么重要

Chrysalis 是本地 Agent。它能执行的动作比普通聊天机器人多得多，所以必须知道什么时候该停下来问你。

常见触发：

```text
写文件
运行代码
执行 shell
截图
读敏感路径
浏览器中执行 JS
派生子 Agent
```

如果你只是阅读文档或总结项目，通常不会频繁确认。如果 Agent 要真正改东西，就应该让你知道。

### 权限对应的源码

```text
chrysalis/permission.py::PermissionEngine.assess_task()
chrysalis/permission.py::PermissionEngine.assess_tool()
chrysalis/agent_loop.py::AgentLoop._execute_tool_with_guards()
```

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

一个适合 TUI 的练习任务：

```text
请阅读 docs 目录，列出每篇文档的主题，并指出哪里需要补充新手说明。
```

这个任务会产生多次文件读取和总结，TUI 能更清楚地看到每一轮工具调用。

## 8. 启动桌面端

桌面端适合管理多个会话、拖拽附件、预览工作区文件和调整模型配置。

```powershell
cd desktop-electron
npm install
npm run dev
```

详细说明见 [桌面端指南](/guide/desktop)。

桌面端和 CLI/TUI 共用 `data/sessions/`。你可以先用 CLI 跑一个任务，再在桌面端里查看会话；也可以在桌面端新建会话，然后回到 TUI 继续。

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

## 10. 三个推荐练习

### 练习 A：只读项目

```bash
chrysalis "请阅读 docs 目录，给我一份文档地图，不要修改任何文件"
```

这个练习适合观察 `file_read` 和会话保存。

### 练习 B：让 Agent 提计划但不执行

```bash
chrysalis "请为 README 重写制定计划，只给计划，不要修改文件"
```

这个练习适合理解“用户约束会进入上下文”。如果你明确说不要修改文件，Agent 应该遵守。

### 练习 C：进入 TUI 看长任务

```bash
chrysalis --tui
```

然后输入：

```text
请检查 docs/tutorial 里的教程是否按从易到难排列，并给出改进建议。
```

这个练习适合观察工具面板、TODO 和上下文占用。

## 11. 出错时怎么判断问题在哪

| 现象 | 大概率问题 | 去哪里看 |
| --- | --- | --- |
| `chrysalis` 命令不存在 | 没装包或虚拟环境没激活 | [安装指南](/guide/installation) |
| API 鉴权失败 | `.env` / JSON / 桌面端模型配置错误 | [配置说明](/guide/configuration) |
| 工具被拒绝 | 权限等级或网关权限限制 | [配置说明](/guide/configuration) |
| 长任务突然变短 | 上下文压缩触发 | [上下文压缩](/tutorial/context-compaction) |
| 加载旧会话后不连续 | 会话 history 或 session id 问题 | [LLM History](/tutorial/llm-history) |
| TUI 没法启动 | 缺少 `tui` extra | [TUI 使用指南](/guide/tui) |

一句话总结：**快速开始不只是跑一条命令，而是让你完整体验 Chrysalis 的模型调用、工具执行、权限确认、会话保存和多入口复用。**
