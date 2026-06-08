# 任务说明：增强子 Agent（Subagent Enhancements）

> 本文件是给独立实现者（另一个会话）的自包含说明。读完即可动手。

## 目标

在现有子 Agent 并行执行基础上增强三点：
1. **进度/输出实时回传前端**：并行子任务的进度、工具调用能在桌面端实时看到，而不是只在父任务结束后看到汇总。
2. **并发数可配**：`max_workers` 从配置/设置读取，而非写死 4。
3. **失败隔离 + 结构化汇总**：单个子任务失败不影响其他，返回 per-task 的结构化状态。

## 当前现状（已勘察）

- `chrysalis/subagent.py`（核心，全文已读）：
  - 模块级状态：`_session_config`、`_progress`、`_executor`（`ThreadPoolExecutor`）、`_subagent_threads`（防嵌套派生）。
  - `configure(session_config, progress=None, max_workers=4)`：由 `kernel.py` 调用（约 93 行 `subagent.configure(...)`）。
  - `run_tasks(tasks, workspace)`：单任务直跑，多任务用 `_executor.submit` + `as_completed` 并行，阻塞到全部完成，返回 `{"ok": True, "results": [...]}`。
  - `_run_subagent`：每个子任务 `create_client` + 新建 `AgentLoop`（`max_turns=10`，工具集 `exclude={"spawn_subagent"}`，独立上下文），跑 `loop.run(task, session_context=context)`。
  - 失败已被 catch：`as_completed` 循环里 `except Exception` → `{"ok": False, "error": ...}`。**所以失败隔离已部分存在**，要增强为更结构化（带 task index、task 描述、状态）。
  - 进度回调 `child_progress` 目前只是 `_progress(f"[子任务] {msg}")` —— **只走父任务的 progress 文本，不带子任务身份，前端无法区分是哪个子任务**。
- `chrysalis/tools/agent_tools.py`：`spawn_subagent` 工具（约 92 行）调 `run_tasks`。
- `chrysalis/kernel.py`：约 93 行 `subagent.configure(session_config=..., progress=self.progress)` —— max_workers 没传，用默认 4。
- `chrysalis/agent_loop.py`：`AgentLoop` 支持 `progress`、`on_tool_call`、`on_trace_event` 等回调 —— 子 AgentLoop 目前只接了 `progress`。
- electron_runtime（已拆成包）：`__init__.py` 的 `_on_progress` / `_emit_event` / `_emit_trace_node`。

## 实现步骤

### 1. 并发数可配（`chrysalis/subagent.py` + `kernel.py` + 配置）
- `configs/config.py`：加配置项 `subagent_max_workers`（默认 4），可来自 `.env`（`CHRYSALIS_SUBAGENT_MAX_WORKERS`）或桌面端设置。
- `kernel.py` 约 93 行：`subagent.configure(..., max_workers=self.config.subagent_max_workers)`。
- 桌面端设置（可选）：在 `desktop_settings.json` schema 和设置页加这个项（参考 electron_runtime 的 `_normalize_llm_settings` / settings.py mixin）。

### 2. 进度/事件实时回传（`chrysalis/subagent.py`）
- `configure` 增加回调参数：`on_subagent_event=None`（签名 `(event: dict) -> None`），由 kernel/electron_runtime 注入。
- `_run_subagent` 给子 `AgentLoop` 接上回调，但要带**子任务身份**（task index / 描述）：
  ```python
  idx = ...  # 子任务序号
  loop = AgentLoop(
      llm=child_llm, workspace=..., max_turns=SUBAGENT_MAX_TURNS,
      progress=lambda msg: _emit_sub("progress", idx, message=msg),
      on_tool_call=lambda tool, args, obs: _emit_sub("tool", idx, tool=tool, ...),
      ...
  )
  ```
  其中 `_emit_sub(kind, idx, **payload)` 调 `on_subagent_event({"sub_index": idx, "task": <desc>, "kind": kind, **payload})`。
- **线程安全**：子任务在线程池里跑，`on_subagent_event` 会被多线程并发调用。回调实现侧（electron_runtime）的 `_emit_event` 已有 `_output_lock`，安全。但 subagent.py 里若有共享可变状态，需加锁。

### 3. 失败隔离 + 结构化汇总（`chrysalis/subagent.py`）
- `run_tasks` 的每个 result 统一结构：
  ```python
  {"index": i, "task": <desc>, "ok": bool, "result": <final> or None, "error": <msg> or None}
  ```
- 单任务异常已 catch，确保异常**不冒泡**、不影响其他 future（现状基本满足，补齐 index/task 字段）。
- 返回值加汇总：`{"ok": True, "results": [...], "summary": {"total": n, "succeeded": x, "failed": y}}`。
- `spawn_subagent` 工具返回值相应带上结构化结果（前端/模型都能用）。

### 4. electron_runtime 转发（`chrysalis/electron_runtime/`）
- kernel 把 `on_subagent_event` 接到 electron_runtime 的新方法 `_on_subagent_event(session_id, task_id, event)`：`self._emit_event("subagent", session_id=..., task_id=..., **event)`。
- 在 `_bind_task_callbacks`（core）里注入。注意：subagent 是模块级单例 `configure`，注入时机要对——可能需要在 `_run_task` 起任务时按 session 重新 `configure`，或让回调能路由到正确 session（用闭包捕获 session_id/task_id）。**这是本任务最 tricky 处**：subagent 是模块级全局，多 session 并发时回调要路由对。建议在 `_task_worker`/`_run_task` 里，针对该 task 的 kernel 重新 `subagent.configure(..., on_subagent_event=<带 session_id/task_id 闭包>)`。
- 前端（`App.tsx` + `types.ts`）：`subagent` 事件类型，UI 展示并行子任务的实时状态（可做成父任务卡片下的子任务列表）。

## 验收

- 新增/补充 `tests/test_subagent.py`：
  - 多任务并行，全部成功 → results 结构正确、summary 计数对。
  - 单任务抛异常 → 该任务 `ok=False` 带 error，其他任务不受影响。
  - 并发上限：传入 N > max_workers 个任务仍全部完成。
  - `on_subagent_event` 被调用，事件带 `sub_index`。
  - 嵌套派生仍被拒（`_subagent_threads` 逻辑不破坏）。
- `pytest` 全绿。
- 桌面端实测：触发 `spawn_subagent` 多任务，UI 实时看到各子任务进度。

## 坑 / 注意

- **多 session 并发 + 模块级 subagent 单例**：回调路由是最大风险点。务必让事件能带上正确的 session_id/task_id。
- **线程安全**：子任务并发回调，所有共享可变状态要么不可变要么加锁。
- **向后兼容**：`on_subagent_event=None` 时行为与现在一致；`configure` 新参数都给默认值。
- 不破坏防嵌套派生（`_subagent_threads`）和单任务直跑分支。
- electron_runtime 现在是**包**（`chrysalis/electron_runtime/*.py`），core 在 `__init__.py`。
