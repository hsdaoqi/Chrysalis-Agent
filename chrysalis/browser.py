"""GA 风格的浏览器适配层。

对外只保留两个能力：
- web_scan：打开/扫描真实浏览器页面
- web_execute_js：在真实浏览器标签页执行 JS

底层使用本机 Chrome/Edge 的 CDP 调试协议，不下载浏览器。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from configs.config import project_path


MAX_HTML_CHARS = 180_000
MAX_TEXT_CHARS = 30_000
MAX_JS_CHARS = 20_000
DEFAULT_TIMEOUT = 15
DEFAULT_WAIT_MS = 1000
IGNORED_TAGS = {"script", "style", "noscript", "meta", "link", "svg", "canvas", "template"}
USER_ACTION_PATTERNS = (
    "请先登录",
    "登录后查看",
    "登录后继续",
    "登录后可见",
    "登录即可",
    "扫码登录",
    "验证码登录",
    "手机登录",
    "密码登录",
    "未登录",
    "sign in to continue",
    "log in to continue",
    "please sign in",
    "please log in",
    "请完成验证",
    "安全验证",
    "人机验证",
    "滑块验证",
    "拖动滑块",
    "请输入验证码",
    "短信验证码",
    "扫码验证",
    "验证身份",
    "需要验证",
    "请授权",
    "授权后继续",
    "请确认",
    "确认继续",
    "请选择",
    "请上传",
    "上传文件",
    "选择文件",
    "请选择文件",
    "请支付",
    "立即支付",
    "captcha",
    "verify you are human",
    "human verification",
    "verification required",
    "complete verification",
    "choose file",
    "upload file",
    "authorize",
    "payment required",
)


@dataclass
class BrowserTab:
    id: str
    url: str
    title: str = ""
    type: str = "page"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "type": self.type,
        }


class BrowserController:
    """通过 CDP 控制本机真实浏览器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222, user_data_dir: Path | None = None):
        self.host = host
        self.port = port
        self.user_data_dir = user_data_dir or project_path("workspace/browser_profile")
        self.active_tab_id: str | None = None
        self.process: subprocess.Popen | None = None

    def scan(
        self,
        url: str | None = None,
        tab_id: str | None = None,
        tabs_only: bool = False,
        text_only: bool = False,
        wait_ms: int = DEFAULT_WAIT_MS,
    ) -> dict:
        """打开或扫描真实浏览器页面。"""
        ready = self._ensure_browser(url)
        if not ready.get("ok"):
            return ready

        if url and not ready.get("opened_with_launch"):
            opened = self._open_tab(url)
            if not opened.get("ok"):
                return opened
            self.active_tab_id = opened["tab"]["id"]

        if tab_id:
            self.active_tab_id = tab_id

        tabs = self._tabs()
        if not tabs:
            return {"ok": False, "error": "没有可用的浏览器标签页。", "backend": "cdp", "tabs": []}
        if self.active_tab_id is None or not any(tab["id"] == self.active_tab_id for tab in tabs):
            self.active_tab_id = tabs[0]["id"]

        if tabs_only:
            return self._scan_result(tabs=tabs, content=None, text_only=text_only)

        if wait_ms > 0:
            time.sleep(min(wait_ms, 10_000) / 1000)

        html_result = self.execute_js("document.documentElement.outerHTML", self.active_tab_id, timeout=DEFAULT_TIMEOUT)
        if not html_result.get("ok"):
            return html_result
        active = self._active_tab()
        html = str(html_result.get("result") or "")[:MAX_HTML_CHARS]
        content = simplify_html(
            html,
            url=active.get("url", "") if active else "",
            title=active.get("title", "") if active else "",
            text_only=text_only,
        )
        result = self._scan_result(tabs=self._tabs(), content=content, text_only=text_only)
        if isinstance(content, dict) and content.get("user_action_required"):
            result.update(_user_action_intervention(content))
        return result

    def execute_js(self, script: str, tab_id: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """在真实浏览器标签页执行 JS。"""
        error = _validate_script(script)
        if error:
            return {"ok": False, "error": error, "backend": "cdp"}

        ready = self._ensure_browser()
        if not ready.get("ok"):
            return ready
        tabs = self._tabs()
        if not tabs:
            return {"ok": False, "error": "没有可执行 JS 的浏览器标签页。", "backend": "cdp", "tabs": []}

        target_id = tab_id or self.active_tab_id or tabs[0]["id"]
        tab = self._find_tab(target_id)
        if tab is None:
            return {"ok": False, "error": f"未知标签页: {target_id}", "backend": "cdp", "tabs": tabs}

        expression = _normalize_script(script)
        before = {"url": tab.get("url", ""), "title": tab.get("title", "")}
        try:
            result = self._cdp_call(
                tab["webSocketDebuggerUrl"],
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                    "userGesture": True,
                },
                timeout=timeout,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "backend": "cdp", "active_tab": target_id}

        remote = result.get("result", {})
        if "exceptionDetails" in result:
            return {
                "ok": False,
                "error": _format_exception(result["exceptionDetails"]),
                "backend": "cdp",
                "active_tab": target_id,
            }
        value = remote.get("value", remote.get("description"))
        self.active_tab_id = target_id
        after_tab = self._find_tab(target_id) or tab
        after = {"url": after_tab.get("url", ""), "title": after_tab.get("title", "")}
        return {
            "ok": True,
            "backend": "cdp",
            "active_tab": target_id,
            "result": value,
            "before": before,
            "after": after,
            "changed": before != after,
        }

    def _ensure_browser(self, initial_url: str | None = None) -> dict:
        if self._cdp_available():
            return {"ok": True, "backend": "cdp", "opened_with_launch": False}

        browser = _find_browser_executable()
        if browser is None:
            return {
                "ok": False,
                "backend": "cdp",
                "error": "没有找到可用的 Chrome 或 Edge 浏览器，无法完成真实浏览器操作。",
            }

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        command = [
            str(browser),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if initial_url:
            command.append(initial_url)

        flags = 0
        if sys.platform.startswith("win"):
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

        deadline = time.time() + 10
        while time.time() < deadline:
            if self._cdp_available():
                tabs = self._tabs()
                if tabs:
                    self.active_tab_id = tabs[0]["id"]
                return {"ok": True, "backend": "cdp", "opened_with_launch": bool(initial_url)}
            time.sleep(0.2)
        return {
            "ok": False,
            "backend": "cdp",
            "error": "浏览器已启动，但 CDP 调试端口没有及时响应。",
            "port": self.port,
        }

    def _cdp_available(self) -> bool:
        try:
            self._http_json("/json/version", timeout=1)
            return True
        except Exception:
            return False

    def _tabs(self) -> list[dict]:
        try:
            items = self._http_json("/json/list", timeout=3)
        except Exception:
            return []
        tabs = []
        for item in items:
            if item.get("type") != "page":
                continue
            if not item.get("webSocketDebuggerUrl"):
                continue
            tabs.append(
                {
                    "id": item.get("id", ""),
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", "page"),
                    "webSocketDebuggerUrl": item.get("webSocketDebuggerUrl", ""),
                }
            )
        return tabs

    def _open_tab(self, url: str) -> dict:
        encoded = urllib.parse.quote(url, safe=":/?&=%#")
        try:
            item = self._http_json(f"/json/new?{encoded}", timeout=5, method="PUT")
        except Exception as exc:
            return {"ok": False, "backend": "cdp", "error": f"打开新标签页失败：{exc}"}
        if not item.get("id"):
            return {"ok": False, "backend": "cdp", "error": "打开新标签页失败：CDP 没有返回标签页 ID。"}
        return {
            "ok": True,
            "backend": "cdp",
            "tab": BrowserTab(
                id=item.get("id", ""),
                url=item.get("url", url),
                title=item.get("title", ""),
                type=item.get("type", "page"),
            ).to_dict(),
        }

    def _active_tab(self) -> dict | None:
        if self.active_tab_id is None:
            return None
        return self._find_tab(self.active_tab_id)

    def _find_tab(self, tab_id: str) -> dict | None:
        for tab in self._tabs():
            if tab.get("id") == tab_id:
                return tab
        return None

    def _scan_result(self, tabs: list[dict], content: dict | str | None, text_only: bool) -> dict:
        public_tabs = [
            BrowserTab(id=tab["id"], url=tab.get("url", ""), title=tab.get("title", ""), type=tab.get("type", "page")).to_dict()
            for tab in tabs
        ]
        return {
            "ok": True,
            "backend": "cdp",
            "active_tab": self.active_tab_id,
            "tabs": public_tabs,
            "tabs_count": len(public_tabs),
            "text_only": text_only,
            "content": content,
        }

    def _http_json(self, path: str, timeout: int = 5, method: str = "GET") -> Any:
        request = urllib.request.Request(f"http://{self.host}:{self.port}{path}", method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def _cdp_call(self, ws_url: str, method: str, params: dict, timeout: int) -> dict:
        try:
            import websocket
        except Exception as exc:
            raise RuntimeError(f"缺少 websocket-client，无法连接 CDP：{exc}") from exc

        message_id = int(time.time() * 1000) % 1_000_000
        ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": message_id, "method": method, "params": params}, ensure_ascii=False))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ws.recv()
                data = json.loads(raw)
                if data.get("id") == message_id:
                    if "error" in data:
                        raise RuntimeError(data["error"].get("message", str(data["error"])))
                    return data.get("result", {})
            raise TimeoutError(f"CDP 调用超时: {method}")
        finally:
            ws.close()


class _HTMLSummaryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts: list[str] = []
        self.links: list[dict] = []
        self.inputs: list[dict] = []
        self.buttons: list[dict] = []
        self.forms: list[dict] = []
        self._ignored_depth = 0
        self._in_title = False
        self._current_link: dict | None = None
        self._current_button: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag in IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "a" and attrs_dict.get("href"):
            self._current_link = {"text": "", "href": attrs_dict.get("href", "")}
        elif tag in {"input", "textarea", "select"}:
            self.inputs.append(_pick_attrs(attrs_dict, ["type", "name", "id", "placeholder", "value", "aria-label"]))
        elif tag == "button":
            self._current_button = _pick_attrs(attrs_dict, ["type", "name", "id", "aria-label"])
            self._current_button["text"] = ""
        elif tag == "form":
            self.forms.append(_pick_attrs(attrs_dict, ["action", "method", "id", "name"]))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_link is not None:
            self._current_link["text"] = _clean_text(self._current_link.get("text", ""))
            self.links.append(self._current_link)
            self._current_link = None
        elif tag == "button" and self._current_button is not None:
            self._current_button["text"] = _clean_text(self._current_button.get("text", ""))
            self.buttons.append(self._current_button)
            self._current_button = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = _clean_text(data)
        if not text:
            return
        if self._in_title:
            self.title = _clean_text(self.title + " " + text)
            return
        self.text_parts.append(text)
        if self._current_link is not None:
            self._current_link["text"] = _clean_text((self._current_link.get("text") or "") + " " + text)
        if self._current_button is not None:
            self._current_button["text"] = _clean_text(self._current_button.get("text", "") + " " + text)


def simplify_html(html: str, url: str = "", title: str = "", text_only: bool = False) -> dict | str:
    parser = _HTMLSummaryParser()
    parser.feed(html[:MAX_HTML_CHARS])
    page_title = title or parser.title
    text = _clean_text(" ".join(parser.text_parts))[:MAX_TEXT_CHARS]
    if text_only:
        return text
    summary = {
        "url": url,
        "title": page_title,
        "text": text,
        "links": _trim_items(parser.links, 80),
        "forms": _trim_items(parser.forms, 20),
        "inputs": _trim_items(parser.inputs, 80),
        "buttons": _trim_items(parser.buttons, 80),
    }
    intervention = detect_user_action_required(summary)
    summary["user_action_required"] = intervention["user_action_required"]
    summary["user_action_signals"] = intervention["signals"]
    summary["user_action_reason"] = intervention["reason"]
    summary["login_required"] = intervention["reason"] == "login_required"
    summary["login_signals"] = intervention["signals"] if summary["login_required"] else []
    return summary


def detect_user_action_required(summary: dict) -> dict:
    """识别会阻塞 agent 继续执行的人工操作点。

    这不是替用户判断所有按钮，而是识别登录、验证、授权、上传、支付、
    强确认等需要用户亲自处理的节点。
    """
    text = str(summary.get("text", ""))
    lowered = text.lower()
    url = str(summary.get("url", "")).lower()
    title = str(summary.get("title", "")).lower()
    inputs = summary.get("inputs", []) or []
    buttons = summary.get("buttons", []) or []
    signals: list[str] = []

    for pattern in USER_ACTION_PATTERNS:
        if pattern in lowered:
            signals.append(pattern)
    if any(str(item.get("type", "")).lower() == "password" for item in inputs):
        signals.append("password_input")
    if any(str(item.get("type", "")).lower() == "file" for item in inputs):
        signals.append("file_input")
    if any("login" in str(item.get("id", "")).lower() or "登录" in str(item.get("text", "")) for item in buttons):
        if len(text) < 1200:
            signals.append("login_button_on_short_page")
    if any("captcha" in str(item).lower() or "验证码" in str(item) for item in inputs):
        signals.append("captcha_input")
    if "/login" in url or "passport" in url or "signin" in url:
        signals.append("login_url")
    if "captcha" in url or "verify" in url:
        signals.append("verification_url")
    if "登录" in title or "sign in" in title:
        signals.append("login_title")
    if "验证" in title or "captcha" in title or "verification" in title:
        signals.append("verification_title")

    strong = [item for item in signals if item not in {"login_button_on_short_page"}]
    reason = _user_action_reason(strong)
    return {
        "user_action_required": bool(strong),
        "reason": reason,
        "signals": _dedupe(signals),
    }


def _user_action_intervention(content: dict) -> dict:
    reason = str(content.get("user_action_reason") or "user_action_required")
    signals = content.get("user_action_signals", [])
    signal_text = "、".join(str(item) for item in signals[:4]) or "页面需要用户操作"
    return {
        "need_user": True,
        "reason": reason,
        "question": (
            f"当前浏览器页面可能需要你亲自操作（{signal_text}）。"
            "请在已打开的浏览器里完成该操作后回复“已完成，继续”，"
            "或者回复“跳过，换方案”。"
        ),
        "candidates": ["已完成，继续", "跳过，换方案"],
    }


def _user_action_reason(signals: list[str]) -> str:
    joined = " ".join(str(item).lower() for item in signals)
    if any(item in joined for item in ("login", "登录", "passport", "password", "sign in", "log in")):
        return "login_required"
    if any(item in joined for item in ("验证", "captcha", "verify", "verification", "滑块")):
        return "verification_required"
    if any(item in joined for item in ("upload", "file", "上传", "选择文件")):
        return "file_upload_required"
    if any(item in joined for item in ("支付", "payment")):
        return "payment_required"
    if any(item in joined for item in ("授权", "authorize")):
        return "authorization_required"
    return "user_action_required"


def _find_browser_executable() -> Path | None:
    candidates = []
    for name in ("msedge", "msedge.exe", "chrome", "chrome.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    if sys.platform.startswith("win"):
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
                / "Google/Chrome/Application/chrome.exe",
            ]
        )
    for path in candidates:
        if path.exists():
            return path
    return None


def _normalize_script(script: str) -> str:
    value = script.strip()
    if value.startswith("() =>") or value.startswith("async () =>"):
        return f"({value})()"
    return value


def _validate_script(script: str) -> str | None:
    if not script or not script.strip():
        return "script 不能为空"
    if len(script) > MAX_JS_CHARS:
        return f"script 太长，当前上限是 {MAX_JS_CHARS} 字符"
    return None


def _format_exception(details: dict) -> str:
    exception = details.get("exception", {})
    description = exception.get("description") or details.get("text") or "JS 执行异常"
    return str(description)[:2000]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pick_attrs(attrs: dict[str, str], names: list[str]) -> dict:
    return {name: attrs[name] for name in names if attrs.get(name)}


def _trim_items(items: list[dict], limit: int) -> list[dict]:
    cleaned = [item for item in items if any(str(value).strip() for value in item.values())]
    return cleaned[:limit]


def _dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
