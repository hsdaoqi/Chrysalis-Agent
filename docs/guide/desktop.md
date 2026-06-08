# 桌面端使用指南

桌面端是 Chrysalis 的 Electron 图形界面。它适合不想长期待在命令行里的使用场景，例如管理多个会话、查看工作区文件、拖拽附件、调整模型配置、观察任务运行状态。

先说清楚一个关键点：桌面端不是另一套 Agent。它只是图形界面，真正跑任务的还是 Python 侧的 Chrysalis Kernel。

```text
Electron 界面
  -> 启动或连接 Python runtime
  -> 调用 chrysalis.electron_runtime
  -> 复用 Kernel、AgentLoop、LLMClient、Tools、SessionStore
```

所以 CLI、TUI、桌面端共享同一套会话、模型配置、权限、长期记忆和技能库。

## 什么时候用桌面端

| 场景 | 为什么适合桌面端 |
| --- | --- |
| 你想管理很多会话 | 图形界面比命令行列表更直观 |
| 你想拖拽附件 | 桌面端更适合文件选择、图片和附件工作流 |
| 你想调整模型配置 | 桌面端设置页可以写入 `data/desktop_settings.json` |
| 你想看任务状态 | GUI 可以持续展示流式输出、工具事件、TODO 和权限 |
| 你不熟悉命令行 | 桌面端降低日常使用门槛 |

如果你是学习源码，建议先用 CLI 跑通，再用桌面端观察同一套运行时如何被 UI 包起来。

## 前置要求

你需要两部分依赖：

| 依赖 | 用途 |
| --- | --- |
| Python 3.11+ | 运行 Chrysalis runtime |
| Node.js / npm | 运行 Electron、Vite 和 TypeScript 构建 |

先在项目根目录安装 Python 包：

```bash
pip install -e .
```

如果要打包 runtime，还需要：

```bash
pip install -e ".[build]"
```

## 开发模式启动

进入桌面端目录：

```powershell
cd desktop-electron
```

安装 Node 依赖：

```powershell
npm install
```

启动开发模式：

```powershell
npm run dev
```

`npm run dev` 会同时做几件事：

```text
tsc -p tsconfig.node.json --watch
  编译 Electron 主进程代码

vite
  启动 renderer 前端开发服务器

electron .
  等主进程和 Vite 都准备好后打开桌面应用
```

这就是为什么第一次启动可能会稍慢：它不是只打开一个窗口，而是同时启动 TypeScript watch、Vite 和 Electron。

## 桌面端脚本说明

`desktop-electron/package.json` 里有这些常用脚本：

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 开发模式启动，适合本地调试 |
| `npm run build:runtime` | 用 PowerShell 脚本打包 Python runtime |
| `npm run build:renderer` | 构建 React/Vite 前端 |
| `npm run build:main` | 编译 Electron 主进程 TypeScript |
| `npm run build` | 依次构建 runtime、renderer、main |
| `npm run package:win` | 构建并打包 Windows portable 版本 |

如果 `npm run build` 失败，建议分开跑三段，定位是哪一段出问题：

```powershell
npm run build:runtime
npm run build:renderer
npm run build:main
```

## 打包分发

在项目根目录运行：

```powershell
.\scripts\build_desktop_electron.ps1 -InstallNodeDeps
```

这个脚本会安装 Node 依赖并执行桌面端打包流程。打包产物默认输出到：

```text
desktop-electron\dist\release\ChrysalisDesktop-*-portable.exe
```

也可以在 `desktop-electron/` 下手动执行：

```powershell
npm run package:win
```

便携版 `.exe` 适合本机使用或拷贝到同类 Windows 环境中使用。由于 Chrysalis 是本地 Agent，分发前请确认不要把个人 `.env`、`data/` 中的敏感会话、权限记录或 API Key 一起打包传播。

## 配置模型

桌面端设置会写入：

```text
data/desktop_settings.json
```

当其中 `enabled` 为 `true` 时，它会覆盖：

```text
configs/llm_models.json
.env
```

也就是说，桌面端配置优先级最高。这个设计方便你在 GUI 里临时切模型，但也容易让新手困惑：为什么我改了 `.env`，CLI 还是用旧模型？

排查顺序：

1. 看 `data/desktop_settings.json` 是否存在。
2. 看里面的 `enabled` 是否为 `true`。
3. 如果想回到 `.env` 或 `configs/llm_models.json`，把 `enabled` 改成 `false`，或在桌面端设置页重置。

最小结构示例：

```json
{
  "enabled": true,
  "llm": {
    "name": "desktop-model",
    "provider": "openai",
    "api_key": "sk-xxx",
    "base_url": "https://api.example.com/v1",
    "model": "example-model",
    "context_window": 28000,
    "temperature": 0.2,
    "max_retries": 4,
    "timeout": 60
  },
  "system_prompt": ""
}
```

## 会话管理

桌面端会读取同一套会话目录：

```text
data/sessions/
```

这意味着：

- CLI 创建的会话，桌面端可以看到。
- TUI 加载过的会话，桌面端也可以继续。
- 桌面端重命名、置顶、删除会话，会影响同一份本地会话存储。

会话保存的是 canonical history，不只是聊天文本。它包含用户输入、模型回答、工具调用、工具结果、图片和其它 block。详细结构见 [LLM History](/tutorial/llm-history)。

## 附件和工作区

Chrysalis 默认工作区是：

```text
workspace/
```

桌面端适合处理这些输入：

- 文本文档。
- 图片。
- 需要作为任务上下文的附件。
- Agent 生成的输出文件。

如果 Agent 的结果里出现 `[FILE:...]` 这类文件引用，桌面端可以把它展示或提供打开入口。网关场景也会使用类似机制把文件回传到 QQ、微信或飞书。

## 权限确认

桌面端同样复用 `PermissionEngine`。当 Agent 想执行高风险操作时，例如写文件、运行代码、截图、浏览器 JS，它会通过 UI 请求确认。

常见选项：

| 选项 | 含义 |
| --- | --- |
| 允许本次 | 只允许当前这一次操作 |
| 永久允许 | 写入 `data/permissions.json`，以后同类请求自动通过 |
| 拒绝 | 不执行本次操作 |
| 详细说明 | 查看工具名、风险等级和参数预览 |

因为桌面端是本机界面，用户可以直接确认权限。消息网关不同，远程聊天用户不能批准本机权限。

## 代码结构

桌面端主要分成三层：

```text
desktop-electron/
  src/
    Electron 主进程、renderer 前端、IPC 协议

chrysalis/electron_runtime.py
  Python runtime，负责接收桌面端请求并调用 Kernel

chrysalis/desktop_trace.py
  桌面端相关 trace 和事件辅助
```

简化调用链：

```text
Renderer 用户点击或输入
  -> Electron 主进程 IPC
  -> Python runtime 进程
  -> Kernel.run(task)
  -> AgentLoop 执行工具循环
  -> runtime 把 stream/tool/permission/working 事件发回 Electron
  -> Renderer 更新界面
```

如果你要改桌面端功能，先判断自己改的是哪一层：

| 想改什么 | 主要位置 |
| --- | --- |
| 前端界面、按钮、布局 | `desktop-electron/src/` |
| Electron 主进程和窗口行为 | `desktop-electron/src/` 的 main 相关文件 |
| Python runtime 事件协议 | `chrysalis/electron_runtime.py` |
| Agent 真正行为 | `chrysalis/kernel.py`、`chrysalis/agent_loop.py`、`chrysalis/tools/` |
| 会话字段 | `chrysalis/session_store.py` |
| 权限策略 | `chrysalis/permission.py` |

## 常见问题

### `npm install` 很慢

这是 Node 依赖安装问题，可以使用你自己的 npm 镜像或代理。Chrysalis 不强制指定源。

### `npm run dev` 打不开窗口

先确认三段服务是否都正常：

```powershell
npm run build:main
npm run build:renderer
```

如果主进程没有编译出 `dist/electron/main.js`，Electron 启动会失败。

### 桌面端模型和 CLI 不一致

检查：

```text
data/desktop_settings.json
```

如果 `enabled: true`，桌面端设置会覆盖 `.env` 和 `configs/llm_models.json`。如果希望统一配置，把它设为 `false`。

### 打包失败

分段运行：

```powershell
cd desktop-electron
npm run build:runtime
npm run build:renderer
npm run build:main
```

如果 `build:runtime` 失败，多半是 Python / PyInstaller 相关；如果 `build:renderer` 失败，多半是前端构建；如果 `build:main` 失败，多半是 TypeScript 类型或主进程代码问题。

### 便携版启动后不能调用模型

检查运行目录下是否有正确配置，尤其是 `.env`、`configs/llm_models.json` 或桌面端设置。也要确认网络和 API Key 可用。

## 推荐上手流程

1. 先用 CLI 跑通：

```bash
chrysalis "请回答：桌面端前置检查成功"
```

2. 再启动桌面端：

```powershell
cd desktop-electron
npm install
npm run dev
```

3. 在桌面端里新建会话，输入：

```text
请阅读 README.md，并给我一个适合新手的阅读顺序。
```

4. 回到 CLI 查看会话目录：

```bash
chrysalis --interactive
```

然后输入：

```text
/session
```

你会发现桌面端和 CLI 看到的是同一套会话。

一句话总结：**桌面端让 Chrysalis 更容易日常使用，但它的核心仍然是同一个本地 Agent Kernel。**
