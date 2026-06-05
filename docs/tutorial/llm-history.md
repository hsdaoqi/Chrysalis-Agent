# LLM History

LLM History 是 Chrysalis 的会话主记录。它记录的不只是“用户说了什么、模型回了什么”，还包括：

- 用户输入文本。
- 模型最终回复。
- 模型思考块 `thinking`。
- 工具调用 `tool_use`。
- 工具结果 `tool_result`。
- 图片输入 `image`。

这份记录是后面所有能力的基础：会话回放、上下文压缩、桌面端历史展示、任务恢复，全都依赖它。

## 为什么不能直接用普通消息列表

不同模型服务的 message 格式不一样。OpenAI 和 Anthropic 的结构不一样，工具调用的字段也不一样。Chrysalis 先在内部统一成一种 canonical block 格式，再在发送给 provider 时转换成 wire format。

这样做的好处是：

1. 内部逻辑只维护一种历史结构。
2. 工具调用和工具结果能稳定配对。
3. CLI、TUI、桌面端看到的是同一份历史。

## canonical 结构长什么样

一个 user 消息通常是这样：

```json
{
  "role": "user",
  "blocks": [
    {"type": "text", "text": "请总结 README.md"},
    {"type": "tool_result", "tool_use_id": "call_1", "content": "...", "is_error": false}
  ]
}
```

一个 assistant 消息可能包含多个 block：

```json
{
  "role": "assistant",
  "blocks": [
    {"type": "thinking", "text": "...", "signature": "..."},
    {"type": "tool_use", "id": "call_1", "name": "file_read", "arguments": "{\"path\":\"README.md\"}"},
    {"type": "text", "text": "我已经读完了，下面是总结。"}
  ]
}
```

## 代码里是谁在维护它

这条链路里有三个关键位置：

| 文件 | 作用 |
| --- | --- |
| `chrysalis/llm/client.py` | 把简化消息合并为 canonical user message |
| `chrysalis/llm/session.py` | 维护 `history`、压缩和 assistant 回写 |
| `chrysalis/session_store.py` | 把 canonical history 保存到磁盘 |

### 1. `LLMClient._merge_user_message()`

`AgentLoop` 给 `LLMClient.chat()` 的消息很简单，但 `LLMClient` 会把它们整理成 canonical 结构。它会：

- 把普通文本放进 `text` block。
- 把 `images` 变成 `image` block。
- 把 `tool_results` 放到最前面，保证和上一轮 `tool_use` 对得上。

### 2. `BaseSession.ask()`

`BaseSession.ask()` 会：

1. 先把 user message 写进 `self.history`。
2. 调 `CompactionManager.apply_preflight()`。
3. 必要时执行 LLM summary。
4. 发起真实模型请求。
5. 把 assistant 响应写回 `history`。

### 3. `SessionStore.save()`

每轮任务结束后，`Kernel` 会通过 `on_history_changed` 触发 `SessionStore.save(history)`。保存文件是整个会话 JSON，不是片段。

## 会话文件长什么样

会话文件保存在：

```text
data/sessions/{session_id}.json
```

文件通常包含这些字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 会话 ID |
| `title` | 自动提取或手动重命名后的标题 |
| `custom_title` | 用户自定义标题 |
| `pinned` | 是否置顶 |
| `created_at` | 创建时间 |
| `updated_at` | 最近更新时间 |
| `model` | 当前会话使用的模型 |
| `turns` | 历史轮数 |
| `history` | canonical history |

一个最小会话文件大概会长这样：

```json
{
  "id": "20260531_120000_abcd",
  "title": "请总结 README.md",
  "custom_title": "",
  "pinned": false,
  "created_at": "2026-05-31T12:00:00",
  "updated_at": "2026-05-31T12:05:00",
  "model": "deepseek-v4-pro",
  "turns": 6,
  "history": [...]
}
```

## 一次任务里历史是怎么增长的

把流程拆开看会更清楚：

1. 用户输入一句任务。
2. `Kernel.run()` 把它交给 `AgentLoop.run()`。
3. `AgentLoop` 先追加一条 `[USER]` 的轻量历史锚点。
4. `LLMClient.chat()` 把这轮用户输入合并进 canonical history。
5. 如果模型要调用工具，assistant history 会记录 `tool_use`。
6. 工具结果回到模型后，user history 会记录 `tool_result`。
7. 模型最终给出 `text`，assistant history 记录最终答复。

也就是说，history 不是只有“问答”，它记录的是完整的行动轨迹。

## 为什么 tool_result 要放在前面

`LLMClient._merge_user_message()` 会把 `tool_result` 排在 `text` 前面。原因很实际：

- 模型会用上一轮 `tool_use` 的 id 去匹配 `tool_result`。
- 如果配对不稳，协议容易断。
- 先放结果，再放文本，最不容易出错。

## 加载会话时发生什么

`Kernel.load_session(session_id)` 会：

1. 读取 `data/sessions/{session_id}.json`。
2. 把 `history` 塞回 `self.llm.session.history`。
3. 清空 `Kernel.history` 和 `pending_user_action`。

桌面端和 TUI 里的会话切换，本质上也是做这件事，只是 UI 表达不同。

## 如果你想手工读懂一份会话文件

建议从这几个角度看：

1. 用户到底提了什么任务。
2. 模型什么时候调用了工具。
3. 工具结果里有什么错误或边界条件。
4. 最终答案是否真的解决了任务。
5. 如果有压缩，哪些内容被折叠了。

这比单看最终回答更接近真实执行过程。

## 和上下文压缩的关系

LLM History 是原始记录，但模型每次调用时不会无脑把整份历史都发过去。`BaseSession.ask()` 会在必要时调用 `CompactionManager.apply_preflight()`，如果还是太长，再做 LLM summary 或 reactive compact。

所以：

- `history` 是保存。
- `context` 是当前发给模型的有效输入。

## 和工作记忆的关系

LLM History 记录“已经发生过什么”。工作记忆记录“当前还要做什么”。两者一起看，才知道一个任务是怎么从开始走到结束的。

## 你在代码里最常会改的地方

| 目标 | 位置 |
| --- | --- |
| 改消息格式 | `chrysalis/llm/types.py` 和 `chrysalis/llm/client.py` |
| 改历史写回逻辑 | `chrysalis/llm/session.py::_append_assistant()` |
| 改会话保存字段 | `chrysalis/session_store.py::save()` |
| 改会话标题提取 | `chrysalis/session_store.py::_extract_title()` |
| 改会话列表排序 | `chrysalis/session_store.py::_session_sort_key()` |

一句话总结：**LLM History 是 Chrysalis 的事实底座，其他层都是在它上面做更短、更聪明、更省上下文的表达。**
