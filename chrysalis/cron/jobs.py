"""基于文件存储的定时任务系统。

每个任务（job）作为一个单独的 JSON 文件存储在 ``data/cron/jobs`` 目录下。
调度器将运行时状态（runtime state）保存在任务文件内部的 ``state`` 字段中，
这样用户依然可以手动打开文件编辑那些声明性的配置（比如提示词、时间），而无需在中央数据库里翻找。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from configs.config import AgentConfig


DEFAULT_MAX_DELAY_MINUTES = 6 * 60
DATETIME_FORMAT_HINT = "YYYY-MM-DDTHH:mm"
CALENDAR_PERIODS = {"daily", "weekly", "monthly", "yearly"}
INTERVAL_PERIOD_UNITS = {
    "everyminute": ("minutes", 1),
    "everyminutes": ("minutes", 1),
    "everyhour": ("hours", 1),
    "everyhours": ("hours", 1),
    "everydaily": ("days", 1),
    "everyday": ("days", 1),
    "everydayly": ("days", 1),
    "everyweekly": ("weeks", 1),
    "everyweek": ("weeks", 1),
    "everymonthly": ("months", 1),
    "everymonth": ("months", 1),
    "everyyearly": ("years", 1),
    "everyyear": ("years", 1),
}


class CronError(ValueError):
    """当定时任务定义无效时抛出的异常。"""


def cron_root(config: AgentConfig) -> Path:
    """获取定时任务系统的根目录。"""
    return config.data_dir / "cron"


def jobs_dir(config: AgentConfig) -> Path:
    """获取存放所有任务 JSON 文件的目录。"""
    return cron_root(config) / "jobs"


def output_dir(config: AgentConfig) -> Path:
    """获取存放任务执行输出结果（Markdown文件）的目录。"""
    return cron_root(config) / "output"


def ensure_dirs(config: AgentConfig) -> None:
    """确保定时任务所需的目录结构（任务目录和输出目录）存在，没有则创建。"""
    jobs_dir(config).mkdir(parents=True, exist_ok=True)
    output_dir(config).mkdir(parents=True, exist_ok=True)


def normalize_job_id(value: str | None) -> str:
    """格式化并验证任务 ID，移除非法字符，为空时自动生成 UUID，确保作为文件名的安全性。"""
    raw = (value or "").strip()
    if not raw:
        raw = uuid.uuid4().hex[:12]
    # 只允许字母、数字、下划线、点和横杠
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    if not raw:
        raw = uuid.uuid4().hex[:12]
    return raw[:80]


def parse_datetime(value: str, field: str = "datetime") -> datetime:
    """将字符串解析为 datetime 对象，如果格式不符合 ISO 标准则抛出 CronError。"""
    text = str(value or "").strip()
    if not text:
        raise CronError(f"{field} is required, format: {DATETIME_FORMAT_HINT}")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise CronError(f"Invalid {field}: {text!r}, expected {DATETIME_FORMAT_HINT}") from exc


def parse_time(value: str) -> tuple[int, int]:
    """解析时间字符串 (HH:mm)，返回 (小时, 分钟) 的整数元组。"""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise CronError(f"Invalid time {text!r}, expected HH:mm")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise CronError(f"Invalid time {text!r}, expected HH:mm")
    return hour, minute


def parse_weekday(value: Any) -> int:
    """解析星期几的输入（支持数字 1-7 或英文缩写/全拼），返回对应的整数 (1-7)。"""
    names = {
        "mon": 1, "monday": 1,
        "tue": 2, "tuesday": 2,
        "wed": 3, "wednesday": 3,
        "thu": 4, "thursday": 4,
        "fri": 5, "friday": 5,
        "sat": 6, "saturday": 6,
        "sun": 7, "sunday": 7,
    }
    if isinstance(value, str):
        text = value.strip().lower()
        if text in names:
            return names[text]
        value = text
    try:
        weekday = int(value)
    except (TypeError, ValueError) as exc:
        raise CronError("weekday must be 1-7 or a weekday name") from exc
    if weekday < 1 or weekday > 7:
        raise CronError("weekday must be in range 1-7")
    return weekday


def normalize_period(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "").replace("-", "")
    if raw in CALENDAR_PERIODS:
        return raw
    if raw in INTERVAL_PERIOD_UNITS:
        return "interval"
    raise CronError(
        "schedule.period must be daily/weekly/monthly/yearly, "
        "or an interval name like everyminute/everyhour/everydaily/everymonthly/everyyearly"
    )


def _interval_unit(raw_period: str) -> tuple[str, int] | None:
    key = raw_period.strip().lower().replace("_", "").replace("-", "")
    return INTERVAL_PERIOD_UNITS.get(key)


def validate_schedule(schedule: dict[str, Any], *, allow_internal_interval: bool = False) -> dict[str, Any]:
    """验证并规范化调度计划 (schedule) 字典。支持 'once' (一次性) 和 'periodic' (周期性)。"""
    if not isinstance(schedule, dict):
        raise CronError("schedule must be an object")

    schedule_type = str(schedule.get("type", "")).strip().lower()
    if schedule_type == "once":
        run_at = parse_datetime(str(schedule.get("run_at", "")), "schedule.run_at")
        return {"type": "once", "run_at": run_at.strftime("%Y-%m-%dT%H:%M")}

    if schedule_type != "periodic":
        raise CronError("schedule.type must be 'once' or 'periodic'")

    raw_period = str(schedule.get("period", "")).strip().lower()
    raw_period_key = raw_period.replace("_", "").replace("-", "")
    if allow_internal_interval and raw_period_key == "interval":
        period = "interval"
    else:
        period = normalize_period(raw_period)

    start_at = parse_datetime(str(schedule.get("start_at", "")), "schedule.start_at")
    if period == "interval":
        try:
            unit = _interval_unit(raw_period)
            if allow_internal_interval and "interval_unit" in schedule:
                unit = (str(schedule.get("interval_unit") or "minutes"), 1)
            if "every_minutes" in schedule:
                every_minutes = int(schedule.get("every_minutes", 0))
            elif "every_hours" in schedule:
                every_minutes = int(schedule.get("every_hours", 0)) * 60
            elif "every_days" in schedule:
                every_minutes = int(schedule.get("every_days", 0)) * 1440
            elif unit:
                unit_name, unit_size = unit
                count = int(schedule.get("every", schedule.get("n", unit_size)))
                if unit_name == "minutes":
                    every_minutes = count
                elif unit_name == "hours":
                    every_minutes = count * 60
                elif unit_name == "days":
                    every_minutes = count * 1440
                else:
                    every_minutes = 0
            else:
                every_minutes = 0
        except (TypeError, ValueError) as exc:
            raise CronError("interval schedule count must be an integer") from exc

        if unit and unit[0] in {"months", "years", "weeks"}:
            count = int(schedule.get("every", schedule.get("n", unit[1])))
            if count < 1:
                raise CronError("interval schedule every/n must be >= 1")
            return {
                "type": "periodic",
                "period": "interval",
                "interval_unit": unit[0],
                "interval_count": count,
                "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
            }
        if every_minutes < 1:
            raise CronError("interval schedule count must be >= 1")
        return {
            "type": "periodic",
            "period": "interval",
            "every_minutes": every_minutes,
            "interval_unit": "minutes",
            "interval_count": every_minutes,
            "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
        }

    hour, minute = parse_time(str(schedule.get("time", "")))
    normalized: dict[str, Any] = {
        "type": "periodic",
        "period": period,
        "time": f"{hour:02d}:{minute:02d}",
        "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
    }

    if period == "weekly":
        normalized["weekday"] = parse_weekday(schedule.get("weekday"))
    elif period == "monthly":
        day = int(schedule.get("day", 0))
        if day < 1 or day > 31:
            raise CronError("monthly schedule.day must be in range 1-31")
        normalized["day"] = day
    elif period == "yearly":
        month = int(schedule.get("month", 0))
        day = int(schedule.get("day", 0))
        if month < 1 or month > 12:
            raise CronError("yearly schedule.month must be in range 1-12")
        if day < 1 or day > monthrange(2024, month)[1]:
            raise CronError("yearly schedule.day is invalid for that month")
        normalized["month"] = month
        normalized["day"] = day

    return normalized


def schedule_display(schedule: dict[str, Any]) -> str:
    """将调度计划转换为易于人类阅读的字符串格式，用于在终端或日志中展示。"""
    if schedule.get("type") == "once":
        return f"once at {schedule.get('run_at')}"
    period = schedule.get("period")
    time_text = schedule.get("time")
    if period == "interval":
        unit = schedule.get("interval_unit", "minutes")
        count = schedule.get("interval_count", schedule.get("every_minutes"))
        return f"every {count} {unit}"
    if period == "daily":
        return f"daily at {time_text}"
    if period == "weekly":
        return f"weekly weekday={schedule.get('weekday')} at {time_text}"
    if period == "monthly":
        return f"monthly day={schedule.get('day')} at {time_text}"
    if period == "yearly":
        return f"yearly {schedule.get('month'):02d}-{schedule.get('day'):02d} at {time_text}"
    return json.dumps(schedule, ensure_ascii=False)


def compute_next_run(

    schedule: dict[str, Any],
    last_run_at: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """
    计算任务的下一次执行时间。基于调度规则、上一次执行时间和当前时间。
    如果任务是一次性且已经执行过，或者计算不到未来的时间，则返回 None。
    """
    now = now or datetime.now()
    schedule = validate_schedule(schedule, allow_internal_interval=True)

    if schedule["type"] == "once":
        return None if last_run_at else schedule["run_at"]

    base = now
    if last_run_at:
        base = max(base, parse_datetime(last_run_at, "last_run_at"))
    start_at = parse_datetime(schedule["start_at"], "schedule.start_at")
    base = max(base, start_at)
    period = schedule["period"]

    if period == "interval":
        unit = schedule.get("interval_unit", "minutes")
        count = int(schedule.get("interval_count") or schedule.get("every_minutes") or 0)
        if unit == "weeks":
            every_minutes = count * 7 * 1440
        elif unit == "months":
            return _next_month_interval(start_at, base, count)
        elif unit == "years":
            return _next_year_interval(start_at, base, count)
        else:
            every_minutes = int(schedule["every_minutes"])
        if last_run_at:
            candidate = parse_datetime(last_run_at, "last_run_at") + timedelta(minutes=every_minutes)
            while candidate <= base:
                candidate += timedelta(minutes=every_minutes)
            return candidate.strftime("%Y-%m-%dT%H:%M")
        if start_at > now:
            return start_at.strftime("%Y-%m-%dT%H:%M")
        candidate = start_at
        while candidate <= now:
            candidate += timedelta(minutes=every_minutes)
        return candidate.strftime("%Y-%m-%dT%H:%M")

    hour, minute = parse_time(schedule["time"])

    if period == "daily":
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate.strftime("%Y-%m-%dT%H:%M")

    if period == "weekly":
        target = int(schedule["weekday"])
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (target - (base.weekday() + 1)) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate.strftime("%Y-%m-%dT%H:%M")

    if period == "monthly":
        day = int(schedule["day"])
        year, month = base.year, base.month
        # 往后寻找最多 36 个月（处理类似 2月31日 这种在某些月份不存在的极端情况）
        for _ in range(36):
            last_day = monthrange(year, month)[1]
            if day <= last_day:
                candidate = datetime(year, month, day, hour, minute)
                if candidate > base:
                    return candidate.strftime("%Y-%m-%dT%H:%M")
            month += 1
            if month > 12:
                year += 1
                month = 1
        return None

    if period == "yearly":
        month = int(schedule["month"])
        day = int(schedule["day"])
        year = base.year
        for _ in range(10):
            candidate = datetime(year, month, day, hour, minute)
            if candidate > base:
                return candidate.strftime("%Y-%m-%dT%H:%M")
            year += 1
        return None

    return None


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_month_interval(start_at: datetime, base: datetime, count: int) -> str:
    candidate = start_at
    while candidate <= base:
        candidate = _add_months(candidate, count)
    return candidate.strftime("%Y-%m-%dT%H:%M")


def _next_year_interval(start_at: datetime, base: datetime, count: int) -> str:
    candidate = start_at
    while candidate <= base:
        candidate = _add_months(candidate, count * 12)
    return candidate.strftime("%Y-%m-%dT%H:%M")


def _job_path(config: AgentConfig, job_id: str) -> Path:
    """获取特定任务 ID 对应的 JSON 配置文件路径。"""
    safe_id = normalize_job_id(job_id)
    return jobs_dir(config) / f"{safe_id}.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """安全地将字典写入 JSON 文件。使用临时文件和原子替换机制，防止写入过程中断导致文件损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_job(config: AgentConfig, job_id: str) -> dict[str, Any] | None:
    """根据任务 ID 从磁盘读取并解析任务的 JSON 数据。如果任务不存在则返回 None。"""
    ensure_dirs(config)
    path = _job_path(config, job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(config: AgentConfig, job: dict[str, Any]) -> dict[str, Any]:
    """将任务字典保存/覆盖到磁盘的 JSON 文件中，并返回保存后的任务字典。"""
    ensure_dirs(config)
    job_id = normalize_job_id(str(job.get("id", "")))
    job["id"] = job_id
    _atomic_write_json(_job_path(config, job_id), job)
    return job


def list_jobs(config: AgentConfig, include_disabled: bool = False) -> list[dict[str, Any]]:
    """列出目录下所有已保存的任务。可以通过 include_disabled 参数决定是否包含已禁用的任务。"""
    ensure_dirs(config)
    jobs: list[dict[str, Any]] = []
    for path in sorted(jobs_dir(config).glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if include_disabled or job.get("enabled", True):
            jobs.append(job)
    return jobs


def create_job(
    config: AgentConfig,
    *,
    schedule: dict[str, Any],
    prompt: str = "",
    job_id: str | None = None,
    name: str | None = None,
    script: str | None = None,
    no_agent: bool = False,
    context_from: list[str] | None = None,
    workdir: str | None = None,
    deliver: str = "local",
    repeat_times: int | None = None,
    max_delay_minutes: int | None = None,
) -> dict[str, Any]:
    """创建一个新的定时任务并保存到磁盘。包含初始状态生成和合法性校验。"""
    normalized_schedule = validate_schedule(schedule)
    if no_agent and not script:
        raise CronError("no_agent=true requires script")
    if not no_agent and not prompt.strip():
        raise CronError("prompt is required unless no_agent=true")

    job_id = normalize_job_id(job_id or name)
    if load_job(config, job_id):
        raise CronError(f"job already exists: {job_id}")

    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    next_run = compute_next_run(normalized_schedule, now=datetime.now())
    job = {
        "id": job_id,
        "name": name or job_id,
        "enabled": True,
        "schedule": normalized_schedule,
        "schedule_display": schedule_display(normalized_schedule),
        "prompt": prompt,
        "script": script,
        "no_agent": bool(no_agent),
        "context_from": context_from or [],
        "workdir": workdir,
        "deliver": deliver or "local",
        "max_delay_minutes": max_delay_minutes or DEFAULT_MAX_DELAY_MINUTES,
        "repeat": {"times": repeat_times, "completed": 0},
        "created_at": now,
        "state": {# 运行时状态会被隔离存放在这里，方便用户手工编辑 JSON 的其他部分
            "next_run_at": next_run,
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "last_output": None,
            "running": False,
            "started_at": None,
        },
    }
    return save_job(config, job)


def remove_job(config: AgentConfig, job_id: str) -> bool:
    """删除指定任务 ID 的 JSON 配置文件。如果文件存在并成功删除返回 True。"""
    path = _job_path(config, job_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def pause_job(config: AgentConfig, job_id: str) -> dict[str, Any]:
    """暂停指定的定时任务（将 enabled 设为 False，并记录暂停时间）。"""
    job = load_job(config, job_id)
    if not job:
        raise CronError(f"job not found: {job_id}")
    job["enabled"] = False
    job["state"]["paused_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
    return save_job(config, job)


def resume_job(config: AgentConfig, job_id: str) -> dict[str, Any]:
    job = load_job(config, job_id)
    if not job:
        raise CronError(f"job not found: {job_id}")
    job["enabled"] = True
    job.setdefault("state", {})["next_run_at"] = compute_next_run(job["schedule"], now=datetime.now())
    return save_job(config, job)


def get_due_jobs(config: AgentConfig, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    due: list[dict[str, Any]] = []
    for job in list_jobs(config):
        state = job.setdefault("state", {})
        if state.get("running"):
            continue
        next_run = state.get("next_run_at")
        if not next_run:
            next_run = compute_next_run(job["schedule"], state.get("last_run_at"), now)
            state["next_run_at"] = next_run
            save_job(config, job)
        if not next_run:
            continue
        next_dt = parse_datetime(next_run, "state.next_run_at")
        if next_dt > now:
            continue
        delay_minutes = (now - next_dt).total_seconds() / 60
        max_delay = int(job.get("max_delay_minutes") or DEFAULT_MAX_DELAY_MINUTES)
        if delay_minutes > max_delay:
            if job.get("schedule", {}).get("type") == "once":
                state["last_status"] = "missed"
                state["last_error"] = f"missed max delay window ({max_delay} minutes)"
                state["next_run_at"] = None
                job["enabled"] = False
            else:
                state["next_run_at"] = compute_next_run(job["schedule"], now=now)
            save_job(config, job)
            continue
        due.append(job)
    return due


def mark_job_started(config: AgentConfig, job_id: str) -> bool:
    """Mark a job as dispatched/running before the worker thread starts."""
    job = load_job(config, job_id)
    if not job:
        return False
    state = job.setdefault("state", {})
    if state.get("running"):
        return False
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    state["running"] = True
    state["started_at"] = now
    state["last_status"] = "running"
    state["last_error"] = None
    if job.get("schedule", {}).get("type") == "periodic":
        state["next_run_at"] = compute_next_run(job["schedule"], now=datetime.now())
    else:
        state["next_run_at"] = None
    save_job(config, job)
    return True


def advance_next_run(config: AgentConfig, job_id: str) -> bool:
    """在任务执行前，提前将周期性任务的下一次执行时间往后推移。
     防止由于任务执行过程中进程崩溃，导致在下次启动时该任务被无限重复触发。
     """
    job = load_job(config, job_id)
    if not job:
        return False
    if job.get("schedule", {}).get("type") != "periodic":
        return False
    job.setdefault("state", {})["next_run_at"] = compute_next_run(job["schedule"], now=datetime.now())
    save_job(config, job)
    return True


def mark_job_run(
    config: AgentConfig,
    job_id: str,
    *,
    success: bool,
    error: str | None = None,
    output_path: str | None = None,
) -> None:
    """
    标记一个任务已经执行完毕。更新最后运行状态、递增完成次数、计算下一次运行时间。
    如果达到了最大重复次数 (repeat_times)，则自动禁用该任务。
    """
    job = load_job(config, job_id)
    if not job:
        return
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    state = job.setdefault("state", {})
    state["running"] = False
    state["started_at"] = None
    state["last_run_at"] = now
    state["last_status"] = "ok" if success else "error"
    state["last_error"] = None if success else error
    state["last_output"] = output_path

    repeat = job.setdefault("repeat", {"times": None, "completed": 0})
    repeat["completed"] = int(repeat.get("completed") or 0) + 1
    times = repeat.get("times")
    if times is not None and int(times) > 0 and repeat["completed"] >= int(times):
        job["enabled"] = False
        state["next_run_at"] = None
        save_job(config, job)
        return

    state["next_run_at"] = compute_next_run(job["schedule"], last_run_at=now, now=datetime.now())
    if state["next_run_at"] is None:
        job["enabled"] = False
    save_job(config, job)


def save_job_output(config: AgentConfig, job_id: str, output: str) -> Path:
    """
    将任务的执行结果保存为 Markdown 文件。
    存储在对应任务专属的输出目录下，文件名为当前的时间戳。
    """
    ensure_dirs(config)
    safe_id = normalize_job_id(job_id)
    target_dir = output_dir(config) / safe_id
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = target_dir / f"{stamp}.md"
    path.write_text(output, encoding="utf-8")
    return path


def latest_output(config: AgentConfig, job_id: str) -> str | None:
    """获取指定任务最近一次执行的输出内容（读取其目录下最新的 Markdown 文件）。如果不存在则返回 None。"""
    safe_id = normalize_job_id(job_id)
    target_dir = output_dir(config) / safe_id
    if not target_dir.exists():
        return None
    files = sorted(target_dir.glob("*.md"))
    if not files:
        return None
    return files[-1].read_text(encoding="utf-8", errors="replace")
