"""GatewayMixin：拆分自 electron_runtime.py（方法体逐字符保留）。"""
from __future__ import annotations

from chrysalis.electron_runtime._common import *  # noqa: F401,F403


class GatewayMixin:
    def _respond_gateway_snapshot(self, command: dict[str, Any]) -> None:
        snapshot = self._snapshot()
        self._respond(command, data=snapshot)
        self._emit_event("gateway_changed", snapshot=snapshot)

    def _gateway_snapshot(self, activity_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        platforms = [self._gateway_platform_snapshot(platform) for platform in _DESKTOP_GATEWAY_PLATFORMS]
        activity_snapshot = activity_snapshot if isinstance(activity_snapshot, dict) else self._gateway_activity_snapshot()
        activities = activity_snapshot.get("activities")
        return {
            "platforms": platforms,
            "activities": copy.deepcopy(activities) if isinstance(activities, list) else [],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _gateway_activity_snapshot(self) -> dict[str, Any]:
        store = getattr(self, "_gateway_activity", None)
        if store is None:
            return {"version": 1, "updated_at": "", "activities": []}
        try:
            snapshot = store.snapshot()
        except Exception:
            return {"version": 1, "updated_at": "", "activities": []}
        activities = snapshot.get("activities") if isinstance(snapshot, dict) else []
        if not isinstance(activities, list):
            activities = []
        return {
            "version": _safe_int(snapshot.get("version")) if isinstance(snapshot, dict) else 1,
            "updated_at": str(snapshot.get("updated_at") or "") if isinstance(snapshot, dict) else "",
            "activities": [copy.deepcopy(item) for item in activities if isinstance(item, dict)],
        }

    def _gateway_activity_for_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return None
        for item in self._gateway_activity_snapshot().get("activities", []):
            if isinstance(item, dict) and str(item.get("session_id") or "") == session_id:
                return copy.deepcopy(item)
        return None

    def _gateway_activity_mtime(self) -> float:
        store = getattr(self, "_gateway_activity", None)
        path = getattr(store, "path", None)
        if not isinstance(path, Path):
            return 0.0
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _start_gateway_activity_watcher(self) -> None:
        thread = getattr(self, "_gateway_activity_watcher_thread", None)
        if thread is not None and thread.is_alive():
            return
        stop = getattr(self, "_gateway_activity_watcher_stop", None)
        if stop is None:
            stop = threading.Event()
            self._gateway_activity_watcher_stop = stop
        stop.clear()
        self._gateway_activity_last_mtime = self._gateway_activity_mtime()
        self._gateway_activity_watcher_thread = threading.Thread(
            target=self._gateway_activity_watcher,
            daemon=True,
        )
        self._gateway_activity_watcher_thread.start()

    def _stop_gateway_activity_watcher(self) -> None:
        stop = getattr(self, "_gateway_activity_watcher_stop", None)
        thread = getattr(self, "_gateway_activity_watcher_thread", None)
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        if getattr(self, "_gateway_activity_watcher_thread", None) is thread:
            self._gateway_activity_watcher_thread = None

    def _gateway_activity_watcher(self) -> None:
        stop = self._gateway_activity_watcher_stop
        try:
            while not stop.wait(_GATEWAY_ACTIVITY_POLL_SECONDS):
                mtime = self._gateway_activity_mtime()
                if mtime == self._gateway_activity_last_mtime:
                    continue
                self._gateway_activity_last_mtime = mtime
                self._emit_event("gateway_changed", snapshot=self._snapshot())
        finally:
            if getattr(self, "_gateway_activity_watcher_thread", None) is threading.current_thread():
                self._gateway_activity_watcher_thread = None

    def _gateway_activity_session_summary(self, activity: dict[str, Any], *, turns: int) -> dict[str, Any]:
        session_id = str(activity.get("session_id") or "")
        platform = str(activity.get("platform") or "gateway").strip() or "gateway"
        source = activity.get("source") if isinstance(activity.get("source"), dict) else {}
        source_label = str(source.get("description") or source.get("chat_name") or source.get("user_name") or "").strip()
        preview = str(activity.get("task_preview") or "").strip().replace("\n", " ")
        title_parts = [f"{_DESKTOP_GATEWAY_LABELS.get(platform, platform)} gateway"]
        if source_label:
            title_parts.append(source_label)
        if preview:
            title_parts.append(preview[:48])
        return {
            "id": session_id,
            "title": " · ".join(title_parts),
            "updated_at": str(activity.get("updated_at") or activity.get("started_at") or datetime.now().isoformat(timespec="seconds")),
            "model": str(activity.get("model") or self.kernel.active_model_name),
            "turns": max(0, turns),
            "pinned": False,
            "busy": True,
            "task_id": str(activity.get("task_id") or ""),
        }

    def _gateway_platform_snapshot(self, platform: str) -> dict[str, Any]:
        config = self._gateway_config(platform)
        missing_dependencies: list[str] = []
        try:
            missing_dependencies = missing_gateway_dependencies([str(config.get("launch_platform") or platform)])
        except BaseException as exc:
            if not isinstance(exc, SystemExit):
                raise
            missing_dependencies = [str(exc)]

        pid: int | None = None
        running = False
        started_at: str | None = None
        command = ""
        log_file: Path | None = None
        return_code: int | None = None
        last_error = ""
        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            last_error = self._gateway_last_errors.get(platform, "")
            log_file = self._gateway_last_logs.get(platform)
            if state is not None:
                code = state.process.poll()
                running = code is None
                pid = state.process.pid if running else None
                started_at = state.started_at
                command = state.command
                log_file = state.log_file
                return_code = code
                if code is not None:
                    state.return_code = code
                    if code != 0 and not state.last_error:
                        state.last_error = self._gateway_exit_error(state, code)
                if state.last_error:
                    last_error = state.last_error
                    self._gateway_last_errors[platform] = last_error
                self._gateway_last_logs[platform] = state.log_file

        configured = bool(config.get("configured"))
        if running:
            status = "running"
        elif not configured:
            status = "not_configured"
        elif last_error:
            status = "failed"
        else:
            status = "configured"

        log_file_text = str(log_file) if log_file else ""
        return {
            "id": platform,
            "label": _DESKTOP_GATEWAY_LABELS.get(platform, platform),
            "status": status,
            "configured": configured,
            "running": running,
            "pid": pid,
            "started_at": started_at,
            "return_code": return_code,
            "last_error": last_error,
            "configuration_error": str(config.get("configuration_error") or ""),
            "config_summary": str(config.get("summary") or ""),
            "required_config": copy.deepcopy(config.get("required_config") or []),
            "launch_platform": str(config.get("launch_platform") or platform),
            "missing_dependencies": missing_dependencies,
            "install_hint": dependency_install_hint(missing_dependencies) if missing_dependencies else "",
            "command": command,
            "log_file": log_file_text,
        }

    def _gateway_config(self, platform: str) -> dict[str, Any]:
        if platform == "qq":
            app_id = os.getenv("CHRYSALIS_QQ_APP_ID", "").strip()
            app_secret = os.getenv("CHRYSALIS_QQ_APP_SECRET", "").strip()
            official_configured = bool(app_id and app_secret)
            if official_configured:
                return {
                    "configured": True,
                    "launch_platform": "qq",
                    "summary": "Official QQ Bot credentials found",
                    "required_config": ["CHRYSALIS_QQ_APP_ID", "CHRYSALIS_QQ_APP_SECRET"],
                    "configuration_error": "",
                }
            missing = ["CHRYSALIS_QQ_APP_ID", "CHRYSALIS_QQ_APP_SECRET"]
            return {
                "configured": False,
                "launch_platform": "qq",
                "summary": "Set official QQ Bot credentials.",
                "required_config": missing,
                "configuration_error": "Missing QQ configuration: set CHRYSALIS_QQ_APP_ID and CHRYSALIS_QQ_APP_SECRET.",
            }

        if platform == "qq_personal":
            ws_url = os.getenv("CHRYSALIS_ONEBOT_WS_URL", "ws://127.0.0.1:3001").strip()
            configured = bool(ws_url)
            return {
                "configured": configured,
                "launch_platform": "qq_personal",
                "summary": f"OneBot WebSocket: {ws_url}" if ws_url else "Set OneBot WebSocket URL.",
                "required_config": ["CHRYSALIS_ONEBOT_WS_URL"],
                "configuration_error": "" if configured else "Missing personal QQ configuration: set CHRYSALIS_ONEBOT_WS_URL.",
            }

        if platform == "wechat":
            token_file = Path(
                os.getenv("CHRYSALIS_WECHAT_TOKEN_FILE", "").strip()
                or (ensure_gateway_dirs() / "wechat_personal_token.json")
            ).expanduser()
            token_ready = token_file.exists()
            return {
                "configured": True,
                "launch_platform": "wechat_personal",
                "summary": f"Token file: {token_file}" if token_ready else "First start opens WeChat QR login.",
                "required_config": ["Optional: CHRYSALIS_WECHAT_TOKEN_FILE"],
                "configuration_error": "",
            }

        if platform == "feishu":
            app_id = os.getenv("CHRYSALIS_FEISHU_APP_ID", "").strip()
            app_secret = os.getenv("CHRYSALIS_FEISHU_APP_SECRET", "").strip()
            configured = bool(app_id and app_secret)
            missing = [
                key
                for key, value in {
                    "CHRYSALIS_FEISHU_APP_ID": app_id,
                    "CHRYSALIS_FEISHU_APP_SECRET": app_secret,
                }.items()
                if not value
            ]
            return {
                "configured": configured,
                "launch_platform": "feishu",
                "summary": "Feishu app credentials found" if configured else "Set Feishu app id and secret.",
                "required_config": ["CHRYSALIS_FEISHU_APP_ID", "CHRYSALIS_FEISHU_APP_SECRET"],
                "configuration_error": f"Missing Feishu configuration: {', '.join(missing)}." if missing else "",
            }

        return {
            "configured": False,
            "launch_platform": platform,
            "summary": "",
            "required_config": [],
            "configuration_error": f"Unsupported gateway platform: {platform}",
        }

    def _handle_gateway_start(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return

        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            if state is not None and state.process.poll() is None:
                self._respond_gateway_snapshot(command)
                return
            if state is not None and state.process.poll() is not None:
                self._gateway_last_logs[platform] = state.log_file

        config = self._gateway_config(platform)
        if not config.get("configured"):
            error = str(config.get("configuration_error") or "Gateway is not configured.")
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        launch_platform = str(config.get("launch_platform") or platform)
        try:
            missing = missing_gateway_dependencies([launch_platform])
        except SystemExit as exc:
            missing = [str(exc)]
        if missing:
            error = dependency_install_hint(missing)
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        backend_note = ""
        if platform == "qq_personal":
            try:
                backend_note = self._ensure_qq_personal_backend()
            except (OSError, ValueError, TimeoutError) as exc:
                error = f"Failed to start NapCat OneBot backend: {type(exc).__name__}: {exc}"
                with self._gateway_lock:
                    self._gateway_last_errors[platform] = error
                snapshot = self._snapshot()
                self._respond(command, ok=False, error=error, data=snapshot)
                self._emit_event("gateway_changed", snapshot=snapshot)
                return

        shared_groups = bool(command.get("shared_groups", False))
        try:
            argv = gateway_process_argv([launch_platform], shared_groups=shared_groups)
            command_text = gateway_process_command([launch_platform], shared_groups=shared_groups)
            log_dir = self._gateway_log_dir()
            log_file = log_dir / f"{platform}_{int(time.time())}.log"
            log_handle = log_file.open("a", encoding="utf-8", buffering=1)
            if backend_note:
                log_handle.write(f"[desktop gateway] {backend_note}\n")
            try:
                popen_kwargs: dict[str, Any] = {
                    "cwd": str(PROJECT_ROOT),
                    "stdout": log_handle,
                    "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    )
                else:
                    popen_kwargs["start_new_session"] = True
                process = subprocess.Popen(argv, **popen_kwargs)
            finally:
                try:
                    log_handle.close()
                except OSError:
                    pass
        except (OSError, SystemExit, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._gateway_lock:
                self._gateway_last_errors[platform] = error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        state = _GatewayProcess(
            platform=platform,
            launch_platform=launch_platform,
            process=process,
            log_file=log_file,
            started_at=datetime.now().isoformat(timespec="seconds"),
            command=command_text,
        )
        with self._gateway_lock:
            self._gateway_processes[platform] = state
            self._gateway_last_logs[platform] = log_file
            self._gateway_last_errors.pop(platform, None)

        time.sleep(0.2)
        code = process.poll()
        if code is not None and code != 0:
            state.return_code = code
            state.last_error = self._gateway_exit_error(state, code)
            with self._gateway_lock:
                self._gateway_last_errors[platform] = state.last_error
            snapshot = self._snapshot()
            self._respond(command, ok=False, error=state.last_error, data=snapshot)
            self._emit_event("gateway_changed", snapshot=snapshot)
            return

        self._respond_gateway_snapshot(command)

    def _handle_gateway_stop(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return
        self._stop_gateway(platform, clear_error=True)
        self._respond_gateway_snapshot(command)

    def _handle_gateway_logs(self, command: dict[str, Any]) -> None:
        platform = _normalize_desktop_gateway_platform(command.get("platform"))
        if not platform:
            self._respond(command, ok=False, error="Missing or unsupported gateway platform.")
            return
        log_file = self._gateway_log_file(platform)
        log_text = self._read_gateway_log_tail(log_file, limit_chars=32_000) if log_file else ""
        self._respond(
            command,
            data={
                "platform": platform,
                "log_file": str(log_file) if log_file else "",
                "log": log_text,
            },
        )

    def _ensure_qq_personal_backend(self) -> str:
        """Start the verified local NapCat OneBot backend before qq_personal gateway."""
        if self._is_local_port_listening(_NAPCAT_ONEBOT_PORT):
            return f"NapCat OneBot already listening on 127.0.0.1:{_NAPCAT_ONEBOT_PORT}."

        launcher = _NAPCAT_LAUNCHER
        if not launcher.exists():
            raise FileNotFoundError(
                f"NapCat launcher was not found: {launcher}. "
                "Install NapCat.Shell.Windows.OneKey.zip or set CHRYSALIS_NAPCAT_LAUNCHER."
            )
        account = _NAPCAT_QQ_PERSONAL_ACCOUNT
        if not account:
            raise ValueError("Set CHRYSALIS_NAPCAT_QQ to the NapCat QQ account, for example 3843511481.")

        log_file = self._gateway_log_dir() / f"napcat_{account}_{int(time.time())}.log"
        command = f'cd /d "{launcher.parent}" && "{launcher}" {account}'
        with log_file.open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(f"Launching NapCat: {command}\n")

        run_cmd = launcher.parent / f"run_napcat_{account}.cmd"
        run_cmd.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"cd /d \"{launcher.parent}\"\r\n"
            f"echo Running: \"{launcher}\" {account}\r\n"
            f"call \"{launcher}\" {account}\r\n"
            "echo.\r\n"
            "echo launcher.bat exited with code %ERRORLEVEL%\r\n"
            "pause\r\n",
            encoding="utf-8",
        )
        with log_file.open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(f"Launching NapCat command file: {run_cmd}\n")
            handle.write(f"Command file calls: {launcher} {account}\n")

        if sys.platform.startswith("win"):
            subprocess.Popen(
                ["cmd.exe", "/k", str(run_cmd)],
                cwd=str(launcher.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        else:
            subprocess.Popen(
                [str(launcher), account],
                cwd=str(launcher.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self._is_local_port_listening(_NAPCAT_ONEBOT_PORT):
                return f"NapCat launcher.bat {account} started; OneBot is listening on 127.0.0.1:{_NAPCAT_ONEBOT_PORT}."
            time.sleep(1)
        raise TimeoutError(
            f"Started {run_cmd}, but 127.0.0.1:{_NAPCAT_ONEBOT_PORT} did not start listening within 45s. "
            "Check the visible NapCat/launcher window and approve UAC if prompted."
        )

    def _is_local_port_listening(self, port: int) -> bool:
        if sys.platform.startswith("win"):
            try:
                script = (
                    f"$c=Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction SilentlyContinue; "
                    "if($c){exit 0}else{exit 1}"
                )
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
                return completed.returncode == 0
            except Exception:
                return False
        return False

    def _gateway_log_dir(self) -> Path:
        path = ensure_gateway_dirs() / "desktop"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _gateway_log_file(self, platform: str) -> Path | None:
        with self._gateway_lock:
            state = self._gateway_processes.get(platform)
            if state is not None:
                return state.log_file
            remembered = self._gateway_last_logs.get(platform)
            if remembered is not None and remembered.exists():
                return remembered
        try:
            matches = sorted(
                self._gateway_log_dir().glob(f"{platform}_*.log"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return matches[0] if matches else None

    def _gateway_exit_error(self, state: _GatewayProcess, code: int | None) -> str:
        tail = self._read_gateway_log_tail(state.log_file, limit_chars=4_000)
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        excerpt = "\n".join(lines[-8:])
        prefix = f"Gateway exited with code {code}."
        return f"{prefix}\n{excerpt}".strip() if excerpt else prefix

    def _read_gateway_log_tail(self, path: Path | None, *, limit_chars: int) -> str:
        if path is None or not path.exists():
            return ""
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit_chars * 4))
                data = handle.read()
            return data.decode("utf-8", errors="replace")[-limit_chars:]
        except OSError:
            return ""

    def _stop_gateway(self, platform: str, *, clear_error: bool = False) -> None:
        with self._gateway_lock:
            state = self._gateway_processes.pop(platform, None)
            if clear_error:
                self._gateway_last_errors.pop(platform, None)
            if state is not None:
                self._gateway_last_logs[platform] = state.log_file
        if state is None:
            return
        process = state.process
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        except Exception as exc:
            with self._gateway_lock:
                self._gateway_last_errors[platform] = f"{type(exc).__name__}: {exc}"

    def _stop_all_gateways(self) -> None:
        with self._gateway_lock:
            platforms = list(self._gateway_processes)
        for platform in platforms:
            self._stop_gateway(platform, clear_error=True)

