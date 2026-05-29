"""Chrysalis 运行时上下文压缩模块。

这个模块负责在一次会话越来越长时，把旧消息分层压缩，尽量让模型还能继续同一个任务。
整体设计是为了提高 prompt cache 命中率：越靠前、越稳定的内容越少被改写。

分层模型：

    A = 原始 history，没有被压缩过的消息
    B = micro 压缩后的消息，主要裁剪旧工具输出、大图、长 thinking、重复结果等
    C = snip 压缩消息，把一段稳定的中间历史合并成一条摘要
    D = full 压缩消息，把最新 D 之后的一组 C 合并成更高层摘要

关键策略：
- 旧 D 不会被重新总结，后续只把“最新 D 后面的 C”做成新的 D。
- 每次压缩都保护开头少量消息和最新尾部消息，避免当前任务状态丢失。
- 如果压缩后仍超过硬限制，才会逐步丢弃最老的 compact 块或最老 turn。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from configs.config import PROJECT_ROOT
from chrysalis.llm.types import Response, SessionConfig

_MICROCOMPACT_KEY = "_microcompact"
_FULL_COMPACT_KEY = "_full_compact"
_COMPACT_LEVEL_KEY = "_compact_level"
_SNIP_COMPACT_KEY = "_snip_compact"

_LEVEL_RAW = "raw"
_LEVEL_MICRO = "micro"
_LEVEL_SNIP = "snip"
_LEVEL_FULL = "full"

_CHARS_PER_TOKEN = 2
_DEFAULT_SOFT_RATIO = 0.70
_DEFAULT_HARD_RATIO = 0.90
_RECENT_TURNS_TO_KEEP = 2
_REACTIVE_KEEP_RECENT_TURNS = 5
_DEFAULT_TAIL_TOKEN_RATIO = 0.20
_SNIP_HEAD_MESSAGES = 2
_MAX_MESSAGES_BEFORE_SNIP = 96
_TOOL_RESULT_BUDGET = 200_000
_COMPACT_MAX_FAILURES = 3
_ARCHIVE_PREVIEW_CHARS = 1800
_MAX_TOOL_ARG_CHARS = 500
_MAX_TEXT_CHARS = 1200
_MIN_TOOL_RESULT_PRUNE_CHARS = 200
_FULL_MIN_SNIP_BLOCKS = 2

_HIGH_OUTPUT_TOOL_NAMES = {"file_read", "code_run", "web_scan", "web_execute_js", "screenshot", "ocr"}
_PROTECTED_TOOL_NAMES = {"spawn_subagent"}

COMPACT_SYSTEM_PROMPT = """You compact an agent conversation for continuation.
Return plain text only. Do not call tools.
Preserve: user goal, important paths/files/commands, tool names and outcomes, errors and fixes,
current work, unfinished tasks, and the next best step.
Keep identifiers exact. Do not invent details."""


@dataclass
class CompactionStats:
    """记录一次压缩执行的结果，用于调试、日志和上层判断。"""

    tool_results_archived: int = 0
    snip_compacted: bool = False
    micro_compacted: bool = False
    full_compacted: bool = False
    llm_full_compacted: bool = False
    reactive_compacted: bool = False
    transcript_path: str = ""
    failures: int = 0
    before_chars: int = 0
    after_chars: int = 0
    notes: list[str] = field(default_factory=list)


class CompactionManager:
    """上下文压缩管理器，负责按 soft/hard 边界组织 A/B/C/D 分层压缩。"""

    def __init__(
        self,
        config: SessionConfig,
        *,
        output_dir: Path | None = None,
        transcript_dir: Path | None = None,
    ) -> None:
        """初始化压缩管理器。

        config 提供上下文窗口、压缩开关、阈值等配置。
        output_dir 用于归档过长工具输出；transcript_dir 用于保存 reactive 压缩前的完整历史。
        """
        self.config = config
        self.output_dir = output_dir or PROJECT_ROOT / "data" / "task_outputs" / "tool_results"
        self.transcript_dir = transcript_dir or PROJECT_ROOT / "data" / "transcripts"
        self.failures = 0
        self.last_stats = CompactionStats()

    @property
    def budget_chars(self) -> int:
        """把模型 context_window 粗略换算成字符预算。"""
        return max(1, self.config.context_window * _CHARS_PER_TOKEN)

    @property
    def soft_budget_chars(self) -> int:
        """soft 边界：超过它就主动开始压缩，但还不算失败。"""
        ratio = getattr(self.config, "compression_soft_limit_ratio", _DEFAULT_SOFT_RATIO)
        return max(1, int(self.budget_chars * ratio))

    @property
    def hard_budget_chars(self) -> int:
        """hard 边界：超过它说明必须更激进地压缩或丢弃旧内容。"""
        ratio = getattr(self.config, "compression_hard_limit_ratio", _DEFAULT_HARD_RATIO)
        return max(1, int(self.budget_chars * ratio))

    @property
    def compression_enabled(self) -> bool:
        """读取配置中的压缩总开关。"""
        return bool(getattr(self.config, "compression_enabled", True))

    @property
    def full_compact_available(self) -> bool:
        """判断 full/LLM summary 是否还能尝试，避免连续失败后每轮重复浪费。"""
        max_failures = getattr(self.config, "compression_max_failures", _COMPACT_MAX_FAILURES)
        return self.failures < max_failures

    @property
    def tail_token_budget(self) -> int:
        """micro 压缩时用于保护尾部上下文的 token 预算。"""
        configured = getattr(self.config, "compression_tail_token_budget", None)
        if configured is not None and configured > 0:
            return int(configured)
        return max(1, int(self.config.context_window * _DEFAULT_TAIL_TOKEN_RATIO))

    def compress_context(
        self,
        history: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        reason: str = "preflight",
    ) -> CompactionStats:
        """兼容旧调用名，实际委托给 apply_preflight。"""
        return self.apply_preflight(history, system=system, tools=tools, reason=reason)

    def apply_preflight(
        self,
        history: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        reason: str = "preflight",
    ) -> CompactionStats:
        """模型调用前的主压缩流程。

        运行顺序：
        1. 先修复 tool_use/tool_result 配对，避免压缩后协议不合法。
        2. 未超过 soft 边界时不压缩，最大化缓存命中。
        3. 超过 soft 后依次尝试工具输出归档、micro、snip、full。
        4. 如果仍超过 hard 边界，才开始丢弃最旧 compact 块/turn。
        """
        stats = CompactionStats(
            before_chars=estimate_request_cost(history, system=system, tools=tools),
            failures=self.failures,
        )
        repair_tool_pairs(history)

        if not self.compression_enabled:
            # 压缩关闭时只记录统计，不改动 history。
            stats.after_chars = estimate_request_cost(history, system=system, tools=tools)
            self.last_stats = stats
            return stats

        if estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars:
            # 第一步先把“当前最后一条 user 消息里的大工具结果”落盘，避免它直接撑爆请求。
            stats.tool_results_archived += self.apply_tool_result_budget(history)

        if estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars:
            # 第二步做 B 层 micro：保留最近尾部，裁剪更旧的工具输出和重内容。
            microcompact_history(history,keep_recent=_RECENT_TURNS_TO_KEEP,protect_tail_tokens=self.tail_token_budget)
            stats.micro_compacted = True
            repair_tool_pairs(history)

        if estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars:
            # 第三步如果还超 soft，把稳定中间段压成一个 C。
            snip_compact_history(history, keep_recent_turns=_RECENT_TURNS_TO_KEEP, max_messages=0)
            stats.snip_compacted = True
            repair_tool_pairs(history)

            # 第四步如果还超 soft，把最新 D 后面的 C 合成新的 D；旧 D 不会被改写。
        if estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars and self.full_compact_available:
            if full_compact_history(history,target_chars=self.soft_budget_chars,keep_recent_turns=_RECENT_TURNS_TO_KEEP):
                stats.full_compacted = True
                self.failures = 0
            else:
                self.failures += 1
                stats.notes.append(f"full compact found no eligible C blocks ({reason})")
            repair_tool_pairs(history)

        # 最后的保险丝：超过 hard 时，牺牲最旧内容来保证请求能发出去。
        while len(history) > 3 and estimate_request_cost(history, system=system, tools=tools) > self.hard_budget_chars:
            drop_oldest_turn(history)
            repair_tool_pairs(history)

        stats.failures = self.failures
        stats.after_chars = estimate_request_cost(history, system=system, tools=tools)
        self.last_stats = stats
        return stats

    def should_try_llm_summary(
        self,
        history: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
    ) -> bool:
        """判断是否需要让模型生成 full summary。

        只有压缩开启、full 失败次数没超过上限，并且当前请求仍超过 soft 边界时返回 True。
        """
        if not self.compression_enabled or not self.full_compact_available:
            return False
        return estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars

    def build_llm_summary_request(self, history: list[dict]) -> list[dict]:
        """构造给 LLM summary 子调用的消息。

        优先总结最新 D 后面的 C 块；如果没有合适 C 块，就退化为总结受保护尾部之前的旧内容。
        返回值是一个独立的 user 消息列表，供 session.py 里的 summary 调用使用。
        """
        start, end = _find_full_compact_window(history, _RECENT_TURNS_TO_KEEP)
        if start >= end:
            start = _find_latest_full_index(history) + 1
            end = _protected_tail_start(history, _RECENT_TURNS_TO_KEEP)
        compact_input = _history_digest(history[start:end])
        prompt = (
            "Compact only the provided Chrysalis conversation segment into a stable continuation summary.\n"
            "Do not mention that other summaries may exist before or after it.\n"
            "Use headings when useful: User Goal, Key Files and Paths, Tool Work, Errors and Fixes, "
            "Current State, Unfinished Tasks, Next Step.\n\n"
            f"{compact_input}"
        )
        return [{"role": "user", "blocks": [{"type": "text", "text": prompt}]}]

    def apply_llm_summary(self, history: list[dict], summary: str) -> bool:
        """把 LLM 生成的 summary 应用回 history，形成新的 D 块。"""
        summary = summary.strip()
        if not summary:
            self.failures += 1
            self.last_stats.failures = self.failures
            return False

        ok = full_compact_history(
            history,
            target_chars=self.soft_budget_chars,
            keep_recent_turns=_RECENT_TURNS_TO_KEEP,
            summary_text=_wrap_earlier_summary(summary),
        )
        if ok:
            self.failures = 0
            self.last_stats.llm_full_compacted = True
        else:
            self.failures += 1
        self.last_stats.failures = self.failures
        return ok

    def mark_llm_summary_failed(self) -> None:
        """记录一次 LLM summary 失败，用于失败熔断。"""
        self.failures += 1
        self.last_stats.failures = self.failures

    def apply_reactive_compact(self, history: list[dict], reason: str = "context_limit_error") -> CompactionStats:
        """收到 context limit 错误后的应急压缩流程。

        这个流程比 preflight 更激进：先保存完整 transcript，再强制归档/压缩，
        目标是尽快把请求压回可发送范围，而不是追求最优缓存。
        """
        stats = CompactionStats(
            before_chars=estimate_request_cost(history),
            failures=self.failures,
            reactive_compacted=True,
        )
        stats.transcript_path = self.save_transcript(history, reason=reason)
        stats.tool_results_archived += self.apply_tool_result_budget(history, force=True)

        # reactive 模式保护更短的尾部，并允许 force 压缩当前可压缩内容。
        microcompact_history(history, keep_recent=_REACTIVE_KEEP_RECENT_TURNS, force=True)
        snip_compact_history(history, keep_recent_turns=_REACTIVE_KEEP_RECENT_TURNS, max_messages=12)
        full_compact_history(
            history,
            target_chars=max(1, int(self.soft_budget_chars * 0.75)),
            keep_recent_turns=_REACTIVE_KEEP_RECENT_TURNS,
        )
        repair_tool_pairs(history)

        # 如果仍然过大，按硬限制的 75% 继续丢弃最旧内容，保证重试成功率。
        while len(history) > 3 and estimate_request_cost(history) > max(1, int(self.hard_budget_chars * 0.75)):
            drop_oldest_turn(history)
            repair_tool_pairs(history)

        stats.after_chars = estimate_request_cost(history)
        self.last_stats = stats
        return stats

    def apply_tool_result_budget(self, history: list[dict], force: bool = False) -> int:
        """控制最后一条 user 消息中的 tool_result 总量。

        很多模型协议会把工具结果作为 user blocks 放回最后一条消息。
        如果这批结果太大，就把完整内容写入文件，并在上下文里只保留路径和预览。
        返回归档的 tool_result 数量。
        """
        msg = _last_user_message(history)
        if not msg:
            return 0
        blocks = msg.get("blocks")
        if not isinstance(blocks, list):
            return 0

        tool_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        total = sum(len(str(b.get("content", ""))) for b in tool_blocks)
        if not force and total <= _TOOL_RESULT_BUDGET:
            return 0

        names = _tool_name_by_id(history)
        archived = 0
        threshold = max(4000, _TOOL_RESULT_BUDGET // max(1, len(tool_blocks)))
        for block in tool_blocks:
            content = str(block.get("content", ""))
            if block.get("_archived_path"):
                continue
            if not force and len(content) <= threshold:
                continue
            path = self._archive_tool_result(block, content)
            block["_archived_path"] = str(path)
            block["content"] = _archived_tool_preview(
                content=content,
                path=path,
                tool_use_id=str(block.get("tool_use_id", "")),
                tool_name=names.get(str(block.get("tool_use_id", "")), "unknown"),
            )
            block[_COMPACT_LEVEL_KEY] = _LEVEL_MICRO
            archived += 1
        return archived

    def _archive_tool_result(self, block: dict, content: str) -> Path:
        """把完整工具输出写入磁盘，返回归档文件路径。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tool_use_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(block.get("tool_use_id", "tool")))[:48]
        path = self.output_dir / f"{ts}_{tool_use_id}_{uuid.uuid4().hex[:8]}.txt"
        path.write_text(content, encoding="utf-8", errors="replace")
        return path

    def save_transcript(self, history: list[dict], reason: str = "compact") -> str:
        """保存完整会话历史，用于 reactive 压缩后的人工排查或恢复。"""
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.transcript_dir / f"{ts}_{reason}_{uuid.uuid4().hex[:8]}.json"
        payload = {
            "reason": reason,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "history": history,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        return str(path)


def trim_messages_history(history: list[dict], context_window: int) -> None:
    """旧版兼容入口：按 context_window 对 history 做一次完整裁剪。"""
    budget = max(1, context_window * _CHARS_PER_TOKEN)
    soft_budget = max(1, int(budget * _DEFAULT_SOFT_RATIO))
    repair_tool_pairs(history)
    if estimate_context_cost(history) > soft_budget:
        microcompact_history(history)
        snip_compact_history(history)
        full_compact_history(history, target_chars=soft_budget)
        repair_tool_pairs(history)
    while len(history) > 3 and estimate_context_cost(history) > budget:
        drop_oldest_turn(history)
        repair_tool_pairs(history)


def compress_history_tags(
    history: list[dict],
    keep_recent: int = 10,
    max_len: int = _MAX_TEXT_CHARS,
    force: bool = False,
) -> None:
    """旧版兼容入口：只触发 micro 压缩中的标签/长文本裁剪。"""
    microcompact_history(history, keep_recent=keep_recent, max_text_tag=max_len, force=force)


def microcompact_history(
    history: list[dict],
    keep_recent: int = 10,
    max_text_tag: int = _MAX_TEXT_CHARS,
    force: bool = False,
    protect_tail_tokens: int | None = None,
) -> None:
    """创建 B 层：裁剪旧工具输出和旧重内容。

    micro 是最轻量、最 cache 友好的压缩：
    - 不移动消息位置，不合并多条消息。
    - 优先裁剪旧 tool_result、重复工具输出、长 tool_use 参数、thinking、大图、可压缩标签。
    - 通过 keep_recent + protect_tail_tokens 保护最新尾部上下文。
    """

    repair_tool_pairs(history)
    # protect_from 之前的消息允许 micro；protect_from 之后属于尾部保护区，默认不动。
    protect_from = 0 if force else _micro_prune_boundary(history, keep_recent, protect_tail_tokens)
    tool_by_id = _tool_name_by_id(history, include_args=True)

    # 从后往前记录工具输出 hash：更旧的重复内容会被替换成一句提示。
    seen_tool_hashes: set[str] = set()
    for msg in reversed(history):
        for block in msg.get("blocks", []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            content = block.get("content")
            if not isinstance(content, str) or len(content) < _MIN_TOOL_RESULT_PRUNE_CHARS:
                continue
            digest = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:12]
            if digest in seen_tool_hashes and not block.get("_duplicate_pruned"):
                block["content"] = "[Duplicate tool output: same content as a more recent call]"
                block["_duplicate_pruned"] = True
                block[_COMPACT_LEVEL_KEY] = _LEVEL_MICRO
            seen_tool_hashes.add(digest)

    protected_ids = _tool_result_ids_for_tools(history, _PROTECTED_TOOL_NAMES)
    for idx, msg in enumerate(history):
        if idx >= protect_from and not force:
            continue
        if msg.get(_COMPACT_LEVEL_KEY) in {_LEVEL_SNIP, _LEVEL_FULL}:
            # C/D 已经是摘要，不再进入 micro，避免摘要被二次裁剪得越来越薄。
            continue

        msg[_MICROCOMPACT_KEY] = True
        msg[_COMPACT_LEVEL_KEY] = _LEVEL_MICRO
        for block in msg.get("blocks", []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                tool_use_id = str(block.get("tool_use_id", ""))
                if tool_use_id in protected_ids or block.get("_archived_path"):
                    # 受保护工具或已落盘内容不再剪，避免破坏重要结果和归档指针。
                    continue
                content = block.get("content")
                if isinstance(content, str) and len(content) > _MIN_TOOL_RESULT_PRUNE_CHARS:
                    tool_name, tool_args = tool_by_id.get(tool_use_id, ("unknown", ""))
                    summary = _summarize_tool_result(tool_name, tool_args, content)
                    if summary != content:
                        block["content"] = summary
                        block[_COMPACT_LEVEL_KEY] = _LEVEL_MICRO
            elif btype == "tool_use":
                name = str(block.get("name", ""))
                args = str(block.get("arguments", ""))
                if name not in _PROTECTED_TOOL_NAMES and len(args) > _MAX_TOOL_ARG_CHARS:
                    block["arguments"] = _truncate_tool_args(args, _MAX_TOOL_ARG_CHARS)
            elif btype == "thinking":
                text = str(block.get("text", ""))
                if len(text) > _MAX_TEXT_CHARS:
                    block["text"] = _truncate_text(text, _MAX_TEXT_CHARS)
            elif btype == "image":
                # 图片 block 通常非常占上下文，micro 阶段直接替换成文本占位。
                block.clear()
                block.update({"type": "text", "text": "[image omitted by microcompact]"})
            elif btype == "text":
                text = str(block.get("text", ""))
                if len(text) > max_text_tag and _has_compressible_tags(text):
                    block["text"] = _truncate_text(text, max_text_tag)


def snip_compact_history(
    history: list[dict],
    keep_recent_turns: int = _RECENT_TURNS_TO_KEEP,
    max_messages: int = _MAX_MESSAGES_BEFORE_SNIP,
) -> None:
    """创建 C 层：把一段稳定的中间 A/B 消息合并成一条 snip 摘要。

    snip 保护开头少量消息和最近 keep_recent_turns 轮，只处理中间段。
    它总是从最新 compact 块之后开始，避免反复改写旧 C/D。
    """

    if len(history) <= max_messages:
        return

    head_end = min(_SNIP_HEAD_MESSAGES, len(history))
    tail_start = _protected_tail_start(history, keep_recent_turns)
    if tail_start <= head_end:
        return

    start = _find_latest_compact_index(history, {_LEVEL_SNIP, _LEVEL_FULL}) + 1
    start = max(start, head_end)
    end = tail_start
    if start >= end:
        return

    segment = history[start:end]
    if not any(_compact_level(msg) in {_LEVEL_RAW, _LEVEL_MICRO} for msg in segment):
        # 如果窗口里已经没有 A/B，就没有新的内容可生成 C。
        return

    summary = _summarize_messages(segment, title="Conversation segment was snip-compacted.")
    c_msg = _summary_message(summary, _LEVEL_SNIP)
    c_msg[_SNIP_COMPACT_KEY] = True
    history[:] = history[:start] + [c_msg] + history[end:]



def full_compact_history(
    history: list[dict],
    target_chars: int,
    keep_recent_turns: int = _RECENT_TURNS_TO_KEEP,
    summary_text: str | None = None,
) -> bool:
    """创建 D 层：把“最新 D 后面的 C 块”合并成新的 full 摘要。

    这里故意不重新总结旧 D。这样前缀中的 D1、D2 等块会保持稳定，
    后续只追加/替换最新 D 之后的新 C，prompt cache 更容易命中。
    """

    start, end = _find_full_compact_window(history, keep_recent_turns)
    if start >= end:
        return False

    segment = history[start:end]
    if summary_text is None and sum(1 for msg in segment if _compact_level(msg) == _LEVEL_SNIP) < _FULL_MIN_SNIP_BLOCKS:
        # 内置摘要至少需要多个 C 才值得升成 D；LLM summary 传入时不受这个限制。
        return False

    summary = summary_text or _summarize_messages(segment, title="Snip summaries were full-compacted.")
    d_msg = _summary_message(_wrap_earlier_summary(summary), _LEVEL_FULL)
    d_msg[_FULL_COMPACT_KEY] = True
    history[:] = history[:start] + [d_msg] + history[end:]
    repair_tool_pairs(history)
    return True


def drop_oldest_turn(history: list[dict]) -> None:
    """丢弃最旧内容。

    优先丢最旧 D，再丢最旧 C；如果没有 compact 块，就丢最早的一轮普通对话。
    这是 hard 边界后的最后保险，不是常规压缩路径。
    """

    if not history:
        return

    for i, msg in enumerate(history):
        if _compact_level(msg) == _LEVEL_FULL:
            del history[i]
            return
    for i, msg in enumerate(history):
        if _compact_level(msg) == _LEVEL_SNIP:
            del history[i]
            return

    end = _first_turn_end(history)
    del history[:end]


def repair_tool_pairs(history: list[dict]) -> None:
    """修复 tool_use/tool_result 配对，保证压缩后消息协议仍合法。

    assistant 里的 tool_use 必须在下一条 user 消息里有对应 tool_result。
    如果配对缺失，就删除孤立 tool_use；如果 user 里有孤立 tool_result，就转成普通 text。
    """
    for i, msg in enumerate(history):
        role = msg.get("role")
        blocks = msg.get("blocks")
        if not isinstance(blocks, list):
            continue

        if role == "assistant":
            tool_ids = _tool_use_ids(msg)
            if not tool_ids:
                continue
            next_msg = history[i + 1] if i + 1 < len(history) else None
            if next_msg is None or next_msg.get("role") != "user":
                continue
            next_ids = _tool_result_ids(next_msg)
            _strip_tool_uses(msg, tool_ids - next_ids)
        elif role == "user":
            prev_msg = history[i - 1] if i > 0 else None
            valid_ids = _tool_use_ids(prev_msg) if prev_msg else set()
            _convert_orphan_tool_results(msg, valid_ids)

    history[:] = [msg for msg in history if _has_blocks(msg)]


def _find_full_compact_window(history: list[dict], keep_recent_turns: int) -> tuple[int, int]:
    """寻找可以升成 D 的 C 块窗口。

    只搜索最新 D 之后、受保护尾部之前的 C 块，返回 [start, end)。
    """
    tail_start = _protected_tail_start(history, keep_recent_turns)
    latest_d = _find_latest_full_index(history)
    start = latest_d + 1

    first_c = None
    last_c = None
    for i in range(start, tail_start):
        if _compact_level(history[i]) == _LEVEL_SNIP:
            if first_c is None:
                first_c = i
            last_c = i

    if first_c is None or last_c is None:
        return 0, 0
    return first_c, last_c + 1


def _protected_tail_start(history: list[dict], keep_recent_turns: int) -> int:
    """返回最近 keep_recent_turns 个真实 user turn 的起点索引。

    这个索引之后的内容属于尾部保护区，snip/full 默认不会碰。
    只有 tool_result 的 user 消息不算真实用户 turn。
    """
    user_indices = [
        i for i, msg in enumerate(history)
        if msg.get("role") == "user" and not _only_tool_results(msg) and _compact_level(msg) != _LEVEL_FULL
    ]
    if len(user_indices) <= keep_recent_turns:
        return 0
    return user_indices[-keep_recent_turns]


def _micro_prune_boundary(
    history: list[dict],
    protect_tail_count: int,
    protect_tail_tokens: int | None = None,
) -> int:
    """计算 micro 可以开始保护尾部的位置。

    count_boundary 负责“至少保护最近 N 个 user turn”。
    protect_tail_tokens 负责“如果最近内容很重，就按 token 预算保护更多消息”。
    返回值越小，受保护尾部越大；micro 只会处理该索引之前的旧消息。
    """
    if not history:
        return 0
    count_boundary = _protected_tail_start(history, protect_tail_count)
    if protect_tail_tokens is None or protect_tail_tokens <= 0:
        return count_boundary

    accumulated = 0
    boundary = len(history)
    min_protect_start = count_boundary
    for i in range(len(history) - 1, -1, -1):
        msg_tokens = _message_token_estimate(history[i])
        # 只有当 token 预算已经覆盖到最小保护区以外时，才允许停止回溯。
        if accumulated + msg_tokens > protect_tail_tokens and i <= min_protect_start:
            boundary = i + 1
            break
        accumulated += msg_tokens
        boundary = i

    return min(boundary, min_protect_start)


def _message_token_estimate(msg: dict) -> int:
    """粗略估算单条消息 token 数，用于尾部保护预算。"""
    tokens = len(json.dumps(msg, ensure_ascii=False, default=str)) // _CHARS_PER_TOKEN + 10
    for block in msg.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            tokens += len(str(block.get("arguments", ""))) // _CHARS_PER_TOKEN
    return max(1, tokens)


def _find_latest_full_index(history: list[dict]) -> int:
    """返回最新 D 消息的索引；不存在时返回 -1。"""
    return _find_latest_compact_index(history, {_LEVEL_FULL})


def _find_latest_compact_index(history: list[dict], levels: set[str]) -> int:
    """从后往前查找指定 compact level 的最新消息索引。"""
    for i in range(len(history) - 1, -1, -1):
        if _compact_level(history[i]) in levels:
            return i
    return -1


def _summary_message(summary: str, level: str) -> dict:
    """把摘要文本包装成一条带 compact level 标记的 user 消息。"""
    msg = {
        "role": "user",
        "blocks": [{"type": "text", "text": summary}],
        _COMPACT_LEVEL_KEY: level,
    }
    if level == _LEVEL_MICRO:
        msg[_MICROCOMPACT_KEY] = True
    elif level == _LEVEL_SNIP:
        msg[_SNIP_COMPACT_KEY] = True
    elif level == _LEVEL_FULL:
        msg[_FULL_COMPACT_KEY] = True
    return msg


def _compact_level(msg: dict) -> str:
    """读取消息压缩层级，兼容旧标记字段。"""
    level = msg.get(_COMPACT_LEVEL_KEY)
    if level:
        return str(level)
    if msg.get(_FULL_COMPACT_KEY):
        return _LEVEL_FULL
    if msg.get(_SNIP_COMPACT_KEY):
        return _LEVEL_SNIP
    if msg.get(_MICROCOMPACT_KEY):
        return _LEVEL_MICRO
    return _LEVEL_RAW


def _first_turn_end(history: list[dict]) -> int:
    """找到第一轮普通对话的结束位置，用于丢弃最老 turn。"""
    if len(history) <= 1:
        return len(history)
    for i in range(1, len(history)):
        if history[i].get("role") == "user" and not _only_tool_results(history[i]):
            return i
    return min(len(history), 1)


def _summarize_tool_result(tool_name: str, tool_args: str, content: str) -> str:
    """把旧工具结果压成一行摘要，保留工具名、关键参数和错误线索。"""
    detail = _tool_arg_hint(tool_name, tool_args)
    line_count = content.count("\n") + 1 if content else 0
    chars = len(content)
    exit_hint = ""
    lowered = content.lower()
    if "exit code" in lowered or "returncode" in lowered:
        exit_hint = " exit status mentioned."
    elif any(mark in lowered for mark in ("traceback", "error", "exception", "failed")):
        exit_hint = " errors mentioned."
    return f"[{tool_name}] {detail} -> pruned old tool output ({chars} chars, {line_count} lines).{exit_hint}"


def _tool_arg_hint(tool_name: str, tool_args: str) -> str:
    """从工具参数里提取最有恢复价值的线索，例如 path、command、url。"""
    try:
        parsed = json.loads(tool_args) if isinstance(tool_args, str) and tool_args.strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        for key in ("path", "file", "filename", "command", "cmd", "url"):
            value = parsed.get(key)
            if value:
                return f"{key}={_truncate_text(str(value), 120)}"
    if tool_args:
        return _truncate_text(str(tool_args), 120)
    return "ran"


def _truncate_tool_args(args: str, max_chars: int) -> str:
    """裁剪过长 tool_use 参数，优先保持 JSON 结构。"""
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError:
        return _truncate_text(args, max_chars)
    if isinstance(parsed, dict):
        changed = False
        for key, value in list(parsed.items()):
            if isinstance(value, str) and len(value) > max_chars:
                parsed[key] = _truncate_text(value, max_chars)
                changed = True
        if changed:
            return json.dumps(parsed, ensure_ascii=False)
    return _truncate_text(args, max_chars)


def _has_compressible_tags(text: str) -> bool:
    """判断文本里是否包含适合 micro 裁剪的结构化标签。"""
    return any(tag in text for tag in ("<thinking>", "<tool_result>", "<history>", "<earlier_summary>"))


def _summarize_messages(messages: list[dict], title: str | None = None) -> str:
    """把一组消息提炼成 earlier_summary 文本。

    摘要会保留最近关键 turn、标识符、工具名、文件路径和错误/阻塞信息。
    对 identifiers/tools/files/errors 使用“保留最新唯一项”，避免旧信息挤掉新状态。
    """
    facts: list[str] = []
    tool_names: list[str] = []
    identifiers: list[str] = []
    errors: list[str] = []
    recent_files: list[str] = []

    for msg in messages:
        role = msg.get("role", "?")
        level = _compact_level(msg)
        text = _message_text(msg)
        if text:
            # facts 只收短片段，避免摘要本身继续膨胀。
            facts.append(f"- {role}/{level}: {_truncate_text(text, 220)}")
            identifiers.extend(_extract_identifiers(text))
            recent_files.extend(_extract_file_paths(text))
            if _looks_error(text):
                errors.append(_truncate_text(text, 180))

        for block in msg.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", "")).strip()
                if name:
                    tool_names.append(name)
                identifiers.extend(_extract_identifiers(str(block.get("arguments", ""))))
                recent_files.extend(_extract_file_paths(str(block.get("arguments", ""))))
            elif block.get("type") == "tool_result":
                content = str(block.get("content", ""))
                identifiers.extend(_extract_identifiers(content))
                recent_files.extend(_extract_file_paths(content))
                if _looks_error(content):
                    errors.append(_truncate_text(content, 180))

    sections = [
        "<earlier_summary>",
        title or "Earlier conversation was compacted. Preserve these identifiers and decisions.",
    ]
    if facts:
        sections.append("Key turns:")
        # 只保留最近的关键 turn，因为当前状态通常比远古状态更重要。
        sections.extend(facts[-24:])

    unique_ids = _unique_keep_latest(identifiers, 40)
    if unique_ids:
        sections.append("Identifiers:")
        sections.append(", ".join(unique_ids))

    unique_tools = _unique_keep_latest(tool_names, 20)
    if unique_tools:
        sections.append("Tools used:")
        sections.append(", ".join(unique_tools))

    unique_files = _unique_keep_latest(recent_files, 5)
    if unique_files:
        sections.append("Recent files to restore by reading if needed:")
        sections.extend(f"- {item}" for item in unique_files)

    unique_errors = _unique_keep_latest(errors, 8)
    if unique_errors:
        sections.append("Errors / blockers:")
        sections.extend(f"- {item}" for item in unique_errors)

    sections.append("</earlier_summary>")
    return "\n".join(sections)


def _message_text(msg: dict) -> str:
    """提取一条消息中可用于摘要的文本内容。"""
    parts: list[str] = []
    for block in msg.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "tool_result":
            parts.append(str(block.get("content", "")))
    return "\n".join(part for part in parts if part).strip()


def _extract_identifiers(text: str) -> list[str]:
    """从文本中提取路径、命令、文件名和代码标识符。"""
    patterns = [
        r"[A-Za-z]:\\[^\s\"'<>|]+",
        r"(?:[\w.-]+/)+[\w.-]+",
        r"\b[\w.-]+\.(?:py|md|json|toml|txt|yaml|yml|js|ts|tsx|jsx|css|html)\b",
        r"\b(?:pytest|python|git|npm|uv|pip)\s+[^\n\r]{1,100}",
        r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text))
    return [str(item).strip(".,;:()[]{}") for item in found if str(item).strip()]


def _extract_file_paths(text: str) -> list[str]:
    """从文本中提取可能需要重新读取的文件路径。"""
    patterns = [
        r"[A-Za-z]:\\[^\s\"'<>|]+",
        r"(?:[\w.-]+/)+[\w.-]+\.(?:py|md|json|toml|txt|yaml|yml|js|ts|tsx|jsx|css|html)",
        r"\b[\w.-]+\.(?:py|md|json|toml|txt|yaml|yml|js|ts|tsx|jsx|css|html)\b",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text))
    return [str(item).strip(".,;:()[]{}") for item in found if str(item).strip()]


def _looks_error(text: str) -> bool:
    """用简单关键词判断一段文本是否包含错误/失败信息。"""
    lowered = text.lower()
    return any(marker in lowered for marker in ("error", "exception", "traceback", "failed"))


def _only_tool_results(msg: dict) -> bool:
    """判断 user 消息是否只包含 tool_result block。"""
    blocks = msg.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)


def _tool_use_ids(msg: dict | None) -> set[str]:
    """提取 assistant 消息中的 tool_use id 集合。"""
    if not msg or msg.get("role") != "assistant":
        return set()
    return {
        str(block.get("id", ""))
        for block in msg.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
    }


def _tool_result_ids(msg: dict | None) -> set[str]:
    """提取 user 消息中的 tool_result id 集合。"""
    if not msg or msg.get("role") != "user":
        return set()
    return {
        str(block.get("tool_use_id", ""))
        for block in msg.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
    }


def _strip_tool_uses(msg: dict, missing_ids: set[str]) -> None:
    """从 assistant 消息中删除没有对应结果的 tool_use。"""
    if not missing_ids:
        return
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    msg["blocks"] = [
        block for block in blocks
        if not (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") in missing_ids
        )
    ]


def _convert_orphan_tool_results(msg: dict, valid_ids: set[str]) -> None:
    """把没有对应 tool_use 的 tool_result 转成普通 text。"""
    blocks = msg.get("blocks")
    if not isinstance(blocks, list):
        return
    cleaned: list[dict] = []
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") not in valid_ids
        ):
            text = str(block.get("content", ""))
            if text:
                cleaned.append({"type": "text", "text": f"[orphaned tool result converted to text]\n{text[:400]}"})
            continue
        cleaned.append(block)
    msg["blocks"] = cleaned


def _has_blocks(msg: dict) -> bool:
    """判断消息是否还有可发送的 blocks；非 blocks 格式消息默认保留。"""
    blocks = msg.get("blocks")
    return not isinstance(blocks, list) or bool(blocks)


def _truncate_text(text: str, max_len: int) -> str:
    """中间截断文本，保留头尾，方便恢复上下文线索。"""
    if len(text) <= max_len:
        return text
    marker = "...[Truncated]..."
    keep = max(0, max_len - len(marker))
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:]


def _unique_keep_latest(items: list[str], limit: int) -> list[str]:
    """去重并保留最新出现的若干项，同时维持返回结果的时间顺序。"""
    seen: set[str] = set()
    latest: list[str] = []
    for item in reversed(items):
        if not item or item in seen:
            continue
        seen.add(item)
        latest.append(item)
        if len(latest) >= limit:
            break
    latest.reverse()
    return latest


def _unique_keep_order(items: list[str]) -> list[str]:
    """按首次出现顺序去重。当前主要作为兼容工具函数保留。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def estimate_context_cost(history: list[dict]) -> int:
    """估算 history 本身的字符成本。"""
    return sum(len(json.dumps(m, ensure_ascii=False, default=str)) for m in history)


def estimate_request_cost(
    history: list[dict],
    *,
    system: str = "",
    tools: list[dict] | None = None,
    reserve_output_tokens: int = 0,
) -> int:
    """估算一次完整请求的字符成本，包括 system、tools 和预留输出。"""
    cost = estimate_context_cost(history)
    if system:
        cost += len(system)
    if tools:
        cost += len(json.dumps(tools, ensure_ascii=False, default=str))
    if reserve_output_tokens > 0:
        cost += reserve_output_tokens * _CHARS_PER_TOKEN
    return cost


def _calc_cost(history: list[dict]) -> int:
    """旧测试/旧调用兼容别名。"""
    return estimate_context_cost(history)


def is_context_limit_error(response: Response | None) -> bool:
    """判断模型返回是否像 context window 超限错误。"""
    if response is None:
        return False
    text = f"{response.content}\n{response.raw}".lower()
    markers = (
        "prompt too long",
        "context length",
        "context window",
        "maximum context",
        "context exceeded",
        "input is too long",
        "too many tokens",
        "tokens exceed",
        "exceeds the model",
    )
    return any(marker in text for marker in markers)


def _last_user_message(history: list[dict]) -> dict | None:
    """返回最后一条 user 消息。"""
    for msg in reversed(history):
        if msg.get("role") == "user":
            return msg
    return None


def _tool_name_by_id(history: list[dict], include_args: bool = False) -> dict[str, Any]:
    """建立 tool_use_id 到工具名/参数的映射。"""
    names: dict[str, Any] = {}
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                tid = str(block.get("id"))
                name = str(block.get("name", ""))
                args = str(block.get("arguments", ""))
                names[tid] = (name, args) if include_args else name
    return names


def _tool_result_ids_for_tools(history: list[dict], tool_names: set[str]) -> set[str]:
    """找出指定工具名对应的 tool_use_id，用于保护特殊工具结果。"""
    names = _tool_name_by_id(history)
    return {tool_id for tool_id, name in names.items() if name in tool_names}


def _archived_tool_preview(content: str, path: Path, tool_use_id: str, tool_name: str) -> str:
    """生成工具结果落盘后的上下文预览文本。"""
    preview = _truncate_text(content, _ARCHIVE_PREVIEW_CHARS)
    return (
        "[tool_result archived by tool_result_budget]\n"
        f"tool_use_id: {tool_use_id}\n"
        f"tool_name: {tool_name}\n"
        f"archive_path: {path}\n"
        "To inspect the complete result, read archive_path with file_read.\n"
        "Preview:\n"
        f"{preview}"
    )


def _history_digest(history: list[dict]) -> str:
    """把一段 history 序列化成给 LLM summary 使用的截断输入。"""
    text = json.dumps(history, ensure_ascii=False, default=str)
    return _truncate_text(text, 60_000)


def _wrap_earlier_summary(summary: str) -> str:
    """确保 summary 被 earlier_summary 标签包裹。"""
    summary = summary.strip()
    if summary.startswith("<earlier_summary>"):
        return summary
    return f"<earlier_summary>\n{summary}\n</earlier_summary>"
