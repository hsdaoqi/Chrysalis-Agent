from pathlib import Path

import chrysalis.tools  # noqa: F401
from chrysalis.llm.image_utils import WindowInfo
from chrysalis.permission import PermissionEngine
from chrysalis.tools.vision_tools import screenshot_tool


def test_screenshot_defaults_to_monitor_capture(monkeypatch) -> None:
    calls = {}

    def fake_capture_screen(monitor: int):
        calls["screen"] = monitor
        return "image/jpeg", "screen-data"

    def fake_capture_window(**kwargs):
        raise AssertionError("window capture should not run")

    monkeypatch.setattr("chrysalis.llm.image_utils.capture_screen", fake_capture_screen)
    monkeypatch.setattr("chrysalis.llm.image_utils.capture_window", fake_capture_window)

    result = screenshot_tool({"monitor": "2"})

    assert result["ok"] is True
    assert calls["screen"] == 2
    assert result["_image"] == {"media_type": "image/jpeg", "data": "screen-data"}
    assert "window" not in result


def test_screenshot_uses_target_window_when_requested(monkeypatch) -> None:
    calls = {}

    def fake_capture_screen(monitor: int):
        raise AssertionError("screen capture should not run")

    def fake_capture_window(title: str = "", pid: int | None = None, exe: str = ""):
        calls.update({"title": title, "pid": pid, "exe": exe})
        return "image/jpeg", "window-data", WindowInfo(101, "Vite Preview", 456, "chrome.exe")

    monkeypatch.setattr("chrysalis.llm.image_utils.capture_screen", fake_capture_screen)
    monkeypatch.setattr("chrysalis.llm.image_utils.capture_window", fake_capture_window)

    result = screenshot_tool({
        "window_title": "Preview",
        "window_pid": "456",
        "window_exe": "chrome",
    })

    assert result["ok"] is True
    assert calls == {"title": "Preview", "pid": 456, "exe": "chrome"}
    assert result["_image"] == {"media_type": "image/jpeg", "data": "window-data"}
    assert result["window"] == {
        "title": "Vite Preview",
        "pid": 456,
        "exe": "chrome.exe",
        "hwnd": 101,
    }


def test_screenshot_rejects_invalid_window_pid(monkeypatch) -> None:
    def fake_capture_screen(monitor: int):
        raise AssertionError("screen capture should not run")

    def fake_capture_window(**kwargs):
        raise AssertionError("window capture should not run")

    monkeypatch.setattr("chrysalis.llm.image_utils.capture_screen", fake_capture_screen)
    monkeypatch.setattr("chrysalis.llm.image_utils.capture_window", fake_capture_window)

    result = screenshot_tool({"window_pid": "not-a-number"})

    assert result["ok"] is False
    assert "window_pid" in result["error"]


def test_permission_describes_target_window_screenshot(tmp_path: Path) -> None:
    engine = PermissionEngine(level="balanced", store_path=tmp_path / "permissions.json")

    decision = engine.assess_tool(
        "screenshot",
        {"window_title": "Preview", "window_exe": "chrome.exe"},
        workspace=tmp_path,
    )

    assert decision.decision == "ask"
    assert "目标窗口" in decision.prompt
    assert decision.details["window_title"] == "Preview"
    assert decision.details["window_exe"] == "chrome.exe"
