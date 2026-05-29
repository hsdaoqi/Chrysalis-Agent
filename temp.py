"""Runtime context compaction for Chrysalis."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from configs.config import PROJECT_ROOT
from chrysalis.llm.types import Response, SessionConfig

# ... (省略了上方的常量定义，保持原样) ...

@dataclass
class CompactionStats:
    """记录一次上下文压缩过程中发生的各种统计数据和结果状态。"""
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
    """Hermes-style runtime compaction pipeline for canonical history.
    上下文压缩管理器：用于在运行时对大模型的历史对话进行预检、强制瘦身、或利用LLM生成摘要。
    """

    def __init__(
            self,
            config: SessionConfig,
            *,
            output_dir: Path | None = None,
            transcript_dir: Path | None = None,
    ) -> None:
        """初始化压缩管理器，设定归档目录和转录本(备份)目录。"""
        self.config = config
        self.output_dir = output_dir or PROJECT_ROOT / "data" / "task_outputs" / "tool_results"
        self.transcript_dir = transcript_dir or PROJECT_ROOT / "data" / "transcripts"
        self.failures = 0
        self.last_stats = CompactionStats()

    @property
    def budget_chars(self) -> int:
        """计算绝对的最大字符预算（大致为上下文窗口 tokens 的 3 倍）。"""
        return max(1, self.config.context_window * 3)

    @property
    def soft_budget_chars(self) -> int:
        """计算软性预算（达到此阈值开始执行常规压缩，默认 70%）。"""
        ratio = getattr(self.config, "compression_soft_limit_ratio", _AUTOCOMPACT_PCT)
        return max(1, int(self.budget_chars * ratio))

    @property
    def hard_budget_chars(self) -> int:
        """计算硬性预算（达到此阈值开始暴力丢弃旧对话，默认 90%）。"""
        ratio = getattr(self.config, "compression_hard_limit_ratio", _HARD_LIMIT_RATIO)
        return max(1, int(self.budget_chars * ratio))

    @property
    def full_compact_available(self) -> bool:
        """检查全局压缩（Full Compact）是否仍然可用（未超过最大失败重试次数）。"""
        max_failures = getattr(self.config, "compression_max_failures", _COMPACT_MAX_FAILURES)
        return self.failures < max_failures

    @property
    def compression_enabled(self) -> bool:
        """检查配置中是否启用了上下文压缩。"""
        return bool(getattr(self.config, "compression_enabled", True))

    def compress_context(
            self,
            history: list[dict],
            *,
            system: str = "",
            tools: list[dict] | None = None,
            reason: str = "preflight",
    ) -> CompactionStats:
        """对外暴露的主入口：压缩上下文，底层直接调用 apply_preflight。"""
        return self.apply_preflight(history, system=system, tools=tools, reason=reason)

    def apply_preflight(
            self,
            history: list[dict],
            *,
            system: str = "",
            tools: list[dict] | None = None,
            reason: str = "preflight",
    ) -> CompactionStats:
        """执行发送请求前的“预检”和级联压缩逻辑：
        如果超预算，依次执行：工具归档 -> 裁剪中间对话(snip) -> 微压缩(micro) -> 摘要压缩(full) -> 暴力丢弃(drop)。
        """
        stats = CompactionStats(
            before_chars=estimate_request_cost(history, system=system, tools=tools),
            failures=self.failures,
        )
        repair_tool_pairs(history) # 修复任何断裂的工具调用配对

        if not self.compression_enabled:
            stats.after_chars = estimate_request_cost(history, system=system, tools=tools)
            self.last_stats = stats
            return stats

        request_cost = estimate_request_cost(history, system=system, tools=tools)
        if request_cost > self.soft_budget_chars:
            # 1. 把超大的工具输出写到本地硬盘，释放空间
            stats.tool_results_archived += self.apply_tool_result_budget(history)

            # 2. 尝试折叠长对话的中间部分
            if snip_compact_history(history, keep_recent_turns=_RECENT_TURNS_TO_KEEP):
                stats.snip_compacted = True

            # 3. 尝试进行微型压缩（截断巨长的参数或标签）
            before_micro = estimate_context_cost(history)
            microcompact_history(history, keep_recent=_RECENT_TURNS_TO_KEEP)
            stats.micro_compacted = estimate_context_cost(history) != before_micro
            repair_tool_pairs(history)

            # 4. 如果还是超标，尝试执行全文提取式压缩(生成结构化摘要)
            if estimate_request_cost(history, system=system,
                                     tools=tools) > self.soft_budget_chars and self.full_compact_available:
                if full_compact_history(
                        history,
                        target_chars=self.soft_budget_chars,
                        keep_recent_turns=_RECENT_TURNS_TO_KEEP,
                ):
                    stats.full_compacted = True
                    self.failures = 0
                else:
                    self.failures += 1
                    stats.notes.append(f"deterministic full compact produced no summary ({reason})")
                repair_tool_pairs(history)

            # 5. 如果达到了硬极限，只能无情地从头丢弃最老的对话轮次
            while len(history) > 4 and estimate_request_cost(history, system=system,
                                                             tools=tools) > self.hard_budget_chars:
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
        """判断当前长度是否值得新开一个大模型请求去专门生成上下文摘要。"""
        if not _AUTOCOMPACT_ENABLED or not self.full_compact_available:
            return False
        return estimate_request_cost(history, system=system, tools=tools) > self.soft_budget_chars

    def build_llm_summary_request(self, history: list[dict]) -> list[dict]:
        """构建要求 LLM 总结之前长对话的 Prompt 请求。"""
        split = _split_for_full_compact(history, _RECENT_TURNS_TO_KEEP)
        early = history[:split] if split > 0 else history[:-1]
        compact_input = _history_digest(early)
        prompt = (
            "Compact this earlier Chrysalis agent conversation into a continuation summary.\n"
            "Use these headings when helpful:\n"
            "User Goal, Key Files and Paths, Tool Work, Errors and Fixes, Current State, "
            "Unfinished Tasks, Next Step.\n\n"
            f"{compact_input}"
        )
        return [{"role": "user", "blocks": [{"type": "text", "text": prompt}]}]

    def apply_llm_summary(self, history: list[dict], summary: str) -> bool:
        """接收 LLM 生成的摘要文本，并将其替换到历史记录的开头，取代旧的臃肿对话。"""
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
        """记录一次 LLM 生成摘要失败，防止无限重试死循环。"""
        self.failures += 1
        self.last_stats.failures = self.failures

    def apply_reactive_compact(self, history: list[dict], reason: str = "context_limit_error") -> CompactionStats:
        """反应式（紧急）压缩：当大模型 API 直接报错“上下文超限(400 Context Exceeded)”时调用。
        使用更激进的裁剪参数，并强制备份当前的事故现场(transcript)。
        """
        stats = CompactionStats(
            before_chars=estimate_request_cost(history),
            failures=self.failures,
            reactive_compacted=True,
        )
        stats.transcript_path = self.save_transcript(history, reason=reason)
        stats.tool_results_archived += self.apply_tool_result_budget(history, force=True)

        snip_compact_history(
            history,
            keep_recent_turns=_REACTIVE_KEEP_RECENT_TURNS,
            max_messages=max(16, _REACTIVE_KEEP_RECENT_TURNS * 3),
        )
        full_compact_history(
            history,
            target_chars=max(1, int(self.soft_budget_chars * 0.75)),
            keep_recent_turns=_REACTIVE_KEEP_RECENT_TURNS,
        )
        microcompact_history(history, keep_recent=_REACTIVE_KEEP_RECENT_TURNS, force=True)
        repair_tool_pairs(history)

        while len(history) > 3 and estimate_request_cost(history) > max(1, int(self.hard_budget_chars * 0.75)):
            drop_oldest_turn(history)
            repair_tool_pairs(history)

        stats.after_chars = estimate_request_cost(history)
        self.last_stats = stats
        return stats

    def apply_tool_result_budget(self, history: list[dict], force: bool = False) -> int:
        """限制最近一次工具输出结果的体积。
        如果包含过大的文本(如读了几十万字的文件)，将其写入磁盘保存，
        在提示词中仅保留前 1800 字符的预览和读取该文件的提示。
        """
        msg = _last_user_message(history)
        if not msg:
            return 0

        blocks = msg.get("blocks")
        if not isinstance(blocks, list):
            return 0

        tool_blocks = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_result"]
        total = sum(len(str(block.get("content", ""))) for block in tool_blocks)
        if not force and total <= _TOOL_RESULT_BUDGET:
            return 0

        tool_names = _tool_name_by_id(history)
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
                tool_name=tool_names.get(str(block.get("tool_use_id", "")), "unknown"),
            )
            archived += 1
        return archived

    def _archive_tool_result(self, block: dict, content: str) -> Path:
        """内部方法：将超长的工具结果文本写入本地文件系统，防止挤爆内存/上下文。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tool_use_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(block.get("tool_use_id", "tool")))[:48]
        path = self.output_dir / f"{ts}_{tool_use_id}_{uuid.uuid4().hex[:8]}.txt"
        path.write_text(content, encoding="utf-8", errors="replace")
        return path

    def save_transcript(self, history: list[dict], reason: str = "compact") -> str:
        """将完整的历史记录(History)序列化保存到本地 JSON，作为“转录本(Transcript)”供排错使用。"""
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
    """原地修剪消息历史记录，保证在不超过窗口限制的同时不破坏工具协议（向后兼容的入口函数）。"""
    budget = max(1, context_window * 3)
    soft_budget = max(1, int(budget * _SOFT_LIMIT_RATIO))

    repair_tool_pairs(history)
    if estimate_context_cost(history) > soft_budget:
        snip_compact_history(history)
        microcompact_history(history)
        if estimate_context_cost(history) > soft_budget:
            full_compact_history(history, target_chars=soft_budget)
        repair_tool_pairs(history)

    while len(history) > 4 and estimate_context_cost(history) > budget:
        drop_oldest_turn(history)
        repair_tool_pairs(history)


def compress_history_tags(
        history: list[dict],
        keep_recent: int = 10,
        max_len: int = 800,
        force: bool = False,
) -> None:
    """向后兼容的独立函数包装器，用于直接调用微压缩 (microcompact)。"""
    microcompact_history(history, keep_recent=keep_recent, max_text_tag=max_len, force=force)


def microcompact_history(
        history: list[dict],
        keep_recent: int = 10,
        max_text_tag: int = _MAX_TEXT_TAG,
        force: bool = False,
) -> None:
    """微型压缩(Micro-compact)：缩小历史中体积庞大的特定块(比如截断过长的思考文本、剥离历史图片、截断长参数)，但不丢失对话轮次的整体结构。"""
    if len(history) <= keep_recent and not force:
        return

    candidates = history if force else history[:-keep_recent]
    high_output_ids = _tool_result_ids_for_tools(history, _HIGH_OUTPUT_TOOL_NAMES)
    protected_ids = _tool_result_ids_for_tools(history, _PROTECTED_TOOL_NAMES)

    for msg in candidates:
        if msg.get(_MICROCOMPACT_KEY) and not force:
            continue
        _microcompact_message(msg, max_text_tag, high_output_ids, protected_ids)
        msg[_MICROCOMPACT_KEY] = True


def snip_compact_history(
        history: list[dict],
        keep_recent_turns: int = _RECENT_TURNS_TO_KEEP,
        max_messages: int = _MAX_MESSAGES_BEFORE_SNIP,
) -> bool:
    """折叠压缩(Snip-compact)：保留对话的开头（设定与目标）和结尾（最近行为），将“中间长长的过程”全部剥离合并为一小段提示文本。"""
    if len(history) <= max_messages:
        return False

    split = _split_for_full_compact(history, keep_recent_turns)
    head = min(_SNIP_HEAD_MESSAGES, len(history))
    if split <= head:
        return False

    middle = history[head:split]
    if not middle:
        return False

    summary = _summarize_messages(middle, title="Middle conversation was snipped.")
    history[:] = history[:head] + [
        {
            "role": "user",
            "blocks": [{"type": "text", "text": summary}],
            _MICROCOMPACT_KEY: True,
            "_snip_compact": True,
        }
    ] + history[split:]
    repair_tool_pairs(history)
    return True


def full_compact_history(
        history: list[dict],
        target_chars: int,
        keep_recent_turns: int = _RECENT_TURNS_TO_KEEP,
        summary_text: str | None = None,
) -> bool:
    """全局压缩(Full-compact)：将指定数量（keep_recent_turns）之外的所有早期对话提取出核心事实、文件和错误，合并成一条包含结构化摘要的单一消息。"""
    split = _split_for_full_compact(history, keep_recent_turns)
    if split <= 0:
        return False

    early = history[:split]
    recent = history[split:]
    summary = summary_text or _summarize_messages(early)
    if not summary:
        return False

    summary_msg = {
        "role": "user",
        "blocks": [{"type": "text", "text": summary}],
        _MICROCOMPACT_KEY: True,
        _FULL_COMPACT_KEY: True,
    }
    history[:] = [summary_msg] + recent

    if estimate_context_cost(history) > target_chars and len(history) > 4:
        microcompact_history(history, keep_recent=keep_recent_turns, force=True)
    repair_tool_pairs(history)
    return True


def drop_oldest_turn(history: list[dict]) -> None:
    """暴力清理：直接将当前历史记录中最老的一轮完整对话（通常从Assistant发起工具到User返回结果）彻底删除。"""
    if not history:
        return
    end = _first_turn_end(history)
    del history[:end]


def repair_tool_pairs(history: list[dict]) -> None:
    """极其重要的协议修复函数：
    在裁剪或删除对话后，大模型 API 严格要求 tool_use（调用）必须有对应的 tool_result（结果）。
    此函数会找出断裂的配对，移除没有结果的调用，并将没有调用的“孤儿”结果降级为普通文本，防止触发 API 报错。
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


def _microcompact_message(
        msg: dict,
        max_text_tag: int,
        high_output_ids: set[str],
        protected_ids: set[str],
) -> None:
    """内部函数：对单条消息中的长文本、冗长的思考过程、巨型工具参数、高输出工具内容进行精细化截断替换，并将图像剥离。"""
    # 逻辑代码未改动... (省略展开)

def _split_for_full_compact(history: list[dict], keep_recent_turns: int) -> int:
    """内部函数：计算需要保护的最近几轮对话(tail)的起始索引，作为裁剪的切割点。"""
    # 逻辑代码未改动...

def _first_turn_end(history: list[dict]) -> int:
    """内部函数：找出历史记录中属于“最旧的第一轮完整交互”结束的索引下标，用于 drop_oldest_turn。"""
    # 逻辑代码未改动...

def _summarize_messages(messages: list[dict], title: str | None = None) -> str:
    """内部函数：对提供的一堆历史消息使用正则表达式和规则提取事实。
    包括提取：标识符、最近使用的文件路径、工具调用记录和错误报错信息，组装成文本摘要。
    """
    # 逻辑代码未改动...

def _message_text(msg: dict) -> str:
    """内部函数：将某一条消息中所有类型为 text 或 tool_result 的 block 内容提取并拼接成单一字符串。"""
    # 逻辑代码未改动...

def _extract_identifiers(text: str) -> list[str]:
    """内部函数：利用多组正则表达式，从文本中提取出系统路径、脚本名称、关键命令和变量名，防止摘要导致上下文丢失关键词汇。"""
    # 逻辑代码未改动...

def _extract_file_paths(text: str) -> list[str]:
    """内部函数：使用正则专门从文本中寻找长得像文件路径(如 /src/app.py)的字符串。"""
    # 逻辑代码未改动...

def _looks_error(text: str) -> bool:
    """内部函数：通过检查包含 error, exception, failed 等敏感词判定该段文本是否属于报错信息。"""
    # 逻辑代码未改动...

def _only_tool_results(msg: dict) -> bool:
    """内部函数：检查该消息是不是一个纯粹返回工具执行结果的消息（里面没有夹带普通的对话 text 块）。"""
    # 逻辑代码未改动...

def _tool_use_ids(msg: dict | None) -> set[str]:
    """内部函数：提取某条 Assistant 消息中包含的所有 tool_use 块的 ID。"""
    # 逻辑代码未改动...

def _tool_result_ids(msg: dict | None) -> set[str]:
    """内部函数：提取某条 User 消息中包含的所有 tool_result 块映射的调用 ID。"""
    # 逻辑代码未改动...

def _strip_tool_uses(msg: dict, missing_ids: set[str]) -> None:
    """内部函数：从 Assistant 消息中去除那些在下一步没有收到对应结果(结果被截断)的工具调用模块。"""
    # 逻辑代码未改动...

def _convert_orphan_tool_results(msg: dict, valid_ids: set[str]) -> None:
    """内部函数：如果在 User 消息里发现了“孤儿”工具结果（前面发起的工具调用已经被裁掉了），把这个特定块转为普通的文本，以防 400 报错。"""
    # 逻辑代码未改动...

def _has_blocks(msg: dict) -> bool:
    """内部函数：检查这条消息里是否还存在有效的内容 block（用于清理空消息）。"""
    # 逻辑代码未改动...

def _truncate_tagged_text(text: str, max_len: int) -> str:
    """内部函数：使用正则匹配类似 <thinking> 这类特定 XML 标签，并缩短标签内的内容。"""
    # 逻辑代码未改动...

def _truncate_tag(match: re.Match, max_len: int) -> str:
    """内部函数：对被正则捕获的特定 XML 标签片段执行替换，缩短中间的内容体。"""
    # 逻辑代码未改动...

def _truncate_text(text: str, max_len: int) -> str:
    """内部函数：标准文本截断器。保留文本的首部和尾部，用 '...[Truncated]...' 填充中间超出 max_len 的部分。"""
    # 逻辑代码未改动...

def _unique_keep_order(items: list[str]) -> list[str]:
    """内部函数：对列表进行去重处理，同时保留元素原本出现的先后顺序。"""
    # 逻辑代码未改动...

def estimate_context_cost(history: list[dict]) -> int:
    """估算工具函数：将给定的历史对话转为 JSON，返回它的字符总数，用来代替分词器(Tokenzier)作极速计算。"""
    # 逻辑代码未改动...

def estimate_request_cost(
        history: list[dict],
        *,
        system: str = "",
        tools: list[dict] | None = None,
        reserve_output_tokens: int = 0,
) -> int:
    """估算工具函数：计算发起一次大模型 API 请求预估消耗的总字符数（历史对话 + 系统提示词 + 工具Schema描述 + 预留输出）。"""
    # 逻辑代码未改动...

def _calc_cost(history: list[dict]) -> int:
    """内部帮助函数：包装估算历史记录上下文成本的代码。"""
    # 逻辑代码未改动...

def is_context_limit_error(response: Response | None) -> bool:
    """诊断函数：根据模型返回的错误 Response，通过字符串包含关系，判断是否是触碰到了模型的最大上下文窗口限制。"""
    # 逻辑代码未改动...

def _last_user_message(history: list[dict]) -> dict | None:
    """内部函数：倒序查找，返回历史对话中最后一条属于 User 的消息对象。"""
    # 逻辑代码未改动...

def _tool_name_by_id(history: list[dict]) -> dict[str, str]:
    """内部函数：遍历所有 Assistant 消息，建立一个 tool_use_id 到 tool_name 的映射字典。"""
    # 逻辑代码未改动...

def _tool_result_ids_for_tools(history: list[dict], tool_names: set[str]) -> set[str]:
    """内部函数：找出所有属于指定工具名称列表（tool_names）的调用的 ID 集合。"""
    # 逻辑代码未改动...

def _archived_tool_preview(content: str, path: Path, tool_use_id: str, tool_name: str) -> str:
    """内部函数：生成被归档(保存至磁盘)的巨型工具输出的占位提示文本，告诉大模型怎么去磁盘上找完整的记录。"""
    # 逻辑代码未改动...

def _history_digest(history: list[dict]) -> str:
    """内部函数：将用来传给 LLM 进行全文摘要的早期对话记录截断到最大 6 万字符以内，防止摘要本身超长。"""
    # 逻辑代码未改动...

def _wrap_earlier_summary(summary: str) -> str:
    """内部函数：确保 LLM 生成的摘要被统一加上 <earlier_summary> XML 标签包装。"""
    # 逻辑代码未改动...