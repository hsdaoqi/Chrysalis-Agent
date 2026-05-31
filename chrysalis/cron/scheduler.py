"""Scheduled job runner for Chrysalis."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from configs.config import AgentConfig
from chrysalis.cron.jobs import (
    CronError,
    cron_root,
    get_due_jobs,
    latest_output,
    mark_job_started,
    mark_job_run,
    save_job_output,
)


SILENT_MARKER = "[SILENT]"
DEFAULT_TICK_SECONDS = 60
DEFAULT_SCRIPT_TIMEOUT = 120
DEFAULT_MAX_WORKERS = 4
_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("CHRYSALIS_CRON_MAX_WORKERS", DEFAULT_MAX_WORKERS)))
_RUNNING_LOCK = threading.Lock()
_RUNNING_JOBS: set[str] = set()


@contextmanager
def _tick_lock(config: AgentConfig) -> Iterator[bool]:
    root = cron_root(config)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".tick.lock"
    lock_fh = lock_path.open("a+", encoding="utf-8")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                lock_fh.seek(0)
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                yield False
                return
        yield True
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_fh.seek(0)
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_fh.close()


def tick(config: AgentConfig | None = None, *, verbose: bool = True, async_run: bool = False) -> int:
    config = config or AgentConfig()
    with _tick_lock(config) as locked:
        if not locked:
            if verbose:
                print("[cron] skipped: another tick is running")
            return 0

        due_jobs = get_due_jobs(config)
        if verbose:
            print(f"[cron] due jobs: {len(due_jobs)}")
        ran = 0
        for job in due_jobs:
            job_id = job["id"]
            if async_run:
                if _dispatch_job(config, job, verbose=verbose):
                    ran += 1
            else:
                if not mark_job_started(config, job_id):
                    if verbose:
                        print(f"[cron:{job_id}] skipped: already running")
                    continue
                _process_job(config, job, verbose=verbose)
                ran += 1
        return ran


def _dispatch_job(config: AgentConfig, job: dict, *, verbose: bool = True) -> bool:
    job_id = str(job["id"])
    with _RUNNING_LOCK:
        if job_id in _RUNNING_JOBS:
            if verbose:
                print(f"[cron:{job_id}] skipped: already running")
            return False
        if not mark_job_started(config, job_id):
            if verbose:
                print(f"[cron:{job_id}] skipped: already running")
            return False
        _RUNNING_JOBS.add(job_id)
    _EXECUTOR.submit(_process_job_async, config, job, verbose)
    if verbose:
        print(f"[cron:{job_id}] dispatched")
    return True


def _process_job_async(config: AgentConfig, job: dict, verbose: bool) -> None:
    try:
        _process_job(config, job, verbose=verbose)
    finally:
        with _RUNNING_LOCK:
            _RUNNING_JOBS.discard(str(job.get("id")))


def _process_job(config: AgentConfig, job: dict, *, verbose: bool = True) -> None:
    job_id = str(job["id"])
    try:
        success, output_doc, final_response, error = run_job(config, job)
        output_path = save_job_output(config, job_id, output_doc)
        mark_job_run(
            config,
            job_id,
            success=success and bool(final_response.strip()),
            error=error if error else None if final_response.strip() else "empty final response",
            output_path=str(output_path),
        )
        if verbose and final_response.strip() and SILENT_MARKER not in final_response.upper():
            print(f"[cron:{job_id}] {final_response}")
    except Exception as exc:
        output_path = save_job_output(
            config,
            job_id,
            f"# Cron Job Failed\n\nJob: {job_id}\n\nError: {type(exc).__name__}: {exc}\n",
        )
        mark_job_run(config, job_id, success=False, error=str(exc), output_path=str(output_path))
        if verbose:
            print(f"[cron:{job_id}] failed: {exc}")


def run_job(config: AgentConfig, job: dict) -> tuple[bool, str, str, str | None]:
    job_id = str(job.get("id", "cron-job"))
    job_name = str(job.get("name") or job_id)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if job.get("no_agent"):
        ok, output = _run_job_script(config, str(job.get("script") or ""), job.get("workdir"))
        if not ok:
            doc = _output_doc(job_id, job_name, now_text, "script failed", output)
            return False, doc, output, output
        if not output.strip() or not _wake_agent_enabled(output):
            doc = _output_doc(job_id, job_name, now_text, "silent script", output or SILENT_MARKER)
            return True, doc, SILENT_MARKER, None
        doc = _output_doc(job_id, job_name, now_text, "script", output)
        return True, doc, output, None

    prerun: tuple[bool, str] | None = None
    if job.get("script"):
        prerun = _run_job_script(config, str(job["script"]), job.get("workdir"))
        if prerun[0] and not _wake_agent_enabled(prerun[1]):
            doc = _output_doc(job_id, job_name, now_text, "silent script gate", prerun[1] or SILENT_MARKER)
            return True, doc, SILENT_MARKER, None

    prompt = _build_job_prompt(config, job, prerun)
    from chrysalis.kernel import Kernel

    child_config = config
    workdir = str(job.get("workdir") or "").strip()
    if workdir:
        child_config = AgentConfig(
            root=config.root,
            llm=config.llm,
            skills_dir=config.skills_dir,
            data_dir=config.data_dir,
            memory_dir=config.memory_dir,
            workspace_dir=Path(workdir),
            max_turns=config.max_turns,
            min_skill_turns=config.min_skill_turns,
        )
    kernel = Kernel(config=child_config, progress=None)
    result = kernel.run(prompt)
    final = str(result.get("final") or result.get("error") or "")
    success = bool(result.get("ok"))
    output = {
        "job_id": job_id,
        "job_name": job_name,
        "run_time": now_text,
        "success": success,
        "result": result,
    }
    doc = _output_doc(job_id, job_name, now_text, "agent", json.dumps(output, ensure_ascii=False, indent=2))
    return success, doc, final, None if success else final


def _build_job_prompt(
    config: AgentConfig,
    job: dict,
    prerun_script: tuple[bool, str] | None = None,
) -> str:
    lines = [
        "你正在执行 Chrysalis 定时任务。",
        "",
        f"任务名称：{job.get('name') or job.get('id')}",
        f"任务 ID：{job.get('id')}",
        "",
        "规则：",
        "- 最终回答会被系统保存到 cron output。",
        "- 如果没有新的内容、没有变化、没有需要报告的事情，只回复 [SILENT]。",
        "- 不要声称你已经发送了外部消息；当前 deliver 仅支持 local 保存。",
        "",
        "用户任务：",
        str(job.get("prompt") or "").strip(),
    ]
    if prerun_script is not None:
        ok, output = prerun_script
        lines.extend([
            "",
            "脚本输出：",
            f"status={'ok' if ok else 'error'}",
            output.strip() or "(empty)",
        ])
    context_from = job.get("context_from") or []
    if isinstance(context_from, str):
        context_from = [context_from]
    for ref in context_from:
        previous = latest_output(config, str(ref))
        if previous:
            lines.extend([
                "",
                f"上游任务最近输出：{ref}",
                previous[-8000:],
            ])
    return "\n".join(lines).strip()


def _run_job_script(config: AgentConfig, script_path: str, workdir: str | None = None) -> tuple[bool, str]:
    scripts_dir = config.data_dir / "cron" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    raw = Path(script_path).expanduser()
    path = raw.resolve() if raw.is_absolute() else (scripts_dir / raw).resolve()
    scripts_root = scripts_dir.resolve()
    try:
        path.relative_to(scripts_root)
    except ValueError:
        return False, f"script must be under {scripts_root}: {script_path}"
    if not path.exists() or not path.is_file():
        return False, f"script not found: {path}"

    suffix = path.suffix.lower()
    if suffix in {".sh", ".bash"}:
        import shutil

        bash = shutil.which("bash")
        if not bash:
            return False, "bash not found on PATH"
        argv = [bash, str(path)]
    else:
        argv = [sys.executable, str(path)]

    cwd = Path(workdir).expanduser().resolve() if workdir else config.workspace_dir
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=int(os.getenv("CHRYSALIS_CRON_SCRIPT_TIMEOUT", DEFAULT_SCRIPT_TIMEOUT)),
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"script timed out: {exc}"
    output = (proc.stdout or "").strip()
    if proc.stderr:
        output = (output + "\n\n[stderr]\n" + proc.stderr.strip()).strip()
    if proc.returncode != 0:
        return False, output or f"script exited with code {proc.returncode}"
    return True, output


def _wake_agent_enabled(output: str) -> bool:
    lowered = output.lower()
    return not ("wakeagent=false" in lowered or '"wakeagent": false' in lowered)


def _output_doc(job_id: str, job_name: str, run_time: str, mode: str, body: str) -> str:
    return (
        f"# Cron Job: {job_name}\n\n"
        f"**Job ID:** {job_id}\n"
        f"**Run Time:** {run_time}\n"
        f"**Mode:** {mode}\n\n"
        "---\n\n"
        f"{body.strip()}\n"
    )


def run_daemon(config: AgentConfig | None = None, *, interval: int = DEFAULT_TICK_SECONDS) -> None:
    config = config or AgentConfig()
    print(f"[cron] daemon started, interval={interval}s")
    while True:
        tick(config, verbose=True, async_run=True)
        time.sleep(interval)
