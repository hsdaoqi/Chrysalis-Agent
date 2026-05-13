import chrysalis.browser as browser
from chrysalis.browser import BrowserController, simplify_html


def test_simplify_html_extracts_page_summary():
    html = """
    <html>
      <head><title>示例页面</title><style>.x{}</style></head>
      <body>
        <nav>导航</nav>
        <a href="/docs">文档</a>
        <form action="/search" method="get">
          <input name="q" placeholder="搜索">
          <button type="submit">提交</button>
        </form>
        <script>window.secret = 1</script>
      </body>
    </html>
    """

    result = simplify_html(html, "https://example.test")

    assert result["title"] == "示例页面"
    assert "window.secret" not in result["text"]
    assert result["links"] == [{"text": "文档", "href": "/docs"}]
    assert result["forms"][0]["action"] == "/search"
    assert result["inputs"][0]["name"] == "q"
    assert result["buttons"][0]["text"] == "提交"


def test_browser_controller_reports_missing_real_browser(monkeypatch):
    monkeypatch.setattr(browser, "_find_browser_executable", lambda: None)
    controller = BrowserController()

    result = controller.scan("https://example.test")

    assert result["ok"] is False
    assert result["backend"] == "cdp"
    assert "真实浏览器" in result["error"]


def test_browser_controller_execute_js_reports_missing_real_browser(monkeypatch):
    monkeypatch.setattr(browser, "_find_browser_executable", lambda: None)
    controller = BrowserController()

    result = controller.execute_js("() => document.title")

    assert result["ok"] is False
    assert result["backend"] == "cdp"
    assert "真实浏览器" in result["error"]


def test_browser_controller_rejects_empty_js():
    controller = BrowserController()

    result = controller.execute_js(" ")

    assert result["ok"] is False
    assert "不能为空" in result["error"]
