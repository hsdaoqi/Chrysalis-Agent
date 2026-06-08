"""本地浏览器桥接服务（复用 GA 的 tmwd_cdp_bridge 扩展）。

架构：
    browser.py --HTTP--> 本服务 --WebSocket--> tmwd_cdp_bridge 扩展 --CDP/scripting--> 页面

GA 原版用 TMWebDriver.py（依赖 simple_websocket_server / bottle / requests）做这一层。
本实现只用标准库重写，零新依赖，可被 PyInstaller 单文件打包，但**对扩展说的是
完全相同的 WebSocket 协议**，所以 GA 的扩展一字不改即可使用。

端口 18765 同时承载两类流量（与扩展硬编码的 `ws://127.0.0.1:18765` 对齐）：
- WebSocket 升级请求：来自扩展的长连接
- 普通 HTTP（/health /tabs /open /execute）：来自 browser.py
- 普通 HTTP GET /：扩展的 isServerAlive() 探测，回任意响应即可

扩展协议（本服务 <-> 扩展，均为 JSON 文本帧）：
    收 {type:'ext_ready'|'tabs_update', tabs:[{id,url,title}]}  -> 刷新标签缓存
    收 {type:'ack', id}                                         -> 命令已送达，重置超时
    收 {type:'result', id, result, newTabs}                     -> 命令成功
    收 {type:'error',  id, error,  newTabs}                     -> 命令失败
    收 {type:'ping'}                                            -> 保活，忽略
    发 {id, code:"<js>", tabId:<int>}                           -> 在指定标签执行 JS
    发 {id, code:'{"cmd":"tabs","method":"create","url":..}'}   -> 走扩展命令路由（开标签等）
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from configs.config import project_path

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765  # 与 tmwd_cdp_bridge 扩展硬编码的 WS_URL 对齐
BRIDGE_FILE = "data/browser_bridge.json"
HEALTH_NAME = "chrysalis_browser_bridge"
COMMAND_TIMEOUT = 30


class _WSConnection:
    """单个扩展 WebSocket 连接：负责 RFC6455 帧编解码与命令收发。"""

    def __init__(self, sock: socket.socket, bridge: "BrowserBridge"):
        self.sock = sock
        self.bridge = bridge
        self._send_lock = threading.Lock()
        self.alive = True

    # --- 命令发送 ---

    def send_command(self, command: dict) -> None:
        self._send_text(json.dumps(command, ensure_ascii=False))

    # --- 读循环 ---

    def reader_loop(self) -> None:
        try:
            while self.alive:
                frame = self._recv_frame()
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping -> pong
                    self._send_frame(0xA, payload)
                    continue
                if opcode in (0x1, 0x2):  # text / binary
                    try:
                        message = json.loads(payload.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    if isinstance(message, dict):
                        self.bridge.on_extension_message(message)
        finally:
            self.close()

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass

    # --- WebSocket 帧编解码（服务端发送不掩码） ---

    def _send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        with self._send_lock:
            self.sock.sendall(bytes(header) + payload)

    def _recv_exact(self, count: int) -> bytes | None:
        chunks = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self.sock.recv(remaining)
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_frame(self) -> tuple[int, bytes] | None:
        first = self._recv_exact(2)
        if first is None:
            return None
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            ext = self._recv_exact(2)
            if ext is None:
                return None
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = self._recv_exact(8)
            if ext is None:
                return None
            length = struct.unpack(">Q", ext)[0]
        mask = b""
        if masked:
            mask = self._recv_exact(4)
            if mask is None:
                return None
        payload = self._recv_exact(length) if length else b""
        if payload is None:
            return None
        if masked and mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload


class BrowserBridge:
    """管理扩展连接、标签缓存、命令收发，并对 browser.py 暴露 HTTP。"""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._connection: _WSConnection | None = None
        self._conn_lock = threading.Lock()
        self._tabs: list[dict] = []
        self._tabs_lock = threading.Lock()
        self._results: dict[str, dict] = {}
        self._acks: set[str] = set()
        self._results_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    # --- 扩展连接管理 ---

    def set_connection(self, connection: _WSConnection | None) -> None:
        with self._conn_lock:
            old = self._connection
            self._connection = connection
        if old is not None and old is not connection:
            old.close()

    def _live_connection(self) -> _WSConnection | None:
        with self._conn_lock:
            conn = self._connection
        if conn is not None and not conn.alive:
            with self._conn_lock:
                if self._connection is conn:
                    self._connection = None
            return None
        return conn

    @property
    def extension_connected(self) -> bool:
        return self._live_connection() is not None

    def on_extension_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type in ("ext_ready", "tabs_update"):
            tabs = message.get("tabs", [])
            cleaned = [
                {"id": str(t.get("id")), "url": t.get("url", ""), "title": t.get("title", "")}
                for t in tabs
                if isinstance(t, dict) and t.get("id") is not None
            ]
            with self._tabs_lock:
                self._tabs = cleaned
        elif msg_type == "ack":
            mid = message.get("id", "")
            with self._results_lock:
                self._acks.add(mid)
        elif msg_type in ("result", "error"):
            mid = message.get("id", "")
            entry = {
                "ok": msg_type == "result",
                "data": message.get("result"),
                "error": message.get("error"),
                "newTabs": message.get("newTabs", []),
            }
            with self._results_lock:
                self._results[mid] = entry
        # 'ping' 等其它类型：忽略

    # --- 命令收发（HTTP 线程调用，阻塞等扩展回传） ---

    def _call(self, code, tab_id: int | None, timeout: int) -> dict:
        conn = self._live_connection()
        if conn is None:
            return {"ok": False, "error": "浏览器扩展未连接（请确认已安装并启用 tmwd_cdp_bridge 扩展，且浏览器已打开网页）"}

        exec_id = str(uuid.uuid4())
        command: dict = {"id": exec_id, "code": code}
        if tab_id is not None:
            command["tabId"] = tab_id
        try:
            conn.send_command(command)
        except Exception as exc:
            conn.close()
            return {"ok": False, "error": f"发送命令到扩展失败：{exc}"}

        deadline = time.time() + timeout
        acked = False
        while time.time() < deadline:
            with self._results_lock:
                if exec_id in self._results:
                    result = self._results.pop(exec_id, None)
                    self._acks.discard(exec_id)
                    break
                if not acked and exec_id in self._acks:
                    acked = True
                    deadline = time.time() + timeout  # 命令已送达，重置计时
            if not conn.alive:
                with self._results_lock:
                    self._acks.discard(exec_id)
                return {"ok": False, "error": "扩展连接在等待结果时断开"}
            time.sleep(0.1)
        else:
            with self._results_lock:
                self._acks.discard(exec_id)
                self._results.pop(exec_id, None)
            hint = "（命令已送达，脚本可能仍在执行）" if acked else "（命令未确认送达）"
            return {"ok": False, "error": f"扩展命令超时 {timeout}s {hint}"}

        if result is None:
            return {"ok": False, "error": "扩展未返回结果"}
        out = {"ok": result["ok"]}
        if result["ok"]:
            out["result"] = result["data"]
        else:
            out["error"] = result.get("error") or "扩展执行失败"
        if result.get("newTabs"):
            out["newTabs"] = result["newTabs"]
        return out

    def list_tabs(self) -> dict:
        if not self.extension_connected:
            return {"ok": False, "error": "浏览器扩展未连接", "tabs": []}
        with self._tabs_lock:
            tabs = [dict(t) for t in self._tabs]
        return {"ok": True, "tabs": tabs}

    def execute_js(self, script: str, tab_id, timeout: int) -> dict:
        int_tab = _coerce_tab_id(tab_id)
        if tab_id is not None and int_tab is None:
            return {"ok": False, "error": f"非法 tab_id: {tab_id}"}
        return self._call(script, int_tab, timeout)

    def open_tab(self, url: str) -> dict:
        command = json.dumps({"cmd": "tabs", "method": "create", "url": url, "active": True})
        result = self._call(command, None, COMMAND_TIMEOUT)
        if not result.get("ok"):
            return result
        data = result.get("result") or {}
        if isinstance(data, dict) and data.get("id") is not None:
            return {"ok": True, "tab": {"id": str(data.get("id")), "url": data.get("url", url), "title": data.get("title", "")}}
        # 回退：从标签缓存里找
        with self._tabs_lock:
            for tab in self._tabs:
                if tab.get("url") == url:
                    return {"ok": True, "tab": dict(tab)}
        return {"ok": True, "tab": {"id": "", "url": url, "title": ""}}

    # --- 生命周期 ---

    def serve_forever(self) -> None:
        self._httpd = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        self._write_bridge_file()
        try:
            self._httpd.serve_forever()
        finally:
            self._remove_bridge_file()

    def shutdown(self) -> None:
        conn = self._live_connection()
        if conn is not None:
            conn.close()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    def _write_bridge_file(self) -> None:
        path = project_path(BRIDGE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": f"http://{self.host}:{self.port}",
            "host": self.host,
            "port": self.port,
            "name": HEALTH_NAME,
            "pid": os.getpid(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _remove_bridge_file(self) -> None:
        path = project_path(BRIDGE_FILE)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _coerce_tab_id(tab_id) -> int | None:
    if tab_id is None or tab_id == "":
        return None
    try:
        return int(tab_id)
    except (TypeError, ValueError):
        return None


def _make_handler(bridge: BrowserBridge):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # 静默
            pass

        def do_GET(self) -> None:
            if self._maybe_websocket():
                return
            path = self.path.split("?")[0]
            if path == "/health":
                self._json(200, {
                    "ok": True,
                    "chrysalis": True,
                    "name": HEALTH_NAME,
                    "extension_connected": bridge.extension_connected,
                })
            elif path == "/tabs":
                self._json(200, bridge.list_tabs())
            else:
                # 扩展的 isServerAlive() 探测会打到这里，回任意 JSON 即可
                self._json(200, {"ok": True, "name": HEALTH_NAME})

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            payload = self._read_json()
            if payload is None:
                self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})
                return
            if path == "/open":
                url = str(payload.get("url") or "").strip()
                if not url:
                    self._json(400, {"ok": False, "error": "缺少 url"})
                    return
                self._json(200, bridge.open_tab(url))
            elif path in ("/execute", "/execute_js"):
                timeout = int(payload.get("timeout") or COMMAND_TIMEOUT)
                self._json(200, bridge.execute_js(payload.get("script", ""), payload.get("tab_id"), timeout))
            elif path == "/tabs":
                url = str(payload.get("url") or "").strip()
                self._json(200, bridge.open_tab(url) if url else bridge.list_tabs())
            else:
                self._json(404, {"ok": False, "error": f"未知路径: {path}"})

        # --- WebSocket 升级（扩展连接） ---

        def _maybe_websocket(self) -> bool:
            if self.headers.get("Upgrade", "").lower() != "websocket":
                return False
            key = self.headers.get("Sec-WebSocket-Key", "")
            if not key:
                self._json(400, {"ok": False, "error": "缺少 Sec-WebSocket-Key"})
                return True
            accept = base64.b64encode(
                hashlib.sha1((key + WS_MAGIC).encode("utf-8")).digest()
            ).decode("ascii")
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            connection = _WSConnection(self.connection, bridge)
            bridge.set_connection(connection)
            connection.reader_loop()  # 阻塞直到扩展断开
            return True

        # --- 辅助 ---

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return None
            return data if isinstance(data, dict) else None

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


def is_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """探测端口上是否已有 chrysalis 桥接服务在跑。"""
    import urllib.request

    try:
        request = urllib.request.Request(f"http://{host}:{port}/health")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return isinstance(data, dict) and data.get("chrysalis") is True
    except Exception:
        return False


def ensure_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, wait: float = 2.0) -> bool:
    """确保桥接服务在跑：已在跑返回 True；否则后台线程起一个。

    返回值表示调用结束时服务是否可用。端口被别的进程占用时返回 False。
    """
    if is_running(host, port):
        return True
    bridge = BrowserBridge(host=host, port=port)
    try:
        thread = threading.Thread(target=bridge.serve_forever, daemon=True, name="chrysalis-browser-bridge")
        thread.start()
    except Exception:
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if is_running(host, port):
            return True
        time.sleep(0.1)
    return is_running(host, port)


def main() -> None:
    host = os.environ.get("CHRYSALIS_BRIDGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CHRYSALIS_BRIDGE_PORT", DEFAULT_PORT))
    bridge = BrowserBridge(host=host, port=port)
    print(f"[chrysalis-bridge] HTTP+WS 监听 http://{host}:{port}（等待 tmwd_cdp_bridge 扩展连接）")
    try:
        bridge.serve_forever()
    except KeyboardInterrupt:
        bridge.shutdown()


if __name__ == "__main__":
    main()
