from __future__ import annotations

from pathlib import Path

from chrysalis import browser as browser_module
from chrysalis.browser import BACKEND_CDP, BACKEND_PLUGIN, BrowserController, BridgeCandidate


class FakePluginBrowser(BrowserController):
    def __init__(self) -> None:
        super().__init__(port=65500)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.launch_attempted = False
        self.tabs = [{"id": "tab-1", "url": "https://example.test", "title": "Example"}]

    def _plugin_bridge_candidates(self) -> list[BridgeCandidate]:
        return [BridgeCandidate(url="http://127.0.0.1:17321", source="test")]

    def _bridge_json(self, path: str, timeout: int = 5, method: str = "GET", payload: dict | None = None):
        self.calls.append((method, path, payload))
        if path == "/health":
            return {"ok": True, "name": "chrysalis_browser_bridge"}
        if path == "/tabs":
            if method == "POST":
                tab = {"id": "tab-2", "url": payload["url"], "title": "Opened"}
                self.tabs.append(tab)
                return {"ok": True, "tab": tab}
            return {"tabs": self.tabs}
        if path == "/execute":
            return {"ok": True, "result": "<html><title>Example</title><body>Hello</body></html>"}
        raise AssertionError(path)

    def _cdp_available(self) -> bool:
        return False

    def _attach_running_cdp(self) -> bool:
        return False


class FakeFallbackBrowser(BrowserController):
    def __init__(self) -> None:
        super().__init__(port=65501)
        self.attached = False

    def _discover_plugin_bridge(self):
        return None

    def _cdp_available(self) -> bool:
        return False

    def _attach_running_cdp(self) -> bool:
        self.attached = True
        return True


def test_plugin_bridge_is_preferred_over_cdp_launch() -> None:
    controller = FakePluginBrowser()

    result = controller.scan(tabs_only=True)

    assert result["ok"] is True
    assert result["backend"] == BACKEND_PLUGIN
    assert result["tabs"][0]["id"] == "tab-1"
    assert controller.process is None
    assert controller.calls[0][1] == "/health"


def test_plugin_bridge_opens_and_executes_in_existing_browser() -> None:
    controller = FakePluginBrowser()

    result = controller.scan("https://opened.test", wait_ms=0)

    assert result["ok"] is True
    assert result["backend"] == BACKEND_PLUGIN
    assert result["active_tab"] == "tab-2"
    assert result["content"]["text"] == "Hello"
    assert ("POST", "/tabs", {"url": "https://opened.test"}) in controller.calls
    assert any(call[1] == "/execute" for call in controller.calls)


def test_cdp_fallback_still_attaches_without_plugin() -> None:
    controller = FakeFallbackBrowser()

    result = controller._ensure_browser()

    assert result["ok"] is True
    assert result["backend"] == BACKEND_CDP
    assert result["opened_with_launch"] is False
    assert controller.attached is True


def test_bridge_candidate_file_accepts_host_port(tmp_path: Path) -> None:
    path = tmp_path / "browser_bridge.json"
    path.write_text('{"host":"127.0.0.1","port":17321,"token":"secret"}', encoding="utf-8")

    candidate = browser_module._bridge_candidate_from_file(path)

    assert candidate is not None
    assert candidate.url == "http://127.0.0.1:17321"
    assert candidate.token == "secret"
