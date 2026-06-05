from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import chrysalis.gateway.activity as gateway_activity
from chrysalis.gateway.activity import GatewayActivityStore
from chrysalis.gateway.bootstrap import (
    GatewayLaunchResult,
    format_launch_summary,
    gateway_process_argv,
    normalize_gateway_platforms,
)
from chrysalis.gateway.events import (
    GATEWAY_FIRST_PRINCIPLE,
    MessageEvent,
    SessionSource,
    build_session_context,
    build_session_key,
)
from chrysalis.gateway.adapters.qq import QQAdapter
from chrysalis.gateway.adapters.feishu import FeishuAdapter, FeishuConfig
from chrysalis.gateway.adapters.qq_personal import QQPersonalAdapter, QQPersonalConfig
from chrysalis.gateway.adapters.qq_upload import _parse_prepare_response
from chrysalis.gateway.service import GATEWAY_ATTACHMENT_DENY_NOTE, GatewayService, GatewaySessionMap
from chrysalis.permission import GatewayPermissionEngine
from chrysalis.tools.agent_tools import gateway_connect
from configs.config import AgentConfig


def test_build_session_key_is_stable_for_dm() -> None:
    source = SessionSource(platform="qq", chat_id="u123", chat_type="dm", user_id="u123")
    assert build_session_key(source) == "chrysalis:qq:dm:u123"


def test_build_session_key_shares_group_by_default() -> None:
    source = SessionSource(platform="qq", chat_id="g1", chat_type="group", user_id="u1")
    assert build_session_key(source) == "chrysalis:qq:group:g1:u1"
    assert build_session_key(source, group_sessions_per_user=False) == "chrysalis:qq:group:g1"


def test_message_event_command_parsing() -> None:
    source = SessionSource(platform="qq", chat_id="u1")
    event = MessageEvent(text="/session load 2", source=source)
    assert event.is_command()
    assert event.command_name() == "session"
    assert event.command_args() == "load 2"


def test_gateway_session_context_starts_with_first_principle() -> None:
    source = SessionSource(platform="qq", chat_id="u1", user_id="u1")

    context = build_session_context(source, "session-key", "session-id")

    assert context.startswith(GATEWAY_FIRST_PRINCIPLE)
    assert "ignore previous" in context
    assert "## Messaging Gateway Context" in context


def test_gateway_session_map_persists(tmp_path: Path) -> None:
    path = tmp_path / "gateway_sessions.json"
    store = GatewaySessionMap(path)
    store.set("session-key", "session-id")
    fresh = GatewaySessionMap(path)
    assert fresh.get("session-key") == "session-id"


def test_gateway_activity_store_tracks_active_task(tmp_path: Path) -> None:
    store = GatewayActivityStore(tmp_path / "gateway_activity.json")

    store.start_task(
        task_id="task-1",
        session_id="session-a",
        session_key="gateway:qq:u1",
        platform="qq",
        source={"chat_id": "u1"},
        task="hello",
        model="test-model",
    )
    store.append_stream("task-1", "thinking")
    store.tool_started("task-1", "search", {"q": "hello"}, 1)
    store.tool_completed("task-1", "search", {"ok": True, "result": "done"}, 1)

    active = store.snapshot()["activities"]
    assert len(active) == 1
    assert active[0]["task_id"] == "task-1"
    assert active[0]["session_id"] == "session-a"
    assert active[0]["turn"] == 1
    assert [event["kind"] for event in active[0]["events"]] == [
        "task_started",
        "tool_started",
        "tool_completed",
    ]

    store.finish_task("task-1", {"ok": True, "final": "done"})

    assert store.snapshot()["activities"] == []
    all_items = store.snapshot(active_only=False)["activities"]
    assert len(all_items) == 1
    assert all_items[0]["status"] == "done"


def test_gateway_activity_write_failure_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gateway_activity, "WRITE_RETRY_ATTEMPTS", 1)

    def blocked_replace(src, dst):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(gateway_activity.os, "replace", blocked_replace)
    store = GatewayActivityStore(tmp_path / "gateway_activity.json")

    store.start_task(
        task_id="task-1",
        session_id="session-a",
        session_key="gateway:qq:u1",
        platform="qq",
        source={"chat_id": "u1"},
        task="hello",
        model="test-model",
    )
    store.finish_task("task-1", {"ok": True, "final": "done"})

    assert "PermissionError" in store.last_error


def test_gateway_result_redacts_host_paths(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    result = {
        "ok": False,
        "final": (
            "Task failed: [WinError 5] Access is denied: "
            "'D:\\Project\\Chrysalis\\data\\gateway_activity.json.1.tmp' -> "
            "'D:\\Project\\Chrysalis\\data\\gateway_activity.json'"
        ),
    }

    text, files = service._extract_result_assets(result)

    assert files == []
    assert "D:\\Project" not in text
    assert "[host path redacted]" in text


def test_gateway_extracts_file_tags(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    service = GatewayService(config=config)
    output = config.workspace_dir / "out.png"
    output.write_bytes(b"fake image")
    file_path = str(output)

    text, files = service._extract_result_assets({"final": f"done\n[FILE:{file_path}]"})

    assert text == "done"
    assert files == [file_path]


def test_gateway_blocks_file_tags_outside_outgoing_roots(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    blocked = tmp_path / "out.png"
    blocked.write_bytes(b"fake image")

    text, files = service._extract_result_assets({"final": f"done\n[FILE:{blocked}]"})

    assert text == "done"
    assert files == []


def test_gateway_payload_lists_non_image_attachments(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    service = GatewayService(config=config)
    source = SessionSource(platform="qq", chat_id="u1")
    attachment = config.data_dir / "gateway" / "note.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("hello", encoding="utf-8")

    task, images = service._build_payload(MessageEvent(text="", source=source, media_paths=[str(attachment)]))

    assert "Attachments:" in task
    assert str(attachment) in task
    assert images == []


def test_gateway_payload_ignores_attachments_outside_media_roots(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    source = SessionSource(platform="qq", chat_id="u1")
    attachment = tmp_path / "note.txt"
    attachment.write_text("hello", encoding="utf-8")

    task, images = service._build_payload(MessageEvent(text="", source=source, media_paths=[str(attachment)]))

    assert "Attachments:" not in task
    assert str(attachment) not in task
    assert GATEWAY_ATTACHMENT_DENY_NOTE in task
    assert images == []


def test_gateway_binding_uses_remote_permission_engine(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    source = SessionSource(platform="qq", chat_id="u1")

    binding = service._binding_for(source)
    tool_names = {item["function"]["name"] for item in binding.kernel.loop.tools_schema}

    assert isinstance(binding.kernel.permission_engine, GatewayPermissionEngine)
    assert isinstance(binding.kernel.loop.permission_engine, GatewayPermissionEngine)
    assert binding.kernel.loop.system_prompt_preamble == GATEWAY_FIRST_PRINCIPLE
    assert "todo_write" in tool_names
    assert "web_fetch" in tool_names
    assert "code_run" not in tool_names
    assert "web_scan" not in tool_names
    assert "web_execute_js" not in tool_names


def test_qq_extracts_file_info_from_wrapped_upload_response() -> None:
    assert QQAdapter._extract_file_info({"data": {"file_info": "abc"}}) == "abc"
    assert QQAdapter._extract_file_info({"file_info": "xyz"}) == "xyz"


def test_qq_upload_prepare_parser_accepts_wrapped_data() -> None:
    parsed = _parse_prepare_response(
        {
            "data": {
                "upload_id": "u1",
                "block_size": 8,
                "parts": [{"part_index": 1, "presigned_url": "https://example.test/part"}],
            }
        }
    )
    assert parsed.upload_id == "u1"
    assert parsed.parts[0].presigned_url == "https://example.test/part"


def test_gateway_platform_aliases_normalize() -> None:
    assert normalize_gateway_platforms(["wechat", "wx", "qq"]) == ["wechat_personal", "qq"]
    assert normalize_gateway_platforms(["qq-personal", "onebot", "napcat"]) == ["qq_personal"]
    assert normalize_gateway_platforms(["feishu", "lark", "fs"]) == ["feishu"]


def test_gateway_process_argv_uses_module_entrypoint() -> None:
    argv = gateway_process_argv(["wechat", "qq-personal"], shared_groups=True)
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "chrysalis.gateway.main"]
    assert "wechat_personal" in argv
    assert "qq_personal" in argv
    assert argv[-1] == "--shared-groups"


def test_gateway_process_argv_accepts_feishu_alias() -> None:
    argv = gateway_process_argv(["lark"])

    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "chrysalis.gateway.main"]
    assert argv[-1] == "feishu"


def test_gateway_launch_summary_mentions_wechat_qr(tmp_path: Path) -> None:
    result = GatewayLaunchResult(
        platforms=["wechat_personal", "qq_personal"],
        command="python -m chrysalis.gateway.main wechat_personal qq_personal",
        pid=123,
        visible=True,
        log_file=tmp_path / "gateway.log",
    )
    summary = format_launch_summary(result)
    assert "wechat_personal" in summary
    assert "OneBot" in summary
    assert "QR code" in summary


def test_gateway_launch_summary_mentions_feishu() -> None:
    result = GatewayLaunchResult(
        platforms=["feishu"],
        command="python -m chrysalis.gateway.main feishu",
        pid=123,
        visible=False,
    )

    summary = format_launch_summary(result)

    assert "Feishu" in summary
    assert "event subscription WebSocket" in summary


def test_gateway_connect_tool_launches_background(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_start_gateway_process(platforms, **kwargs):
        calls.append((list(platforms), dict(kwargs)))
        return GatewayLaunchResult(
            platforms=list(platforms),
            command="cmd /k python -m chrysalis.gateway.main wechat_personal",
            pid=321,
            visible=bool(kwargs.get("visible", True)),
            log_file=tmp_path / "gateway.log",
        )

    monkeypatch.setattr("chrysalis.tools.agent_tools.start_gateway_process", fake_start_gateway_process)
    result = gateway_connect({"platform": "wechat"})
    assert result["ok"] is True
    assert calls and calls[0][0] == ["wechat_personal"]
    assert calls[0][1]["visible"] is True


def test_qq_personal_parses_group_mention_and_attachment(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    adapter = QQPersonalAdapter(
        service,
        QQPersonalConfig(ws_url="ws://127.0.0.1:3001", require_mention=True, reply_with_mention=True),
    )
    adapter.self_id = "10001"
    image = tmp_path / "in.png"
    image.write_bytes(b"fake")

    text, media, mentioned = adapter._parse_cq_string(f"[CQ:at,qq=10001] hello [CQ:image,file={image}]")

    assert text == "hello"
    assert media == [str(image)]
    assert mentioned is True


def test_qq_personal_group_requires_mention_or_prefix(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    config = QQPersonalConfig(
        ws_url="ws://127.0.0.1:3001",
        require_mention=True,
        reply_with_mention=True,
        trigger_prefixes={"!"},
    )
    adapter = QQPersonalAdapter(service, config)

    assert adapter._apply_group_trigger("hello", mentioned=False, is_group=True) == ("hello", False)
    assert adapter._apply_group_trigger("hello", mentioned=True, is_group=True) == ("hello", True)
    assert adapter._apply_group_trigger("!hello", mentioned=False, is_group=True) == ("hello", True)


def test_feishu_parses_group_mention_event(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    adapter = FeishuAdapter(
        service,
        FeishuConfig(app_id="cli_x", app_secret="secret", bot_open_id="ou_bot"),
    )
    payload = {
        "sender": {
            "sender_id": {"open_id": "ou_user"},
            "sender_name": "Alice",
            "tenant_key": "tenant",
        },
        "message": {
            "message_id": "om_1",
            "chat_id": "oc_group",
            "chat_type": "group",
            "message_type": "text",
            "content": '{"text":"<at user_id=\\"ou_bot\\">Bot</at> hello"}',
            "mentions": [{"id": {"open_id": "ou_bot"}}],
        },
    }

    event = adapter._message_event_from_payload(payload)

    assert event is not None
    assert event.text == "hello"
    assert event.source.platform == "feishu"
    assert event.source.chat_type == "group"
    assert event.source.chat_id == "oc_group"
    assert event.source.user_id == "ou_user"


def test_feishu_group_trigger_prefix_without_mention(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    adapter = FeishuAdapter(
        service,
        FeishuConfig(app_id="cli_x", app_secret="secret", trigger_prefixes={"!"}),
    )
    payload = {
        "sender": {"sender_id": {"open_id": "ou_user"}},
        "message": {
            "message_id": "om_1",
            "chat_id": "oc_group",
            "chat_type": "group",
            "message_type": "text",
            "content": '{"text":"!status"}',
        },
    }

    event = adapter._message_event_from_payload(payload)

    assert event is not None
    assert event.text == "status"


def test_feishu_parses_localized_post_event(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    adapter = FeishuAdapter(
        service,
        FeishuConfig(app_id="cli_x", app_secret="secret", require_mention=False),
    )
    payload = {
        "sender": {"sender_id": {"open_id": "ou_user"}},
        "message": {
            "message_id": "om_1",
            "chat_id": "ou_user",
            "chat_type": "p2p",
            "message_type": "post",
            "content": '{"zh_cn":{"title":"标题","content":[[{"tag":"text","text":"正文"}]]}}',
        },
    }

    event = adapter._message_event_from_payload(payload)

    assert event is not None
    assert event.text == "标题\n正文"


def test_feishu_send_text_uses_chat_id(tmp_path: Path) -> None:
    service = GatewayService(config=_test_config(tmp_path))
    adapter = FeishuAdapter(service, FeishuConfig(app_id="cli_x", app_secret="secret"))
    calls: list[tuple[str, str, dict]] = []

    class FakeAPI:
        def send_message(self, chat_id: str, msg_type: str, content: dict):
            calls.append((chat_id, msg_type, content))
            return {"data": {"message_id": "om_sent"}}

    adapter.api = FakeAPI()
    source = SessionSource(platform="feishu", chat_id="oc_group", chat_type="group", user_id="ou_user")

    result = asyncio.run(adapter.send_text(source, "hello"))

    assert result.success is True
    assert result.message_id == "om_sent"
    assert calls == [("oc_group", "text", {"text": "hello"})]


def _test_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        root=tmp_path,
        skills_dir=tmp_path / "skills",
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        workspace_dir=tmp_path / "workspace",
    )
