# 任务说明：任务断点续跑（Checkpoint Resume）

> 本文件是给独立实现者（另一个会话）的自包含说明。读完即可动手，不需要额外上下文。

## 目标

任务被中断（用户点终止 / `cancel`）后，能从中断点**继续**，而不是从头重来。复用现有 `pending_user_action` 的「中断 → 存状态 → 续跑」范式。

## 当前现状（已勘察）

- `chrysalis/agent_loop.py`
  - `AgentLoop.cancel()`（约 169 行）：只 `self._cancel_event.set()` + `self.llm.cancel()`，循环检测到后返回 `self._cancelled_result()` → `{"ok": False, "cancelled": True, "final": "任务已中断"}`。**没有保存任何进度**。
  - `self.working`（`WorkingMemory`）、`self._tool_trace`（list[dict]）、循环内的 `turn` 是中断时的核心状态。
  - `run()` 开头 `self.working.reset()`、`self._tool_trace = []` —— 续跑时**不能**无脑 reset，要能恢复。
- `chrysalis/working.py`
  - `WorkingMemory` 有 `to_dict()`（约 99 行）和 `state_snapshot()`（约 419 行），但**没有 `restore()` / `from_dict()`**。需要新增反序列化。
- `chrysalis/kernel.py`
  - `run()`（约 111 行）：调 `_resolve_pending_user_action(task)` 判断是否为续跑。`pending_user_action` 是 dict，存 `{task, question, reason, result}`。
  - `_resolve_pending_user_action()`（约 194 行）是现成的「续跑分发」范式，**仿照它做 checkpoint 续跑**。
  - `self.session_store`（`SessionStore`，`data/sessions/`）可用来存 checkpoint 文件。
- `chrysalis/electron_runtime/`（注意：已从单文件拆成包）
  - `tasks.py`：`_cancel_session_task`、`_task_worker`、`_run_task`、`_resolve_pending_user_action`。
  - `__init__.py`（core）：`_emit_event`、`_bind_task_callbacks`、`_on_*` 回调。

## 实现步骤

### 1. WorkingMemory 序列化/恢复（`chrysalis/working.py`）
- 已有 `to_dict()`，确认它覆盖全部字段（key_info、related_sop、todos、plan、long_term_update_requested、round 计数等）。若有遗漏字段补上。
- 新增 `@classmethod from_dict(cls, data: dict) -> WorkingMemory` 或 `restore(self, data: dict) -> None`，把 `to_dict()` 的结果完整还原（含 TodoItem/PlanItem 的反序列化，参考它们已有的 `from_value`）。

### 2. AgentLoop 存/取 checkpoint（`chrysalis/agent_loop.py`）
- 新增字段 `self._resume_state: dict | None = None`。
- `cancel()` 时构造 checkpoint dict：
  ```python
  {
    "working": self.working.to_dict(),
    "tool_trace": self._tool_trace,
    "history_info": self.history_info,   # 轻量文本历史
    "turn": <当前 turn，需在循环里记录到 self._current_turn>,
  }
  ```
  通过一个新回调 `self.on_checkpoint(checkpoint)` 交给上层持久化（或直接返回到 `_cancelled_result` 里带上 `"checkpoint": ...`）。**推荐**：放进 `_cancelled_result()` 返回值，让 Kernel 负责落盘，AgentLoop 不碰文件系统（保持职责单一）。
- `run()` 支持入参 `resume: dict | None = None`：若有，则 `self.working.restore(resume["working"])`、`self._tool_trace = resume["tool_trace"]`、`self.history_info` 扩展，并在上下文里注入「已完成步骤/已验证事实」摘要（用 `context_engine.session_anchor` 或自定义），从中断点继续而非重来。**注意**：`run()` 开头的 `self.working.reset()` 要在 resume 分支跳过。

### 3. Kernel 落盘 + 续跑分发（`chrysalis/kernel.py`）
- checkpoint 文件路径：`self.config.data_dir / "sessions" / <session_id> / "checkpoint.json"`（或 SessionStore 提供存取方法）。
- `run()` 拿到 `result.get("cancelled")` 且带 `checkpoint` 时，落盘。
- `run()` 开头检测到该 session 有 checkpoint 文件 + 本次 task 是「继续」信号时，读出来传给 `loop.run(resume=...)`，成功后删除 checkpoint 文件。
- 「继续」信号设计：可仿照 `pending_user_action`——新增 `self.resume_state` 字段，由 electron_runtime 在用户点「继续」时通过新命令注入。

### 4. 桌面端入口（`chrysalis/electron_runtime/tasks.py` + 前端）
- Python 侧：
  - `_cancel_session_task` 后，若 kernel 产生了 checkpoint，`_emit_event("task_resumable", session_id=..., task_id=...)`。
  - 新增命令 `resume_task`（在 `__init__.py` 的 `_handle_command` 分发表里加 `elif kind == "resume_task": self._resume_task(command)`），仿照 `_run_task` 起线程，但走续跑路径。
- 前端（`desktop-electron/src/App.tsx` + `electron/preload.ts` + `types.ts`）：
  - preload 暴露 `resumeTask(sessionId)`。
  - App 收到 `task_resumable` 事件后，在任务卡片/主按钮区显示「继续」入口，点击调 `resumeTask`。

## 验收

- 新增 `tests/test_checkpoint_resume.py`：
  - WorkingMemory `to_dict` → `from_dict` 往返相等。
  - 模拟 cancel 产生 checkpoint，再 resume，working/tool_trace 被正确恢复。
  - checkpoint 文件落盘与续跑后删除。
- `pytest` 全绿。
- 桌面端实测：跑长任务→终止→点继续→从断点继续。

## 坑 / 注意

- **上下文重建正确性是本任务最敏感处**：续跑时注入的「已完成事实」要让模型知道哪些步骤别重做，否则会重复执行有副作用的工具（写文件、跑命令）。务必充分测试。
- 续跑 UX 细节（按钮位置、是否自动续跑）实现前最好与用户确认。
- electron_runtime 现在是**包**，路径是 `chrysalis/electron_runtime/<module>.py`，命令分发在 `__init__.py` 的 `_handle_command`。
- 不要在 AgentLoop 里直接读写文件，落盘交给 Kernel/SessionStore。
