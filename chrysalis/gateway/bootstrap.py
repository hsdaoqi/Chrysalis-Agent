"""Bootstrap helpers for starting Chrysalis gateway adapters."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from configs.config import PROJECT_ROOT, project_path


PLATFORM_ALIASES: dict[str, str] = {
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

PLATFORM_DEPS: dict[str, list[tuple[str, str]]] = {
    "qq": [
        ("botpy", "qq-botpy"),
        ("requests", "requests"),
        ("aiohttp", "aiohttp"),
    ],
    "qq_personal": [
        ("websocket", "websocket-client"),
        ("requests", "requests"),
    ],
    "wechat_personal": [
        ("requests", "requests"),
        ("qrcode", "qrcode"),
        ("Crypto", "pycryptodome"),
        ("PIL", "Pillow"),
    ],
    "feishu": [
        ("requests", "requests"),
        ("lark_oapi", "larksuite-oapi"),
    ],
}


@dataclass(slots=True)
class GatewayLaunchResult:
    platforms: list[str]
    command: str
    pid: int | None
    visible: bool
    log_file: Path | None = None


def normalize_gateway_platforms(platforms: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for platform in platforms:
        key = str(platform).strip().lower()
        if not key:
            continue
        mapped = PLATFORM_ALIASES.get(key)
        if not mapped:
            raise SystemExit(f"Unsupported gateway platform: {platform}")
        if mapped not in seen:
            seen.add(mapped)
            normalized.append(mapped)
    return normalized


def ensure_gateway_dirs() -> Path:
    base = project_path("data/gateway")
    base.mkdir(parents=True, exist_ok=True)
    return base


def missing_gateway_dependencies(platforms: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for platform in normalize_gateway_platforms(platforms):
        for module_name, package_name in PLATFORM_DEPS.get(platform, []):
            if importlib.util.find_spec(module_name) is None:
                missing.append(package_name)
    # Preserve order but drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for package_name in missing:
        if package_name in seen:
            continue
        seen.add(package_name)
        unique.append(package_name)
    return unique


def dependency_install_hint(packages: Sequence[str]) -> str:
    package_text = ", ".join(packages) if packages else "gateway dependencies"
    return (
        f"Missing {package_text}. Install with: "
        'pip install -e ".[gateway]"'
    )


def gateway_process_argv(platforms: Sequence[str], *, shared_groups: bool = False) -> list[str]:
    normalized = normalize_gateway_platforms(platforms)
    if not normalized:
        raise SystemExit("No gateway platform selected.")
    argv = [sys.executable, "-m", "chrysalis.gateway.main", *normalized]
    if shared_groups:
        argv.append("--shared-groups")
    return argv


def gateway_process_command(platforms: Sequence[str], *, shared_groups: bool = False) -> str:
    return subprocess.list2cmdline(gateway_process_argv(platforms, shared_groups=shared_groups))


def start_gateway_process(
    platforms: Sequence[str],
    *,
    shared_groups: bool = False,
    visible: bool = True,
) -> GatewayLaunchResult:
    ensure_gateway_dirs()
    normalized = normalize_gateway_platforms(platforms)
    missing = missing_gateway_dependencies(normalized)
    if missing:
        raise SystemExit(dependency_install_hint(missing))

    argv = gateway_process_argv(normalized, shared_groups=shared_groups)
    command = gateway_process_command(normalized, shared_groups=shared_groups)
    actual_visible = bool(visible and os.name == "nt")
    if actual_visible:
        wrapped = ["cmd.exe", "/k", command]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        proc = subprocess.Popen(
            wrapped,
            cwd=str(PROJECT_ROOT),
            creationflags=creationflags,
        )
        return GatewayLaunchResult(normalized, f"cmd /k {command}", proc.pid, True)

    log_dir = ensure_gateway_dirs()
    log_file = log_dir / f"gateway_connect_{int(time.time())}.log"
    log_handle = log_file.open("a", encoding="utf-8", buffering=1)
    try:
        kwargs: dict[str, object] = {
            "cwd": str(PROJECT_ROOT),
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(argv, **kwargs)
    finally:
        try:
            log_handle.close()
        except OSError:
            pass
    return GatewayLaunchResult(normalized, command, proc.pid, actual_visible, log_file)


def run_gateway_foreground(platforms: Sequence[str], *, shared_groups: bool = False) -> None:
    normalized = normalize_gateway_platforms(platforms)
    missing = missing_gateway_dependencies(normalized)
    if missing:
        raise SystemExit(dependency_install_hint(missing))
    ensure_gateway_dirs()
    from chrysalis.gateway.main import _run

    print(f"[Gateway] launching in foreground: {', '.join(normalized)}", flush=True)
    asyncio.run(_run(normalized, group_sessions_per_user=not shared_groups))


def build_connect_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chrysalis connect",
        description="Bootstrap and run Chrysalis gateway adapters.",
    )
    parser.add_argument(
        "platforms",
        nargs="*",
        default=["wechat"],
        help="Platforms to connect. Example: chrysalis connect wechat",
    )
    parser.add_argument(
        "--shared-groups",
        action="store_true",
        help="Use one shared session per group/thread instead of one per user.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Launch the gateway in a new process and return immediately.",
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help="Run the detached process without a visible console window.",
    )
    return parser


def run_connect_cli(argv: Sequence[str]) -> None:
    parser = build_connect_parser()
    args = parser.parse_args(list(argv))
    platforms = normalize_gateway_platforms(args.platforms)

    if args.background:
        result = start_gateway_process(
            platforms,
            shared_groups=args.shared_groups,
            visible=not args.hidden,
        )
        _print_launch_result(result)
        return

    run_gateway_foreground(platforms, shared_groups=args.shared_groups)


def format_launch_summary(result: GatewayLaunchResult) -> str:
    platform_text = ", ".join(result.platforms)
    lines = [
        f"Gateway started: {platform_text}",
        f"PID: {result.pid}" if result.pid else "PID: unknown",
        f"Mode: {'visible' if result.visible else 'detached'}",
        f"Command: {result.command}",
    ]
    if result.log_file:
        lines.append(f"Log: {result.log_file}")
    if "qq_personal" in result.platforms:
        lines.append("Personal QQ expects a OneBot v11 WebSocket, for example NapCat on ws://127.0.0.1:3001.")
    if "wechat_personal" in result.platforms:
        lines.append("WeChat will show a QR code in the launched console window.")
    if "feishu" in result.platforms:
        lines.append("Feishu expects a self-built app bot with event subscription WebSocket enabled.")
    return "\n".join(lines)


def _print_launch_result(result: GatewayLaunchResult) -> None:
    print(format_launch_summary(result), flush=True)
