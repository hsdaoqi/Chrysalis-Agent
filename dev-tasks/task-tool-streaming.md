# 任务说明：工具执行流式化（Tool Streaming）

> 本文件是给独立实现者（另一个会话）的自包含说明。读完即可动手。

## 目标

工具执行期间把进度/输出**边跑边回传**到前端，而不是跑完一次性返回。典型场景：`code_run` 跑长命令时，stdout 逐行刷新到 UI。

## 当前现状（已勘察）

- **模型文字回答已是真流式**：`chrysalis/llm/session.py` 的 `claude_stream`/`openai_stream` 逐 chunk yield，`agent_loop.py` 的 `on_stream_chunk` 一路传到 `electron_runtime` 的 `_on_stream_chunk` → `_emit_event("stream", ...)`。**这条链路是现成的范本，照抄它做工具流。**
- **工具执行是阻塞的**：
  - `chrysalis/tools/code_tools.py` 的 `code_run` 用 `subprocess.run(capture_output=True)`（约 67 行 python、108 行 shell），跑完才返回 `{"ok":..., "stdout":...}`。
  - `chrysalis/agent_loop.py` 里工具调用：`_execute_tool_with_guards` → `run_tool(tool_name, args, self.workspace)`（来自 `chrysalis/tools/registry.py`）。只有 `tool_started`/`tool_completed` 两个离散 trace 事件（`_emit_trace` + `on_tool_call`）。
  - 回调字段在 `AgentLoop.__init__`：`on_stream_chunk`、`on_tool_call`、`on_thinking`、`on_trace_event` 等（约 36-41 行）。
- **electron_runtime（已拆成包）**：
  - `__init__.py`（core）：`_on_stream_chunk`（约 `_emit_event("stream", ...)`）、`_on_tool_call`、`_bind_task_callbacks`、`_emit_event`。
  - 前端事件消费在 `desktop-electron/src/App.tsx`（搜 `'stream'`、`'tool_started'`、`'tool_completed'` 的 RuntimeEvent 处理）。

## 实现步骤

### 1. code_run 改流式（`chrysalis/tools/code_tools.py`）
- `subprocess.run(...)` 改 `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True)`，逐行读：
  ```python
  proc = subprocess.Popen(cmd, cwd=..., stdout=PIPE, stderr=STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
  for line in proc.stdout:
      if on_stream: on_stream(line)
      buf.append(line)
  proc.wait(timeout=timeout)  # 注意超时处理：Popen 没有 run 的 timeout，需自己用 communicate(timeout) 或线程看门狗
  ```
- 保留原有返回结构（`stdout`/`stderr`/`exit_code`/`ok`）不变，只是额外通过 `on_stream` 回调实时吐行。
- **超时**：`subprocess.run` 的 `timeout` 行为要保留——用 `proc.communicate(timeout=...)` 或 `proc.wait(timeout=...)` + 超时后 `proc.kill()`，返回原来的超时错误结构。
- 安全检查（`DANGEROUS_CODE_PATTERNS`、`blocked_shell_pattern`）保持不变。

### 2. 工具如何拿到 on_stream 回调（`chrysalis/tools/registry.py` + `agent_loop.py`）
- 工具函数签名目前是 `func(args: dict, workspace: Path | None)`。要让部分工具能接收 stream 回调，方案二选一：
  - **方案 A（推荐，侵入小）**：`run_tool` 增加可选参数 `on_stream=None`，仅当工具函数签名声明了 `on_stream` 参数时透传（用 `inspect.signature` 判断，参考 `agent_loop._chat_accepts_turn` 的做法）。
  - 方案 B：在 `@tool` 装饰器加 `streaming=True` 标记。
- `AgentLoop.__init__` 增加回调 `on_tool_stream: Callable[[str, dict, str], None] | None = None`（参数：tool_name, args, chunk）。
- `_execute_tool_with_guards` 调 `run_tool` 时，构造 `on_stream = lambda chunk: self.on_tool_stream(tool_name, args, chunk)`（若 `self.on_tool_stream` 存在）传下去。

### 3. electron_runtime 转发（`chrysalis/electron_runtime/`）
- core（`__init__.py`）新增 `_on_tool_stream(self, session_id, task_id, tool, args, chunk)`：`self._emit_event("tool_stream", session_id=..., task_id=..., tool=tool, content=chunk, turn=<当前 tool_turn>)`。照抄 `_on_stream_chunk` 的写法。
- `_bind_task_callbacks`（也在 core）加一行：`kernel.loop.on_tool_stream = lambda tool, args, chunk: self._on_tool_stream(session_id, task_id, tool, args, chunk)`。
- `_bind_callbacks`（空绑定）也加 `self.kernel.loop.on_tool_stream = lambda *a: None`。
- Kernel 需要把 `on_tool_stream` 透传给 `AgentLoop`：检查 `chrysalis/kernel.py` 里 `AgentLoop(...)` 的构造（约 79 行），它没传所有回调——回调是 electron_runtime 直接 set 到 `kernel.loop.on_tool_stream` 的，所以 Kernel 侧只要 AgentLoop 有这个字段即可，无需改 Kernel。

### 4. 前端显示（`desktop-electron/src/App.tsx` + `types.ts`）
- `types.ts`：`RuntimeEvent` 加 `tool_stream` 事件类型。
- App：在工具卡片（搜 `tool_started`/`tool_completed` 的渲染）里，监听 `tool_stream` 事件，把 chunk 追加到对应 turn 的工具卡片输出区，实时刷新。

## 验收

- 新增 `tests/test_code_run_streaming.py`：
  - `code_run` 跑一段多行输出的脚本，断言 `on_stream` 回调被多次调用、收到的行拼起来等于最终 stdout。
  - 超时行为仍正确（跑一个 sleep 超时的命令，返回超时错误）。
  - 不传 `on_stream` 时行为与原来完全一致（向后兼容）。
- `pytest` 全绿。
- 桌面端实测：跑 `code_run` 长命令，UI 看到 stdout 逐行刷新。

## 坑 / 注意

- **向后兼容**：没声明 `on_stream` 的工具、没设 `on_tool_stream` 回调时，行为必须与现在完全一致。
- **超时**：`Popen` 不像 `run` 自带 timeout，必须手动实现，否则长命令永久阻塞。
- **编码**：保留原有 `errors="replace"` + 多编码兜底逻辑（`_decode_process_output`）。流式逐行时也要处理编码。
- electron_runtime 现在是**包**：core 回调在 `__init__.py`，事件转发照抄 `_on_stream_chunk`。
- `_emit_event` 是线程安全的（有 `_output_lock`），工具在 worker 线程跑，直接调没问题。
