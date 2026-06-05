# Chrysalis

Chrysalis 是一个本地通用 agent。它不是只服务代码任务的 code agent，而是一个可以读写文件、运行脚本、操作浏览器、维护短期/长期记忆，并通过工具循环逐步完成任务的 agent 骨架。

这份 README 分成两部分：

- **使用教程**：怎么安装、配置、运行、使用 TUI 和会话功能。
- **Agent 学习教程**：这个项目如何组织 LLM history、工作记忆、长期记忆、工具调用、上下文压缩和协议转换。

## 使用教程

#### [在线阅读](https://hsdaoqi.github.io/Chrysalis-Agent/)
### 1. 安装

```bash
git clone https://github.com/hsdaoqi/Chrysalis-Agent.git
cd Chrysalis-Agent
pip install -e .
```

如果要使用 TUI：

```bash
pip install -e ".[tui]"
```

如果要使用桌面端：

```powershell
pip install -e .
cd desktop-electron
npm install
npm run dev
```

打包 Electron 桌面端：

```powershell
.\scripts\build_desktop_electron.ps1 -InstallNodeDeps
```

默认输出在：

```text
desktop-electron\dist\release\ChrysalisDesktop-*-portable.exe
```

右键这个 `.exe` 发送到桌面快捷方式即可。

如果要使用视觉或语音能力：

```bash
pip install -e ".[vision]"
pip install -e ".[voice]"
```

### 2. 配置模型

复制 `.env.example` 为 `.env`，填入基础模型配置：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.deepseek.com
CHRYSALIS_MODEL=deepseek-v4-pro
CHRYSALIS_CONTEXT_WINDOW=28000
```

Chrysalis 支持 OpenAI 兼容 API，也支持 Anthropic 协议。多模型配置优先读取：

```text
configs/llm_models.json
```

加载路径是：

```text
Kernel.__init__()
  -> AgentConfig.load_session_configs()
  -> create_client(...)
```

如果 `llm_models.json` 有多个模型，会创建 `FailoverSession`，主模型失败时自动切换备用模型。

### 3. 运行

单次任务：

```bash
chrysalis "帮我总结 README.md"
```

交互模式：

```bash
chrysalis --interactive
```

TUI 模式：

```bash
chrysalis --tui
```

TUI 常用快捷键：

```text
Ctrl+C  退出
Ctrl+L  清屏
Ctrl+G  跳转到历史问题
Ctrl+R  语音输入
Tab     补全 / 命令
Esc     关闭弹窗
```

### 4. 交互命令

```text
/help                 显示帮助
/session              查看会话列表
/session new          新建会话
/session load <n>     加载第 n 个会话
/session delete <n>   删除第 n 个会话
/queue                查看任务队列
/add <task>           添加任务到队列
/exit                 退出
```

会话文件保存在：

```text
data/sessions/
```

任务队列保存在：

```text
data/task_queue.json
```

### 5. 工具集

| 工具 | 用途 |
|------|------|
| `code_run` | 运行 Python / PowerShell / Bash |
| `file_read` | 读取文件，支持行号和关键词 |
| `file_write` | 写入、追加、前插文件 |
| `file_patch` | 替换文件中唯一匹配的文本块 |
| `web_scan` | 用本机浏览器扫描网页，返回简化 HTML |
| `web_execute_js` | 在浏览器标签页执行 JS |
| `update_working_checkpoint` | 更新当前任务的短期工作记忆 |
| `ask_user` | 遇到阻塞时询问用户 |
| `start_long_term_update` | 标记当前任务有可沉淀经验 |
| `spawn_subagent` | 派生并行子 agent 执行子任务 |

工具通过 `@tool(...)` 装饰器注册，导入 `chrysalis.tools` 时自动生成 function calling schema。

## Agent 学习教程

### 1. 总体运行链路

一条任务从输入到完成，大致经过：

```text
main()
  -> Kernel()
    -> AgentConfig()
    -> SessionStore()
    -> UsageTracker()
    -> create_client()
      -> BaseSession / FailoverSession
      -> LLMClient
    -> AgentLoop()
      -> WorkingMemory()
      -> ContextEngine()
  -> Kernel.run(task)
    -> AgentLoop.run(task)
      -> ContextEngine.assemble()
      -> LLMClient.chat(...)
        -> BaseSession.ask(...)
          -> trim_messages_history(...)
          -> to_openai_messages(...) / to_anthropic_messages(...)
          -> openai_stream(...) / claude_stream(...)
        -> SessionStore.save(history)
      -> run_tool(...)
      -> compact_observation(...)
      -> 下一轮
```

核心分工：

```text
Kernel          顶层装配器
AgentLoop       观察-行动循环
LLMClient       AgentLoop 与 BaseSession 之间的适配层
BaseSession     LLM history、压缩、协议分发
FailoverSession 多模型自动切换
ContextEngine   长期记忆动态组装
WorkingMemory   当前任务短期记忆
SessionStore    会话持久化
Tool Registry   工具注册和分发
UsageTracker    token、turn、费用统计
```

### 2. Kernel：总装配器

入口在 `chrysalis/kernel.py`。

`Kernel.__init__()` 创建这些对象：

```python
self.config = AgentConfig()
self.session_store = SessionStore(...)
self.tracker = UsageTracker(...)
self.llm = create_client(...)
self.history = []
self.loop = AgentLoop(...)
```

`Kernel.run(task)` 做这些事：

```text
1. 重置本次任务 usage
2. 处理 pending_user_action 续跑状态
3. 调用 AgentLoop.run(...)
4. 记录 elapsed_ms、token usage、cost
5. 返回 result
```

### 3. AgentLoop：观察-行动循环

入口在 `chrysalis/agent_loop.py`。

初始化属性：

```python
self.llm = llm
self.workspace = workspace
self.max_turns = max_turns
self.progress = progress
self.on_stream_chunk = on_stream_chunk
self.on_tool_call = on_tool_call
self.working = WorkingMemory()
self.history_info = history if history is not None else []
self.context_engine = ContextEngine()
```

其中：

```text
on_stream_chunk  模型流式输出回调，TUI 用来实时显示文本
on_tool_call     工具开始/完成回调，TUI 用来显示工具面板和 diff
working          当前任务内短期工作记忆
history_info     人类可读的轻量 session anchor 历史
```

`AgentLoop.run(task)` 开始时会：

```python
self.working.reset()
self.history_info.append(f"[USER]: ...")
system_prompt = get_system_prompt(include_memory=False)
assembled = self.context_engine.assemble(...)
```

然后进入 function calling 循环：

```text
模型响应 tool_call -> 执行工具 -> 压缩观察结果 -> 带 tool_result 继续问模型
模型响应 text      -> 作为 final 返回用户
```

### 4. LLMClient：消息适配层

入口在 `chrysalis/llm/client.py`。

`AgentLoop` 传入的消息比较简单：

```python
[
    {"role": "user", "content": "读取 README.md"}
]
```

`LLMClient._merge_user_message()` 会转成 canonical message：

```python
{
    "role": "user",
    "blocks": [
        {"type": "text", "text": "读取 README.md"}
    ],
}
```

如果是工具结果：

```python
{
    "role": "user",
    "content": "观察结果...",
    "tool_results": [
        {"tool_use_id": "call_1", "content": "..."}
    ],
}
```

会变成：

```python
{
    "role": "user",
    "blocks": [
        {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "...",
            "is_error": False,
        },
        {"type": "text", "text": "观察结果..."},
    ],
}
```

### 5. BaseSession：真正的 LLM history

入口在 `chrysalis/llm/session.py`。

初始化属性：

```python
self.config = config
self.history = []
self.system = ""
self.tools = None
self._lock = threading.Lock()
```

`BaseSession.history` 使用 canonical block 格式：

```python
[
    {
        "role": "user",
        "blocks": [
            {"type": "text", "text": "读取 README.md"}
        ],
    },
    {
        "role": "assistant",
        "blocks": [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "file_read",
                "arguments": "{\"path\":\"README.md\"}",
            }
        ],
    },
    {
        "role": "user",
        "blocks": [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "...",
                "is_error": False,
            }
        ],
    },
]
```

`BaseSession.ask(message)` 会：

```text
1. history.append(message)
2. trim_messages_history(history, context_window)
3. 复制 history_snapshot
4. 转成 OpenAI 或 Anthropic wire format
5. 调用模型 stream
6. 把 assistant response 写回 history
```

### 6. FailoverSession：多模型自动切换

入口在 `chrysalis/llm/failover.py`。

`FailoverSession(BaseSession)` 暴露和 `BaseSession` 一样的接口，但内部持有多个 `BaseSession`：

```python
self.sessions = sessions
self._spring_back = 300
self._current_idx = 0
self._switched_at = 0.0
```

`_spring_back = 300` 表示切到备用模型后，300 秒后下次请求会优先尝试回到主模型。

### 7. ContextEngine：长期记忆组装

入口在 `chrysalis/context_engine.py`。

它使用 `ContextBudget` 控制注入字符数：

```python
total_chars = 12000
l1_chars = 2500
working_chars = 1500
related_chars = 5000
session_chars = 3000
```

组装顺序：

```text
system prompt
> L1 insight
> working memory
> related L2/L3
> session_context
> session anchor
```

长期记忆分层：

```text
L0  META-SOP        memory_management_sop.md
L1  Insight Index   global_mem_insight.txt
L2  Fact Store      global_mem.txt
L3  SOPs            memory/*.md
L4  Session Archive data/l4_session/
```

原则：

```text
No Execution, No Memory
只有工具验证过的事实才能写入长期记忆。
```

### 8. WorkingMemory：当前任务短期记忆

入口在 `chrysalis/working.py`。

结构：

```python
WorkingMemory(
    key_info="",
    related_sop="",
    long_term_update_requested="",
)
```

渲染到 prompt 时长这样：

```text
## 当前短期工作记忆
- key_info: 已确认 README.md 存在
- related_sop: verify_sop.md
- long_term_update_requested: 发现稳定路径
```

每次新任务开始都会 reset，所以它不是长期记忆。

### 9. Runtime Compact：上下文压缩

入口在 `chrysalis/llm/context.py`。

压缩流程：

```text
microcompact
-> repair_tool_pairs
-> full compact
-> repair_tool_pairs
-> hard trim
-> repair_tool_pairs
```

三种 compact：

```text
microcompact
  截断旧 thinking、tool_result、tool args、图片和大 tag 内容。

full compact
  把早期多轮历史折成结构化 earlier_summary。

hard trim
  如果还超预算，删除最旧的完整 turn。
```

full compact 摘要模板：

```text
<earlier_summary>
Earlier conversation was compacted. Preserve these identifiers and decisions.
Key turns:
- user: ...
- assistant: ...
Identifiers:
D:\Project\Chrysalis\..., tests/test_llm.py, pytest ...
Tools used:
file_read, code_run, file_patch
Errors / blockers:
- ModuleNotFoundError: ...
</earlier_summary>
```

`repair_tool_pairs()` 用来避免 OpenAI 400：

```text
assistant tool_use 没有对应 user tool_result -> 移除断裂 tool_use
孤立 user tool_result -> 降级成普通 text
```

### 10. 协议转换

入口在 `chrysalis/llm/protocols.py`。

内部 history 始终是 canonical 格式，发给模型前才转换。

OpenAI wire format：

```python
[
    {"role": "system", "content": "..."},
    {"role": "user", "content": "读取 README.md"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [...]
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "..."
    }
]
```

Anthropic wire format：

```python
[
    {
        "role": "user",
        "content": [{"type": "text", "text": "读取 README.md"}]
    },
    {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_1", "name": "file_read", "input": {...}}]
    },
    {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "..."}]
    }
]
```

这样可以让内部 history 与具体模型协议解耦。

### 11. 工具注册与执行

入口在 `chrysalis/tools/registry.py`。

工具定义：

```python
@tool("file_read", "读取文件", {"path": "文件路径"})
def file_read(args: dict, workspace=None) -> dict:
    ...
```

注册后进入全局 `_REGISTRY`：

```python
_REGISTRY[name] = ToolDef(...)
```

执行工具：

```python
run_tool(name, args, workspace)
```

生成 function calling schema：

```python
generate_tools_schema()
```

### 12. TUI 事件流

入口在：

```text
chrysalis/tui/app.py
chrysalis/tui/bridge.py
```

TUI 创建：

```python
ChrysalisApp()
  -> AgentBridge(self)
    -> Kernel(progress=...)
```

Bridge 把回调接到 AgentLoop：

```python
self.kernel.loop.on_stream_chunk = self._on_stream_chunk
self.kernel.loop.on_tool_call = self._on_tool_call
```

运行中事件：

```text
StreamChunk        模型流式输出
ToolCallStarted    工具开始
ToolCallCompleted  工具完成
FileDiff           文件变更 diff
AgentDone          最终结果
StatusChange       状态变化
```

### 13. 目录结构

```text
chrysalis/
  kernel.py            CLI 入口 + Kernel 装配
  agent_loop.py        观察-行动循环
  context_engine.py    长期记忆动态组装
  working.py           任务内短期工作记忆
  observation.py       工具观察结果压缩
  session_store.py     canonical history 持久化
  session.py           轻量 session context 兼容层
  subagent.py          并行子 agent
  task_queue.py        任务队列
  compress_session.py  L4 会话归档
  llm/                 streaming、failover、history compact、协议转换
  tools/               工具注册和工具实现
  tui/                 Textual 终端 UI

configs/
  config.py            路径、环境变量、模型配置加载
  llm_models.json      多模型配置

memory/
  global_mem_insight.txt
  global_mem.txt
  *.md                 SOP 和长期记忆

data/
  sessions/            会话历史
  model_responses/     原始 LLM 日志
  l4_session/          历史会话归档
  usage_history.jsonl  token/费用记录

workspace/
  工具默认工作目录
```

### 14. 设计原则

- OpenClaw 风格 Context Engine 做长期记忆主干。
- Claude Code 风格 runtime compact 做上下文续航。
- Function calling 优先，JSON-in-text 保留为 fallback。
- canonical history 与 OpenAI/Anthropic 协议解耦。
- 工具尽量少而通用，复杂任务通过工具组合完成。
- 记忆写入遵守 `No Execution, No Memory`。
- 不伪造 tool result；断裂 tool 协议块会被修复或降级。

![img_1.png](assets/images/img_1.png)

## Messaging Gateway

QQ、个人微信和飞书可以通过统一网关接入 Chrysalis：

```bash
pip install -e ".[gateway]"

chrysalis-gateway qq
chrysalis-gateway qq-personal
chrysalis-gateway onebot
chrysalis-gateway wechat
chrysalis-gateway feishu
chrysalis-gateway qq-personal wechat feishu
chrysalis-gateway qq-personal --shared-groups
```

`qq` 是 QQ 开放平台官方 Bot，群聊通常需要按平台规则添加机器人并通过 @ 触发；如果你想把“一个 QQ 号”拉进普通群聊，请用 `qq-personal` / `onebot`，它连接 NapCat、Lagrange 等 OneBot v11 WebSocket 实现。

`feishu` / `lark` 是飞书自建应用机器人，通过事件订阅长连接收消息，通过飞书 OpenAPI 发送文本、图片和文件。

环境变量：

```text
CHRYSALIS_GATEWAY_ALLOWED_TOOLS=

CHRYSALIS_QQ_APP_ID=
CHRYSALIS_QQ_APP_SECRET=
CHRYSALIS_QQ_ALLOWED_USERS=
CHRYSALIS_QQ_ALLOW_ALL=false

CHRYSALIS_ONEBOT_WS_URL=ws://127.0.0.1:3001
CHRYSALIS_ONEBOT_ACCESS_TOKEN=
CHRYSALIS_ONEBOT_ALLOWED_USERS=
CHRYSALIS_ONEBOT_ALLOWED_GROUPS=
CHRYSALIS_ONEBOT_ALLOW_ALL=false
CHRYSALIS_ONEBOT_REQUIRE_MENTION=true
CHRYSALIS_ONEBOT_REPLY_WITH_MENTION=true
CHRYSALIS_ONEBOT_TRIGGER_PREFIXES=

CHRYSALIS_WECHAT_ALLOWED_USERS=
CHRYSALIS_WECHAT_ALLOW_ALL=false
CHRYSALIS_WECHAT_TOKEN_FILE=

CHRYSALIS_FEISHU_APP_ID=
CHRYSALIS_FEISHU_APP_SECRET=
CHRYSALIS_FEISHU_VERIFICATION_TOKEN=
CHRYSALIS_FEISHU_ENCRYPT_KEY=
CHRYSALIS_FEISHU_BOT_OPEN_ID=
CHRYSALIS_FEISHU_ALLOWED_USERS=
CHRYSALIS_FEISHU_ALLOWED_CHATS=
CHRYSALIS_FEISHU_ALLOW_ALL=false
CHRYSALIS_FEISHU_REQUIRE_MENTION=true
CHRYSALIS_FEISHU_TRIGGER_PREFIXES=
CHRYSALIS_FEISHU_API_BASE=https://open.feishu.cn/open-apis
```

`*_ALLOWED_USERS` 用英文逗号分隔；留空表示开放访问。QQ群聊和飞书群聊默认按“群 + 发送者”隔离会话，`--shared-groups` 会改成整个群共享一个会话。

网关会话默认按远程不可信输入处理：模型在 QQ、微信、飞书里只会看到少量内部控制工具，不能直接读写本机文件、运行代码、截图、执行浏览器 JS 或派生子 Agent；远程聊天用户也不能批准本机权限弹窗。`CHRYSALIS_GATEWAY_ALLOWED_TOOLS` 可以用英文逗号额外开放工具名，`*` 会暴露全部工具名，但凡需要本机权限确认的动作仍会被远程网关拒绝。入站附件只接受网关缓存目录里的文件；结果里的 `[FILE:...]` 只会发送 `workspace` 或 `data/gateway` 下真实存在的文件，避免把任意本机路径发到群聊。

个人 QQ 群聊推荐流程：

1. 用一个专门的 QQ 小号登录 NapCat / Lagrange，并启用 OneBot v11 WebSocket 服务，例如 `ws://127.0.0.1:3001`。
2. 把这个 QQ 小号拉进群。
3. 运行 `chrysalis connect qq-personal`，或让 agent 调用 `gateway_connect`，platform 填 `qq-personal` / `onebot`。
4. 群里默认需要 @ 这个 QQ 号才会触发；也可以设置 `CHRYSALIS_ONEBOT_TRIGGER_PREFIXES=!`，用 `!帮我总结一下` 这种前缀触发。

个人微信首次启动会弹出二维码登录，token 默认保存到 `data/gateway/wechat_personal_token.json`。QQ、微信和飞书都支持接收文字、图片和附件；agent 结果里的 `[FILE:...]` 会尽量按平台能力原生回传，其中 QQ 和飞书本地文件会走平台上传流程，失败时回退为文件路径文本。

飞书推荐流程：

1. 在飞书开放平台创建自建应用，启用机器人能力。
2. 在事件订阅里开启长连接，并订阅 `im.message.receive_v1`。
3. 给应用开通发送消息、接收消息、上传/下载图片或文件所需权限，并发布到企业。
4. 在 `.env` 填入 `CHRYSALIS_FEISHU_APP_ID`、`CHRYSALIS_FEISHU_APP_SECRET`，有校验/加密时再填 token 和 encrypt key。
5. 运行 `chrysalis connect feishu`，或让 agent 调用 `gateway_connect`，platform 填 `feishu` / `lark`。

只想接某个平台时，直接运行：

```bash
chrysalis connect wechat
chrysalis connect feishu
```

如果是让 agent 自己去做，这个项目里还有 `gateway_connect` 工具。

网关内可用命令：

```text
/help
/status
/stop
/new 或 /reset
/session
/session new
```
