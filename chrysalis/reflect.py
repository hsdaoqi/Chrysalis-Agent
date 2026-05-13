"""基于 trace.log 的运行反思器。"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from chrysalis.config import AgentConfig


def reflect_traces(config: AgentConfig, limit: int = 50) -> dict:
    """读取最近运行轨迹，生成一份可审查的反思报告。"""
    records = read_trace_records(config.trace_log, limit)
    report = build_reflection_report(records, config.min_skill_turns)
    report_dir = config.data_dir / "reflections"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / _report_name()
    path.write_text(report, encoding="utf-8")
    return {
        "ok": True,
        "final": "已生成运行复盘报告。",
        "path": str(path),
        "records": len(records),
        "report": report,
        "transcript": [],
    }


def read_trace_records(path: Path, limit: int = 50) -> list[dict]:
    if not path.exists():
        return []

    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records[-limit:]


def build_reflection_report(records: list[dict], min_skill_turns: int = 16) -> str:
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Chrysalis 运行复盘",
        "",
        f"- 生成时间：{now}",
        f"- 分析条数：{len(records)}",
    ]

    if not records:
        lines.extend(["", "## 结论", "- 暂无运行轨迹。"])
        return "\n".join(lines) + "\n"

    summary = analyze_records(records, min_skill_turns)
    lines.extend([
        "",
        "## 概览",
        f"- 成功：{summary['success']} / {summary['total']}",
        f"- 失败：{len(summary['failures'])}",
        f"- 平均耗时：{summary['avg_elapsed_ms']} ms",
        f"- 长链路：{len(summary['long_runs'])}",
        f"- 多工具任务：{len(summary['tool_heavy'])}",
        f"- 调用过技能：{len(summary['used_skills'])}",
        f"- 写入过技能：{len(summary['wrote_skills'])}",
        f"- 技能候选：{len(summary['skill_candidates'])}",
        f"- 技能验证失败：{len(summary['skill_validation_failures'])}",
        f"- 用户介入：{len(summary['user_interventions'])}",
        f"- 模型/循环异常：{len(summary['model_errors'])}",
    ])

    lines.extend(_counter_section("常用工具", summary["tool_counter"]))
    lines.extend(_counter_section("失败工具", summary["failed_tool_counter"]))
    lines.extend(_section("失败任务", summary["failures"], "需要优先复查失败原因，避免重复试错。"))
    lines.extend(_section("需要用户介入", summary["user_interventions"], "暂无用户介入。"))
    lines.extend(_section("模型/循环异常", summary["model_errors"], "暂无模型/循环异常。"))
    lines.extend(_section("技能候选", summary["skill_candidates"], "暂无满足条件但未沉淀的候选。"))
    lines.extend(_section("技能验证失败", summary["skill_validation_failures"], "暂无技能验证失败。"))
    lines.extend(_duplicate_section(summary["duplicate_tasks"]))
    lines.extend(_section("已写入技能的任务", summary["wrote_skills"], "后续遇到相似任务应优先调用已有技能。"))
    lines.extend(_section("最近任务", records[-10:], "暂无最近任务。"))

    lines.extend([
        "",
        "## 建议",
        "- 优先处理失败任务和失败工具高频项。",
        "- 对技能候选，先确认是否已有 SOP 或 skill 覆盖，再考虑泛化沉淀。",
        "- 对技能验证失败，修生成器提示或验证规则，不要手动绕过 L1 写入。",
        "- 对用户介入项，判断是否需要新增 ask_user 前置条件或更清楚的任务澄清策略。",
        "- 对重复任务，优先补技能、SOP 或 L1 索引。",
    ])
    return "\n".join(lines) + "\n"


def analyze_records(records: list[dict], min_skill_turns: int = 16) -> dict:
    total = len(records)
    failures = [item for item in records if not item.get("ok")]
    wrote_skills = [item for item in records if item.get("wrote_skill")]
    used_skills = [item for item in records if int(item.get("skill_calls") or 0) > 0]
    long_runs = [item for item in records if int(item.get("turns") or 0) >= min_skill_turns]
    tool_heavy = [item for item in records if int(item.get("tool_calls") or 0) >= 3]
    skill_candidates = [
        item for item in records
        if item.get("candidate_skill") or (
            item.get("ok")
            and not item.get("wrote_skill")
            and int(item.get("skill_calls") or 0) == 0
            and int(item.get("turns") or 0) >= min_skill_turns
            and int(item.get("tool_calls") or 0) >= 3
        )
    ]
    skill_validation_failures = [item for item in records if item.get("skill_validation_error")]
    user_interventions = [item for item in records if item.get("need_user")]
    model_errors = [item for item in records if item.get("model_error")]
    duplicate_tasks = [
        (task, count) for task, count in Counter(str(item.get("task", "")) for item in records).most_common()
        if task and count > 1
    ]
    elapsed = [int(item.get("elapsed_ms") or 0) for item in records]
    tool_counter = Counter()
    failed_tool_counter = Counter()
    for item in records:
        for tool in item.get("tools", []) or []:
            name = str(tool.get("name", ""))
            if name:
                tool_counter[name] += 1
        for tool in item.get("failed_tools", []) or []:
            name = str(tool.get("name", ""))
            if name:
                failed_tool_counter[name] += 1

    return {
        "total": total,
        "success": total - len(failures),
        "avg_elapsed_ms": int(sum(elapsed) / len(elapsed)) if elapsed else 0,
        "failures": failures,
        "wrote_skills": wrote_skills,
        "used_skills": used_skills,
        "long_runs": long_runs,
        "tool_heavy": tool_heavy,
        "skill_candidates": skill_candidates,
        "skill_validation_failures": skill_validation_failures,
        "user_interventions": user_interventions,
        "model_errors": model_errors,
        "duplicate_tasks": duplicate_tasks,
        "tool_counter": tool_counter,
        "failed_tool_counter": failed_tool_counter,
    }


def _section(title: str, records: list[dict], empty_text: str) -> list[str]:
    lines = ["", f"## {title}"]
    if not records:
        lines.append(f"- 无。{empty_text}")
        return lines
    for item in records[-10:]:
        lines.append(_format_record(item))
    return lines


def _counter_section(title: str, counter: Counter) -> list[str]:
    lines = ["", f"## {title}"]
    if not counter:
        lines.append("- 无。")
        return lines
    for name, count in counter.most_common(10):
        lines.append(f"- {name}：{count}")
    return lines


def _duplicate_section(items: list[tuple[str, int]]) -> list[str]:
    lines = ["", "## 重复任务"]
    if not items:
        lines.append("- 无明显重复。")
        return lines
    for task, count in items[:10]:
        lines.append(f"- {task}（{count} 次）")
    return lines


def _format_record(item: dict) -> str:
    task = str(item.get("task", "")).replace("\n", " ")
    final = str(item.get("final", "")).replace("\n", " ")
    reason = str(item.get("failure_reason") or item.get("skill_validation_error") or "").replace("\n", " ")
    turns = int(item.get("turns") or 0)
    tools = int(item.get("tool_calls") or 0)
    skills = int(item.get("skill_calls") or 0)
    elapsed = int(item.get("elapsed_ms") or 0)
    suffix = f" | reason={reason}" if reason else ""
    return f"- {task} | turns={turns}, tools={tools}, skills={skills}, elapsed={elapsed}ms | {final}{suffix}"


def _report_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"reflection_{stamp}.md"


def main() -> None:
    result = reflect_traces(AgentConfig())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
