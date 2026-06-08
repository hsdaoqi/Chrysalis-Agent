"""桥接服务测试：用真实 HTTP+WS server + websocket-client 模拟 tmwd_cdp_bridge 扩展。

不依赖真实浏览器，只验证 Chrysalis 桥接服务与 GA 扩展协议的对接是否正确。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest

from chrysalis.browser_bridge import BrowserBridge

try:
    import websocket  # websocket-client，已是项目依赖
except Exception:  # pragma: no cover
    websocket = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(port: int, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode())


class _FakeExtension:
    """模拟扩展：连上后发 ext_ready，对每条命令回 ack + result。"""

    def __init__(self, port: int):
        self.ws = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=5)
        self.stop = threading.Event()
        self.ws.send(json.dumps({
            "type": "ext_ready",
            "tabs": [{"id": 101, "url": "https://example.com", "title": "Example"}],
        }))
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.ws.settimeout(0.2)
                raw = self.ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            mid = data.get("id")
            if not mid:
                continue
            self.ws.send(json.dumps({"type": "ack", "id": mid}))
            code = data.get("code")
            if isinstance(code, str) and code.startswith("{"):
                obj = json.loads(code)
                if obj.get("cmd") == "tabs" and obj.get("method") == "create":
                    self.ws.send(json.dumps({"type": "result", "id": mid,
                                             "result": {"id": 202, "url": obj.get("url"), "title": "Opened"}}))
                    continue
            self.ws.send(json.dumps({"type": "result", "id": mid,
                                     "result": f"ran:{code}", "newTabs": []}))

    def close(self) -> None:
        self.stop.set()
        try:
            self.ws.close()
        except Exception:
            pass


@pytest.fixture
def port():
    p = _free_port()
    b = BrowserBridge(port=p)
    threading.Thread(target=b.serve_forever, daemon=True).start()
    time.sleep(0.4)
    yield p
    b.shutdown()
    time.sleep(0.2)


@pytest.mark.skipif(websocket is None, reason="websocket-client 不可用")
def test_health_reflects_extension_connection(port):
    assert _http(port, "GET", "/health")["extension_connected"] is False
    ext = _FakeExtension(port)
    time.sleep(0.5)
    try:
        assert _http(port, "GET", "/health")["extension_connected"] is True
    finally:
        ext.close()


@pytest.mark.skipif(websocket is None, reason="websocket-client 不可用")
def test_execute_fails_without_extension(port):
    result = _http(port, "POST", "/execute", {"script": "1+1"})
    assert result["ok"] is False
    assert "扩展" in result["error"]


@pytest.mark.skipif(websocket is None, reason="websocket-client 不可用")
def test_execute_and_tabs_and_open_via_extension(port):
    ext = _FakeExtension(port)
    time.sleep(0.5)
    try:
        tabs = _http(port, "GET", "/tabs")
        assert tabs["ok"] is True
        assert tabs["tabs"][0]["id"] == "101"

        run = _http(port, "POST", "/execute", {"script": "document.title", "tab_id": "101"})
        assert run["ok"] is True
        assert run["result"] == "ran:document.title"

        opened = _http(port, "POST", "/open", {"url": "https://opened.test"})
        assert opened["ok"] is True
        assert opened["tab"]["id"] == "202"
        assert opened["tab"]["url"] == "https://opened.test"
    finally:
        ext.close()
