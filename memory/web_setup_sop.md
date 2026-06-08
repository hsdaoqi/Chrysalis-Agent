# Chrysalis Web SOP

## 当前能力

Chrysalis 现有三个 Web 工具：
- `web_fetch(url)`：无浏览器、无登录态的公开网页抓取（标准库 HTTP）。
- `web_scan`：打开/扫描真实浏览器页面，返回结构化摘要。
- `web_execute_js`：在真实浏览器标签页执行 JS。

`web_scan`/`web_execute_js` 有两条后端，按优先级自动选择：
1. **插件桥接（首选，GA 风格）**：本地桥接服务 `chrysalis/browser_bridge.py` + 浏览器扩展
   `assets/tmwd_cdp_bridge`。扩展接管你**日常正在用的浏览器**，保留全部登录态/Cookie，
   支持跨标签页操作。这是 GA 原版 TMWebDriver 的能力，已用纯标准库重写桥接层（零新依赖）。
2. **CDP 回退**：连不上插件时，连接/启动一个带 `--remote-debugging-port` 的浏览器。
   注意：从 Edge/Chrome 136 起，CDP 不能调试默认配置，所以这条路径用的是独立空白配置
   `workspace/browser_profile`，**没有你的登录态**。

## 一次性安装扩展（强烈建议）

要让 bot 接管你日常的浏览器（带登录态），需手动装一次扩展，装完永久生效：

1. 打开浏览器扩展管理页：Edge `edge://extensions`，Chrome `chrome://extensions`。
2. 打开右上角「开发者模式」。
3. 点「加载已解压的扩展」，选择目录 `assets/tmwd_cdp_bridge`。
4. 装好后，打开任意普通网页（非 `about:blank`/`edge://` 内部页），
   扩展会自动连接本地桥接服务（`ws://127.0.0.1:18765`）。
5. 页面右下角出现绿色 `ljq_driver: 已连接` 角标即表示就绪。

桥接服务由 agent 在用到浏览器时自动后台拉起，无需手动启动；也可手动运行
`python -m chrysalis.browser_bridge` 自行启动。

## 使用规则

1. `web_fetch` 用于公开网页文本；需要登录/点击/JS/截图时用 `web_scan`/`web_execute_js`。
2. 只抓公开 URL，不抓密钥、后台管理页或私人数据。
3. `web_execute_js` 里用 `await` 时需**显式 `return`** 才能拿到返回值。
4. 浏览器特殊操作（文件上传/HttpOnly Cookie/跨域 iframe/跨 tab/CDP）见 [[tmwebdriver_sop]]。

## 连不上排查

1. 浏览器开着吗？扩展装了并启用了吗？打开一个普通网页让扩展注入。
2. 桥接服务在跑吗？`http://127.0.0.1:18765/health` 应返回 `{"chrysalis": true}`；
   `extension_connected` 字段反映扩展是否已连。
3. `web_scan` 返回里若带 `note` 提示"临时空白浏览器"，说明走了 CDP 回退、扩展没连上，
   按上面步骤装/启用扩展即可。
4. 以上都正常仍连不上 → 请求用户协助。
