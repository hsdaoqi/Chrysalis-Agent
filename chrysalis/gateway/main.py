"""CLI entry point for messaging gateway adapters."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import os
from pathlib import Path

from chrysalis.gateway.adapters.feishu import FeishuAdapter
from chrysalis.gateway.adapters.qq import QQAdapter
from chrysalis.gateway.adapters.qq_personal import QQPersonalAdapter
from chrysalis.gateway.adapters.wechat_personal import WeChatPersonalAdapter
from chrysalis.gateway.bootstrap import ensure_gateway_dirs
from chrysalis.gateway.service import GatewayService


PLATFORM_ALIASES = {
    "qq": "qq",
    "qq-personal": "qq_personal",
    "qq_personal": "qq_personal",
    "personal-qq": "qq_personal",
    "personal_qq": "qq_personal",
    "qq-group": "qq_personal",
    "qq_group": "qq_personal",
    "onebot": "qq_personal",
    "napcat": "qq_personal",
    "wechat": "wechat_personal",
    "wechat-personal": "wechat_personal",
    "wechat_personal": "wechat_personal",
    "wx": "wechat_personal",
    "feishu": "feishu",
    "lark": "feishu",
    "fs": "feishu",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Chrysalis messaging gateway.")
    parser.add_argument(
        "platforms",
        nargs="+",
        choices=sorted(PLATFORM_ALIASES),
        help="Platforms to run. Example: chrysalis-gateway qq wechat",
    )
    parser.add_argument(
        "--shared-groups",
        action="store_true",
        help="Use one shared session per group/thread instead of one session per user.",
    )
    args = parser.parse_args()
    selected = list(dict.fromkeys(PLATFORM_ALIASES[p] for p in args.platforms))
    locks = _acquire_platform_locks(selected)
    try:
        asyncio.run(_run(selected, group_sessions_per_user=not args.shared_groups))
    finally:
        _release_platform_locks(locks)


async def _run(platforms: list[str], *, group_sessions_per_user: bool) -> None:
    service = GatewayService(group_sessions_per_user=group_sessions_per_user)
    tasks: list[asyncio.Task] = []
    for platform in platforms:
        if platform == "qq":
            tasks.append(asyncio.create_task(QQAdapter(service).run_forever()))
        elif platform == "qq_personal":
            adapter = QQPersonalAdapter(service)
            tasks.append(asyncio.create_task(asyncio.to_thread(adapter.run_forever)))
        elif platform == "wechat_personal":
            adapter = WeChatPersonalAdapter(service)
            tasks.append(asyncio.create_task(asyncio.to_thread(adapter.run_forever)))
        elif platform == "feishu":
            adapter = FeishuAdapter(service)
            tasks.append(asyncio.create_task(asyncio.to_thread(adapter.run_forever)))
        else:
            raise SystemExit(f"Unsupported platform: {platform}")
    if not tasks:
        raise SystemExit("No platform selected.")
    print(f"[Gateway] running platforms: {', '.join(platforms)}", flush=True)
    await asyncio.gather(*tasks)


def _acquire_platform_locks(platforms: list[str]) -> list[Path]:
    lock_dir = ensure_gateway_dirs() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    acquired: list[Path] = []
    for platform in sorted(set(platforms)):
        lock_path = lock_dir / f"{platform}.lock"
        if _try_acquire_lock(lock_path):
            acquired.append(lock_path)
            continue
        pid = _read_lock_pid(lock_path)
        raise SystemExit(
            f"Gateway platform already running: {platform}"
            f"{f' (pid {pid})' if pid else ''}. Stop it before starting another one."
        )
    if acquired:
        atexit.register(_release_platform_locks, acquired)
    return acquired


def _try_acquire_lock(lock_path: Path) -> bool:
    payload = f"{os.getpid()}\n"
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = _read_lock_pid(lock_path)
            if pid and _pid_is_running(pid):
                return False
            try:
                lock_path.unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return True
    return False


def _release_platform_locks(lock_paths: list[Path]) -> None:
    current_pid = os.getpid()
    for lock_path in lock_paths:
        if _read_lock_pid(lock_path) != current_pid:
            continue
        try:
            lock_path.unlink()
        except OSError:
            pass


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip().splitlines()[0]
        pid = int(raw)
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid > 0 else None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_running(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process = kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(process)
    except Exception:
        return False


if __name__ == "__main__":
    main()
