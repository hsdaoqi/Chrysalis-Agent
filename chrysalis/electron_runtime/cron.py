"""CronMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class CronMixin:
    def _cron_snapshot(self) -> dict[str, Any]:
        try:
            jobs = copy.deepcopy(list_jobs(self.kernel.config, include_disabled=True))
        except Exception as exc:
            jobs = []
            with self._cron_lock:
                self._cron_daemon_last_error = f"{type(exc).__name__}: {exc}"
        with self._cron_lock:
            thread = self._cron_daemon_thread
            running = bool(thread and thread.is_alive() and not self._cron_daemon_stop.is_set())
            daemon = {
                "running": running,
                "interval_seconds": self._cron_daemon_interval_seconds,
                "started_at": self._cron_daemon_started_at,
                "last_tick_at": self._cron_daemon_last_tick_at,
                "last_count": self._cron_daemon_last_count,
                "last_error": self._cron_daemon_last_error,
            }
        return {"daemon": daemon, "jobs": jobs}

    def _respond_cron_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("cron_changed", snapshot=snapshot)

    def _handle_cron_create(self, command: dict[str, Any]) -> None:
        spec = command.get("spec")
        if not isinstance(spec, dict):
            spec = {
                key: value
                for key, value in command.items()
                if key not in {"type", "request_id"}
            }
        context_from = spec.get("context_from") or []
        if isinstance(context_from, str):
            context_from = [part.strip() for part in re.split(r"[,\n]+", context_from) if part.strip()]
        try:
            create_job(
                self.kernel.config,
                schedule=spec["schedule"],
                prompt=str(spec.get("prompt") or ""),
                job_id=spec.get("id"),
                name=spec.get("name"),
                script=spec.get("script"),
                no_agent=bool(spec.get("no_agent", False)),
                context_from=context_from,
                workdir=spec.get("workdir"),
                deliver=spec.get("deliver", "local"),
                repeat_times=spec.get("repeat_times"),
                max_delay_minutes=spec.get("max_delay_minutes"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_update(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        spec = command.get("spec")
        if not isinstance(spec, dict):
            spec = {}
        context_from = spec.get("context_from") or []
        if isinstance(context_from, str):
            context_from = [part.strip() for part in re.split(r"[,\n]+", context_from) if part.strip()]
        try:
            update_job(
                self.kernel.config,
                job_id,
                schedule=spec["schedule"],
                prompt=str(spec.get("prompt") or ""),
                name=spec.get("name"),
                script=spec.get("script"),
                no_agent=bool(spec.get("no_agent", False)),
                context_from=context_from,
                workdir=spec.get("workdir"),
                deliver=spec.get("deliver", "local"),
                repeat_times=spec.get("repeat_times"),
                max_delay_minutes=spec.get("max_delay_minutes"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_pause(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        try:
            pause_job(self.kernel.config, job_id)
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_resume(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        try:
            resume_job(self.kernel.config, job_id)
        except (CronError, KeyError, TypeError, ValueError) as exc:
            self._respond(command, ok=False, error=str(exc))
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_remove(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        job = load_job(self.kernel.config, job_id)
        if job and job.get("state", {}).get("running"):
            self._respond(command, ok=False, error="Job is running.")
            return
        ok = remove_job(self.kernel.config, job_id)
        if not ok:
            self._respond(command, ok=False, error=f"Job not found: {job_id}")
            return
        self._respond_cron_snapshot(command)

    def _handle_cron_run(self, command: dict[str, Any]) -> None:
        job_id = str(command.get("job_id") or "").strip()
        if not job_id:
            self._respond(command, ok=False, error="Missing job_id.")
            return
        job = load_job(self.kernel.config, job_id)
        if not job:
            self._respond(command, ok=False, error=f"Job not found: {job_id}")
            return
        with self._cron_dispatch_lock:
            if not mark_job_started(self.kernel.config, job_id):
                self._respond(command, ok=False, error="Job is already running.")
                return
            threading.Thread(target=self._cron_job_worker, args=(job,), daemon=True).start()
        self._respond_cron_snapshot(command)

    def _handle_cron_tick(self, command: dict[str, Any]) -> None:
        try:
            count = self._run_cron_tick()
        except Exception as exc:
            self._record_cron_tick(0, f"{type(exc).__name__}: {exc}")
            self._respond(command, ok=False, error=str(exc))
            return
        with self._cron_lock:
            self._cron_daemon_last_count = count
        self._respond_cron_snapshot(command)

    def _handle_cron_daemon_start(self, command: dict[str, Any]) -> None:
        try:
            interval_seconds = int(command.get("interval_seconds") or 60)
        except (TypeError, ValueError):
            interval_seconds = 60
        self._start_cron_daemon(max(1, interval_seconds))
        self._respond_cron_snapshot(command)

    def _handle_cron_daemon_stop(self, command: dict[str, Any]) -> None:
        self._stop_cron_daemon()
        self._respond_cron_snapshot(command)

    def _start_cron_daemon(self, interval_seconds: int) -> None:
        thread_to_start: threading.Thread | None = None
        with self._cron_lock:
            self._cron_daemon_interval_seconds = interval_seconds
            if self._cron_daemon_thread and self._cron_daemon_thread.is_alive():
                return
            self._cron_daemon_stop.clear()
            self._cron_daemon_started_at = datetime.now().isoformat(timespec="seconds")
            self._cron_daemon_last_error = None
            self._cron_daemon_thread = threading.Thread(target=self._cron_daemon_loop, daemon=True)
            thread_to_start = self._cron_daemon_thread
        thread_to_start.start()

    def _stop_cron_daemon(self) -> None:
        with self._cron_lock:
            self._cron_daemon_stop.set()
            thread = self._cron_daemon_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        with self._cron_lock:
            if self._cron_daemon_thread is thread and (thread is None or not thread.is_alive()):
                self._cron_daemon_thread = None

    def _cron_daemon_loop(self) -> None:
        try:
            while not self._cron_daemon_stop.is_set():
                try:
                    self._run_cron_tick()
                except Exception as exc:
                    self._record_cron_tick(0, f"{type(exc).__name__}: {exc}")
                self._emit_event("cron_changed", snapshot=self._snapshot())
                with self._cron_lock:
                    interval_seconds = self._cron_daemon_interval_seconds
                if self._cron_daemon_stop.wait(max(1, interval_seconds)):
                    break
        finally:
            with self._cron_lock:
                if self._cron_daemon_thread is threading.current_thread():
                    self._cron_daemon_thread = None

    def _run_cron_tick(self) -> int:
        with self._cron_dispatch_lock:
            count = tick(self.kernel.config, verbose=False, async_run=True)
        self._record_cron_tick(count, None)
        return count

    def _record_cron_tick(self, count: int, error: str | None) -> None:
        with self._cron_lock:
            self._cron_daemon_last_tick_at = datetime.now().isoformat(timespec="seconds")
            self._cron_daemon_last_count = count
            self._cron_daemon_last_error = error

    def _cron_job_worker(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        try:
            success, output_doc, final_response, error = run_job(self.kernel.config, job)
            output_path = save_job_output(self.kernel.config, job_id, output_doc)
            mark_job_run(
                self.kernel.config,
                job_id,
                success=success and bool(final_response.strip()),
                error=error if error else None if final_response.strip() else "empty final response",
                output_path=str(output_path),
            )
        except Exception as exc:
            try:
                output_path = save_job_output(
                    self.kernel.config,
                    job_id,
                    f"# Cron Job Failed\n\nJob: {job_id}\n\nError: {type(exc).__name__}: {exc}\n",
                )
            except Exception:
                output_path = None
            mark_job_run(
                self.kernel.config,
                job_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                output_path=str(output_path) if output_path else None,
            )
        finally:
            self._emit_event("cron_changed", snapshot=self._snapshot())

