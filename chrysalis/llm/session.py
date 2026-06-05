"""LLM 会话管理：history 持久化、上下文裁剪、流式调用生命周期。
history 内部统一使用 canonical block 格式（见 chrysalis/llm/types.py），发送给 provider 时由 protocols 模块转换为协议特定的 wire format。"""

from __future__ import annotations

import json
import threading
from typing import Generator

from chrysalis.llm.claude_stream import claude_stream
from chrysalis.llm.context import (
    COMPACT_SYSTEM_PROMPT,
    CompactionManager,
    is_context_limit_error,
)
from chrysalis.llm.logger import write_llm_log
from chrysalis.llm.openai_stream import openai_stream
from chrysalis.llm.openai_responses_stream import openai_responses_stream
from chrysalis.llm.protocols import to_anthropic_messages, to_openai_messages, to_openai_responses_input
from chrysalis.llm.types import CancelledError, Response, SessionConfig


class BaseSession:
    """单个 LLM 会话。管理 history、上下文裁剪、协议分发。

    作用：作为核心调度器，维护多轮对话的上下文状态。保证线程安全的同时，
    处理大模型调用中最棘手的“上下文超长”问题，并在不同模型厂商（OpenAI/Anthropic）的协议之间做桥接。
    """

    def __init__(self, config: SessionConfig):
        """
        初始化会话实例。
        作用：加载配置，初始化标准格式的对话历史(history)、系统提示词、工具列表。
        同时引入了 CompactionManager（上下文压缩管理器），并配置了线程锁和取消事件以保证并发安全。
        """
        self.config = config
        self.history: list[dict] = []
        self.system: str = ""
        self.tools: list[dict] | None = None
        self.compaction = CompactionManager(config)
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def ask(self, message: dict, cancel_event: threading.Event | None = None) -> Generator[str, None, Response]:
        """
        向 LLM 发起对话的核心入口函数（流式生成）。

        作用：
        1. 将用户的新消息加入 history。
        2. 触发预检压缩（剔除无效/过早的历史记录）。
        3. 判断是否需要让 LLM 对早期历史进行“智能总结”，如果需要，则在后台发起一个独立的 LLM 调用来生成总结。
        4. 调用大模型获取回答（支持流式吐出字符串，并在结束时返回完整的 Response 对象）。
        5. 将 LLM 的最终回答转换为标准格式并追加到 history 中。
        """
        cancel = cancel_event or self._cancel_event

        # 1. 安全地追加用户消息，并进行发车前的“预检”上下文处理
        with self._lock:
            self.history.append(message)
            self.compaction.apply_preflight(self.history, system=self.system, tools=self.tools)

            # 判断是否需要对冗长的历史记录进行 LLM 智能总结
            llm_summary_request = (
                self.compaction.build_llm_summary_request(self.history)
                if self.compaction.should_try_llm_summary(self.history, system=self.system, tools=self.tools)
                else None
            )
            # 建立历史快照，防止在流式输出时 history 被其他线程修改
            history_snapshot = [dict(m) for m in self.history]

        # 2. 如果需要总结，则在此处执行“后台总结任务”
        if llm_summary_request:
            summary = self._run_compaction_summary(llm_summary_request, cancel)
            with self._lock:
                if summary:
                    self.compaction.apply_llm_summary(self.history, summary)
                else:
                    self.compaction.mark_llm_summary_failed()
                # 总结完成后，再次执行预检并更新快照
                self.compaction.apply_preflight(self.history, system=self.system, tools=self.tools)
                history_snapshot = [dict(m) for m in self.history]

        # 3. 发送给 LLM 并获取流式响应（带有被动防御机制）
        response = yield from self._ask_with_reactive_retry(history_snapshot, cancel)
        if response is None:
            response = Response(content="!!!Error: 未收到响应", raw="")

        # 4. 如果没有报错且没有被用户取消，将 AI 的回复加入历史
        if not response.is_error and not response.cancelled:
            self._append_assistant(response)

        return response

    def _raw_ask(self, history: list[dict], cancel_event: threading.Event | None = None) -> Generator[str, None, Response]:
        """
        使用当前会话默认的系统提示词和工具，发起底层调用。
        作用：这是一个便捷封装，把当前 Session 绑定的 system 和 tools 传给 _raw_ask_with_options。
        """
        return self._raw_ask_with_options(history, self.system, self.tools, cancel_event=cancel_event)

    def _raw_ask_with_options(
            self,
            history: list[dict],
            system: str,
            tools: list[dict] | None,
            cancel_event: threading.Event | None = None,
    ) -> Generator[str, None, Response]:
        """
        底层协议路由转发器。
        作用：根据配置中的 protocol 字段（如 anthropic 或 openai），将内部通用的 canonical history
        转换成对应厂商的特定消息格式（wire format），然后调用对应的底层流式客户端函数。
        """
        if self.config.protocol == "anthropic":
            messages = to_anthropic_messages(history)
            return claude_stream(self.config, messages, system, tools, cancel_event=cancel_event)

        if self.config.wire_api == "responses":
            input_items = to_openai_responses_input(history)
            return openai_responses_stream(self.config, input_items, system, tools, cancel_event=cancel_event)

        messages = to_openai_messages(history, system)
        return openai_stream(self.config, messages, "", tools, cancel_event=cancel_event)

    def _ask_with_reactive_retry(
            self,
            history_snapshot: list[dict],
            cancel: threading.Event,
    ) -> Generator[str, None, Response]:
        """
        带有“被动截断重试”机制的请求执行器。
        作用：尝试请求大模型。如果大模型直接抛出“上下文长度超限（Context Limit Exceeded）”错误，
        该函数会主动拦截错误，强制对历史记录进行深度压缩（reactive_compact），然后自动重试一次。
        这极大提升了长对话场景下的系统鲁棒性。
        """
        response = yield from self._stream_raw(history_snapshot, cancel)
        if response and response.cancelled:
            return response
        # 如果不是上下文超限错误（成功或者其他报错），直接返回
        if not is_context_limit_error(response):
            return response

        # 触发被动防御机制：发现上下文爆了，强制裁切历史记录
        with self._lock:
            self.compaction.apply_reactive_compact(self.history)
            retry_snapshot = [dict(m) for m in self.history]

        # 向前端流中插入一条系统提示，告知用户发生了自动压缩
        yield "\n[Chrysalis] 上下文过长，已自动压缩历史并重试一次。\n"

        # 使用压缩后的快照重试最后一次
        retry_response = yield from self._stream_raw(retry_snapshot, cancel)
        if is_context_limit_error(retry_response):
            # 如果依然超长，说明任务本身的单次文本量就已经超出模型极限了
            return Response(
                content="!!!Error: 上下文压缩后仍超过模型限制，请开启新会话或缩小任务范围。",
                raw=(retry_response.raw if retry_response else ""),
            )
        return retry_response

    def _stream_raw(
            self,
            history: list[dict],
            cancel: threading.Event,
    ) -> Generator[str, None, Response]:
        """
        最底层的流式数据泵（Data Pump）。
        作用：不断从底层 SDK 迭代获取 chunk 数据并 yield 吐给上层调用者。
        同时实时监听 cancel 取消事件，并在迭代结束（StopIteration）时捕获底层返回的完整 Response 对象。
        """
        gen = self._raw_ask(history, cancel)
        response: Response | None = None
        try:
            while True:
                chunk = next(gen)
                yield chunk
                # 每次吐字后检查是否被用户强行取消
                if cancel.is_set():
                    raise CancelledError()
        except CancelledError:
            response = Response(content="", raw="", stop_reason="cancelled", cancelled=True)
        except StopIteration as e:
            # 在 Python generator 中，return 的值会包含在 StopIteration 的 value 属性中
            response = e.value

        return response or Response(content="!!!Error: 未收到响应", raw="")

    def _run_compaction_summary(self, request: list[dict], cancel: threading.Event) -> str:
        """
        后台静默执行历史记录的 LLM 总结任务。
        作用：使用专门的 COMPACT_SYSTEM_PROMPT（压缩专用提示词）向 LLM 发起请求，
        消耗该生成器的所有流数据（不向前端 yield 输出，做到用户无感知静默执行），最终返回总结好的文本。
        """
        gen = self._raw_ask_with_options(request, COMPACT_SYSTEM_PROMPT, None, cancel_event=cancel)
        response: Response | None = None
        try:
            while True:
                next(gen) # 默默消耗流，不 yield 给前端
                if cancel.is_set():
                    raise CancelledError()
        except CancelledError:
            return ""
        except StopIteration as e:
            response = e.value

        if response is None or response.cancelled or response.is_error:
            return ""
        return response.content.strip()

    def _append_assistant(self, response: Response) -> None:
        """
        将 LLM 返回的完整响应写入本地历史记录中。
        作用：解析 Response 对象，将其还原拆分为标准化的块（blocks），例如：思考过程(thinking)、纯文本(text)、工具调用(tool_use)。
        确保内部存储的 history 始终保持严谨的 canonical block 格式。
        """
        blocks: list[dict] = []

        # 兼容深度思考模型（如 Claude 3.7 Sonnet 或 OpenAI O1）
        if response.thinking and response.thinking_signature:
            blocks.append({
                "type": "thinking",
                "text": response.thinking,
                "signature": response.thinking_signature,
            })

        # 普通文本回复
        if response.content:
            blocks.append({"type": "text", "text": response.content})

        # Agent 工具调用（Function Calling）
        for tc in response.tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            })

        if not blocks:
            return

        with self._lock:
            self.history.append({"role": "assistant", "blocks": blocks})

    def clear_history(self) -> None:
        """
        清空当前会话的历史记录。
        作用：安全地重置会话上下文，并复位取消状态，相当于页面上的“新建对话/清空对话”功能。
        """
        with self._lock:
            self.history.clear()
        self._cancel_event.clear()

    def cancel(self) -> None:
        """
        中断当前正在生成的 LLM 请求。
        作用：向线程事件发送信号。所有涉及网络通信的流式循环一旦检测到此信号，就会立即抛出 CancelledError 中止生成。
        """
        self._cancel_event.set()
