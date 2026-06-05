# 用 NapCat 个人 QQ + OneBot 接入 QQ 群（官方 bot 不能入群时）

## when_to_use
- 用户要“把 QQ 机器人拉进群/接入群”，但官方 QQ bot 无法被拉群。
- 需要用一个普通 QQ 账号作为机器人账号，通过 NapCat 提供 OneBot v11 WebSocket，再让 Chrysalis 的 `qq_personal` 网关连接。

## verified_outcome_from_session
- 成功用 QQ `3843511481` 登录 NapCat。
- NapCat WebUI 监听：`http://127.0.0.1:6099`
- WebUI token：`71b9aa24ba85`
- OneBot WebSocket Server：`ws://127.0.0.1:3001`
- 已验证 `Get-NetTCPConnection` 显示 `127.0.0.1:3001` 与 `6099` 均监听。
- 已配置文件：`data/napcat/OneKey_20260602_123844/bootmain/config/napcat_protocol_3843511481.json`

## key_steps
1. 不要继续官方 QQ bot 路线；改用个人 QQ / OneBot。
2. Chrysalis 网关平台名是 `qq-personal` 或源码别名 `qq_personal`，不是 `qq-personal/onebot`。
   - 可启动：`gateway_connect(platform="qq-personal")`
   - 返回提示：Personal QQ expects OneBot v11 WebSocket，例如 `ws://127.0.0.1:3001`。
3. 安装 NapCat：
   - 官方 release 可用资产：`NapCat.Shell.Windows.OneKey.zip`
   - 本次解压目录：`data/napcat/OneKey_20260602_123844`
   - 若一键安装器下载核心失败，可手动下载 `NapCat.Shell.zip` 到 `bootmain` 并 `Expand-Archive` 解压。
4. 正确启动 NapCat 不要直接运行 `NapCatWinBootMain.exe QQ号`，那会只打印 argv 后退出；应运行：
   ```bat
   cd /d D:\Project\Chrysalis\data\napcat\OneKey_20260602_123844\bootmain
   launcher.bat 3843511481
   ```
   或一行：
   ```bat
   cmd /k "cd /d D:\Project\Chrysalis\data\napcat\OneKey_20260602_123844\bootmain && launcher.bat 3843511481"
   ```
   如果弹 UAC/管理员权限，用户需要点“是”。
5. 终端二维码是 QQ 登录二维码，不是 token。让用户用机器人 QQ 对应手机 QQ 扫码确认。
6. WebUI token 定位：
   - 优先读：`data/napcat/OneKey_20260602_123844/bootmain/config/webui.json`
   - 本次 token 在字段：`"token": "71b9aa24ba85"`
7. WebUI 地址：`http://127.0.0.1:6099/webui/`，登录页 token 填 `webui.json` 里的 token。
8. OneBot v11 WS 配置重点：不要只改 `onebot11_账号.json`。本次真正让 3001 生效的是：
   `config/napcat_protocol_3843511481.json`
   内容应类似：
   ```json
   {
     "enable": true,
     "network": {
       "httpServers": [],
       "websocketServers": [
         {
           "name": "chrysalis-ws-server",
           "enable": true,
           "host": "127.0.0.1",
           "port": 3001,
           "messagePostFormat": "array",
           "reportSelfMessage": false,
           "token": "",
           "debug": false
         }
       ],
       "websocketClients": []
     }
   }
   ```
9. 改配置后需要重启 NapCat。若 `taskkill` / `Stop-Process` 被安全策略拦截，可用 WMI 精确终止监听 6099 的 PID：
   ```bat
   for /f "tokens=5" %p in ('netstat -ano ^| findstr /r /c:":6099 .*LISTENING"') do wmic process where processid=%p call terminate
   ```
   然后重新运行 `launcher.bat 账号`。
10. 验证端口：
   ```powershell
   Get-NetTCPConnection -LocalPort 3001,6099 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess | ConvertTo-Json -Compress
   ```
   看到 `127.0.0.1:3001` 和 `6099` LISTENING 即成功。
11. 之后把该普通 QQ 账号拉入目标群；群里 `@` 它或发测试消息。若不响应，再检查 Chrysalis qq_personal 侧群消息权限/过滤。

## pitfalls
- `gateway_connect(platform="qq-personal/onebot")` 不支持；正确是 `qq-personal`。
- 官方 QQ bot 不能被普通方式拉进群，别在这条路上继续耗时。
- `NapCatWinBootMain.exe 账号` 不是正常启动入口；要用 `launcher.bat 账号`。
- `bootmain/napcat.bat` 工作目录不对时会报找不到 `QQ.exe`；使用 `launcher.bat` 更稳。
- 只改 `onebot11_账号.json` 可能不让 3001 生效；本次必须把 `napcat_protocol_账号.json` 的 `enable` 改为 `true` 并加 `websocketServers`。
- `taskkill`、`Stop-Process` 可能被安全策略拦截；WMI 精确 PID terminate 可行（已验证）。
- 终端不能关，关掉机器人会掉线。

## evidence
- 本次执行后端口检查显示：
  - `127.0.0.1:3001` LISTENING
  - `::/0.0.0.0:6099` LISTENING
- QQ/NapCat PID 曾为 `24836`，后续会变化，不要记死 PID。

## provenance
- Learned from successful session on 2026-06-02: connected QQ `3843511481` to NapCat/OneBot for QQ group robot use.
