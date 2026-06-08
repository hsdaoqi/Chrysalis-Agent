"""多模型 Failover：主模型失败时自动切换备用模型。"""

import time
from typing import Generator

from chrysalis.llm.session import BaseSession
from chrysalis.llm.types import Response


class FailoverSession(BaseSession):
    """包装多个 BaseSession，实现 round-robin failover。

    对外暴露与 BaseSession 相同的 ask() 接口，内部在多个 session 间轮询。
    主模型恢复后（spring_back 秒后）自动回切。
    """

    def __init__(self, sessions: list[BaseSession], spring_back: int = 300):
        if not sessions:
            raise ValueError("至少需要一个 session")
        self.sessions = sessions
        self._spring_back = spring_back  # 切换模型的秒
        self._current_idx = 0
        self._switched_at = 0.0
        self.config = sessions[0].config
        self._lock = __import__("threading").Lock()
        self._on_preflight_trace = None
        for s in self.sessions:
            s.config.max_retries = 3

    @property
    def on_preflight_trace(self):
        return self._on_preflight_trace

    @on_preflight_trace.setter
    def on_preflight_trace(self, value) -> None:
        self._on_preflight_trace = value
        for session in self.sessions:
            session.on_preflight_trace = value

    @property
    def system(self) -> str:
        return self.sessions[0].system

    @system.setter
    def system(self, value: str) -> None:
        for s in self.sessions:
            s.system = value

    @property
    def tools(self) -> list[dict] | None:
        return self.sessions[0].tools

    @tools.setter
    def tools(self, value: list[dict] | None) -> None:
        for s in self.sessions:
            s.tools = value

    @property
    def history(self) -> list[dict]:
        return self.sessions[self._current_idx].history

    @history.setter
    def history(self, value: list[dict]) -> None:
        for s in self.sessions:
            s.history = value

    def ask(self, message: dict, cancel_event=None) -> Generator[str, None, Response]:
        """轮询尝试各 session，成功则返回，全部失败则报错。"""
        start_idx = self._pick_start()
        last_error = ""

        for attempt in range(len(self.sessions)):
            idx = (start_idx + attempt) % len(self.sessions)
            session = self.sessions[idx]

            session.history = self.sessions[self._current_idx].history.copy()
            gen = session.ask(message, cancel_event=cancel_event)

            chunks: list[str] = []
            response: Response | None = None
            hit_error = False

            try:
                while True:
                    chunk = next(gen)
                    if chunk.startswith("!!!Error:"):
                        hit_error = True
                        last_error = chunk
                        break
                    chunks.append(chunk)
                    yield chunk
            except StopIteration as e:
                response = e.value

            if hit_error:
                continue

            if response is not None and response.cancelled:
                return response

            if response is None:
                last_error = "!!!Error: 未收到响应"
                continue

            if response.is_error:
                last_error = response.content
                continue

            if idx != self._current_idx:
                self._current_idx = idx
                self._switched_at = time.time()
            return response

        error_text = last_error or "!!!Error: 所有模型均不可用"
        yield error_text
        return Response(content=error_text, raw=error_text)

    def cancel(self) -> None:
        for session in self.sessions:
            if hasattr(session, "cancel"):
                session.cancel()

    def _pick_start(self) -> int:
        """
        如果当前已经是主模型 index 0：继续从主模型开始

        如果当前是备用模型：
            看距离切换时间是否超过 300 秒
            超过了，就回到主模型
            没超过，就继续用当前备用模型
        """
        if self._current_idx == 0:
            return 0
        if time.time() - self._switched_at > self._spring_back:
            self._current_idx = 0
            return 0
        return self._current_idx

    def clear_history(self) -> None:
        for s in self.sessions:
            s.clear_history()
