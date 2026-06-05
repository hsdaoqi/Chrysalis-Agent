"""CLI entry point for messaging gateway adapters."""

from __future__ import annotations

import argparse
import asyncio

from chrysalis.gateway.adapters.feishu import FeishuAdapter
from chrysalis.gateway.adapters.qq import QQAdapter
from chrysalis.gateway.adapters.qq_personal import QQPersonalAdapter
from chrysalis.gateway.adapters.wechat_personal import WeChatPersonalAdapter
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
    selected = [PLATFORM_ALIASES[p] for p in args.platforms]
    asyncio.run(_run(selected, group_sessions_per_user=not args.shared_groups))


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


if __name__ == "__main__":
    main()
