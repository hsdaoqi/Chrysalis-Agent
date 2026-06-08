---
title: 消息网关
---

# 消息网关

消息网关让你在 QQ、个人微信、飞书里 @ 一下机器人，就能用上 Chrysalis。它背后跑的还是同一个 Kernel——网关只是把聊天消息转成任务、把结果发回去的"传话筒"。

::: warning 先读这一句
网关默认把远程聊天**当作不可信输入**。远程用户不能像你本人一样批准本机权限弹窗，所以默认情况下他们用不了写文件、跑脚本这类危险工具。部署到公开群组前，务必理解这一点。原理见 [第 7 章 7.6 节](/tutorial/permission#_7-6-网关场景-默认不信任远程) 和 [第 11 章](/tutorial/architecture-extras#_11-2-消息网关-把-agent-接到聊天软件)。
:::

## 安装

网关依赖单独装：

```bash
pip install -e ".[gateway]"
```

## 支持的平台

| 命令 | 平台 | 说明 |
| --- | --- | --- |
| `chrysalis-gateway qq` | QQ 官方 Bot | QQ 开放平台的官方机器人，被视为可信宿主 |
| `chrysalis-gateway qq-personal` | 个人 QQ | 通过 OneBot v11（NapCat / Lagrange 等后端）接入个人 QQ 号 |
| `chrysalis-gateway onebot` | 同上 | `onebot` / `napcat` 都归一到 `qq-personal` |
| `chrysalis-gateway wechat` | 个人微信 | 首次启动扫码登录 |
| `chrysalis-gateway feishu` | 飞书 | 飞书自建应用机器人，长连接收消息 |

也可以从主命令进入：

```bash
chrysalis connect wechat
chrysalis connect feishu
```

一条命令可以同时启动多个平台，它们共享同一个网关服务。

## 配置

网关相关的环境变量写在 `.env` 里（`.env.example` 有完整列表）：

| 变量 | 用途 |
| --- | --- |
| `CHRYSALIS_GATEWAY_ALLOWED_TOOLS` | 远程聊天额外允许的工具名（默认只有安全工具） |
| `CHRYSALIS_ONEBOT_WS_URL` | OneBot v11 WebSocket 地址，如 `ws://127.0.0.1:3001` |
| `CHRYSALIS_ONEBOT_ACCESS_TOKEN` | OneBot 鉴权 token |
| `CHRYSALIS_ONEBOT_REQUIRE_MENTION` | 群聊是否需要 @ 才触发 |
| `CHRYSALIS_WECHAT_TOKEN_FILE` | 个人微信登录 token 保存路径 |
| `CHRYSALIS_FEISHU_APP_ID` | 飞书自建应用 app id |
| `CHRYSALIS_FEISHU_APP_SECRET` | 飞书自建应用 app secret |
| `CHRYSALIS_FEISHU_REQUIRE_MENTION` | 飞书群聊是否需要 @ 才触发 |

各平台的接入细节（比如 NapCat 怎么配 OneBot、飞书怎么建应用）参考 `skills/integration/` 下对应的技能文档。

## 聊天里的命令

在聊天窗口里，除了直接发任务，还能用几个控制命令：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示帮助 |
| `/new` | 开一个新会话（清空当前上下文） |
| `/stop` | 停止当前正在跑的任务 |
| `/btw <话>` | 给正在跑的任务补充一句话（插话） |

每个聊天会话会绑定一个独立的 Kernel 和会话历史，并持久化到 `data/gateway_sessions.json`——所以网关重启后，同一个群/同一个人还能接着之前的上下文聊。

## 会话隔离

群聊默认按用户隔离（每个人独立会话），可以用 `--shared-groups` 改成整群共享一个会话：

```bash
chrysalis-gateway qq-personal --shared-groups
```

注意个人 QQ 群聊（`qq-personal`）默认就是整群共享会话。

## 安全模型回顾

把第 7 章和第 11 章的结论浓缩在这里，因为它太重要：

- **只有官方 QQ Bot（`qq`）被当作可信宿主**，用完整权限。
- 其余平台用 `GatewayPermissionEngine`：危险工具直接从模型可见列表里移除；`file_read`/`ocr` 限制在白名单目录；`web_fetch` 只能访问公网。
- **远程用户无法批准本机权限**——任何需要确认的操作，对远程用户一律拒绝。
- `CHRYSALIS_GATEWAY_ALLOWED_TOOLS` 可以额外放开工具，但放开前必须确认部署环境可信（比如只有你自己在用的私有群）。

一句话：**别在陌生人能进的群里开放危险工具。**

## 桌面端能旁观网关

如果你同时开着 Electron 桌面端，它能看到网关里正在跑的会话——网关把活动写进 `data/gateway_activity.json`，桌面端轮询读取并展示。这是"一套内核多个前端"的又一个体现（见 [第 11 章](/tutorial/architecture-extras)）。

## 下一步

- 想理解网关怎么复用 Kernel、远程消息怎么变成任务 → [第 11 章：子 Agent、网关与桌面端](/tutorial/architecture-extras)
- 想理解网关的权限引擎 → [第 7 章：权限系统](/tutorial/permission)
