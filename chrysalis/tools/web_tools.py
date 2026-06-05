"""Web tools: browser-backed scan/JS plus public HTTP fetch."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from chrysalis.browser import BrowserController, simplify_html
from chrysalis.tools.registry import tool
from chrysalis.tools.safety import as_bool

_BROWSER = BrowserController()
_PUBLIC_FETCH_MAX_BYTES = 512_000


@tool("web_scan", "Open or scan a page in the local browser and return a page summary.", params={
    "url": "URL to open (optional)",
    "tab_id": "Target browser tab id (optional)",
    "tabs_only": "Only return the tab list",
    "text_only": "Return text only",
    "wait_ms": "Wait time in milliseconds (default 1000)",
})
def web_scan(args: dict, workspace: Path | None = None) -> dict:
    del workspace
    return _BROWSER.scan(
        url=args.get("url"),
        tab_id=args.get("tab_id"),
        tabs_only=as_bool(args.get("tabs_only", False)),
        text_only=as_bool(args.get("text_only", False)),
        wait_ms=int(args.get("wait_ms", 1000)),
    )


@tool("web_fetch", "Fetch a public HTTP/HTTPS URL without local browser state or JavaScript.", params={
    "url": "Public HTTP/HTTPS URL to fetch",
    "text_only": "Return plain text only",
    "timeout": "Timeout seconds (default 15)",
})
def web_fetch(args: dict, workspace: Path | None = None) -> dict:
    del workspace
    url = str(args.get("url") or "").strip()
    error = _public_url_error(url, resolve=True)
    if error:
        return {"ok": False, "error": error, "url": url}

    timeout = max(1, min(int(args.get("timeout") or 15), 30))
    text_only = as_bool(args.get("text_only", True))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Chrysalis/1.0 public-web-fetch",
            "Accept": "text/html,text/plain,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        },
    )
    try:
        opener = urllib.request.build_opener(_PublicHTTPRedirectHandler)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_PUBLIC_FETCH_MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
            content_type = response.headers.get("content-type", "")
            final_url = response.geturl()
            final_error = _public_url_error(final_url, resolve=True)
            if final_error:
                return {"ok": False, "error": final_error, "url": url, "final_url": final_url}
            status = getattr(response, "status", 200)
    except Exception as exc:
        return {"ok": False, "error": f"web_fetch failed: {type(exc).__name__}: {exc}", "url": url}

    truncated = len(raw) > _PUBLIC_FETCH_MAX_BYTES
    body = raw[:_PUBLIC_FETCH_MAX_BYTES].decode(charset, errors="replace")
    if "html" in content_type.lower() or "<html" in body[:500].lower():
        content = simplify_html(body, url=final_url, text_only=text_only)
    else:
        content = body[:30_000].strip()

    return {
        "ok": True,
        "url": final_url,
        "status": status,
        "content_type": content_type,
        "truncated": truncated,
        "text_only": text_only,
        "content": content,
    }


@tool("web_execute_js", "Execute JavaScript in the current local browser tab.", params={
    "script": "JavaScript code",
    "tab_id": "Target browser tab id (optional)",
    "timeout_ms": "Timeout in milliseconds (default 10000)",
})
def web_execute_js(args: dict, workspace: Path | None = None) -> dict:
    del workspace
    return _BROWSER.execute_js(
        script=args.get("script", ""),
        tab_id=args.get("tab_id"),
        timeout=int(args.get("timeout_ms", args.get("timeout", 10_000))),
    )


class _PublicHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_url = urljoin(req.full_url, newurl)
        error = _public_url_error(target_url, resolve=True)
        if error:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                f"redirect blocked: {error}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, target_url)


def _public_url_error(url: str, *, resolve: bool = False) -> str:
    if not url:
        return "url is required"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "only explicit public HTTP/HTTPS URLs are allowed"
    if parsed.username or parsed.password:
        return "URLs with embedded credentials are not allowed"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "URL host is required"
    if host == "localhost" or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")):
        return "local or private hostnames are not allowed"
    ip_error = _private_ip_error(host.strip("[]"))
    if ip_error:
        return ip_error
    if resolve:
        return _public_dns_error(host, parsed.port)
    return ""


def _private_ip_error(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return ""
    if _is_private_ip(ip):
        return "local or private network addresses are not allowed"
    return ""


def _public_dns_error(host: str, port: int | None) -> str:
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"host DNS lookup failed: {exc}"
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0]).strip("[]")
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_private_ip(ip):
            return "local or private network addresses are not allowed"
    return ""


def _is_private_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
