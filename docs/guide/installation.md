# 安装指南

这一页的目标很简单：把 Chrysalis 在你的电脑上跑起来。即使你从来没有安装过 Python 项目，也可以按步骤来。

先解释一句：Chrysalis 是一个本地 Python Agent 项目。所谓“本地”，意思是它的主程序、会话记录、记忆文件、权限记录和工作区都在你自己的项目目录里。所谓“Agent”，意思是它不是只回复文字，而是可以调用工具去读文件、改文件、运行脚本、看网页，再把结果交回模型继续推理。

安装完成后，同一套运行时可以通过这些入口使用：

- `chrysalis "任务"`：一次性命令行任务。
- `chrysalis --interactive`：连续交互模式，适合命令行会话、任务队列和 cron 管理。
- `chrysalis --tui`：终端 UI。
- `desktop-electron`：Electron 桌面端工程。

## 先准备什么

如果你是零基础，先把这些名字和用途对上：

| 依赖 | 版本 / 说明 | 为什么需要 |
| --- | --- | --- |
| Python | `3.11+`，以 `pyproject.toml` 的 `requires-python = ">=3.11"` 为准 | 运行 Chrysalis 主程序 |
| Git | 任意较新版本 | 克隆仓库、管理代码版本 |
| PowerShell | Windows 自带即可 | Windows 下创建虚拟环境、打包桌面端 |
| Node.js | 建议使用当前 LTS 或较新版本 | 运行文档站和 Electron 桌面端 |
| 模型 API Key | 取决于你使用的模型服务 | Agent 需要调用大模型推理 |

建议使用虚拟环境，避免把依赖装到全局 Python。

虚拟环境可以理解成“给这个项目单独准备的 Python 小房间”。依赖装进这个小房间里，不会影响其它 Python 项目。

## 克隆项目

```bash
git clone https://github.com/hsdaoqi/Chrysalis-Agent.git
cd Chrysalis-Agent
```

如果已经在本地项目目录中，确认当前目录是项目根目录即可：

```bash
python -c "from pathlib import Path; print(Path.cwd())"
```

项目根目录就是能看到 `pyproject.toml`、`README.md`、`chrysalis/`、`docs/` 的那一层目录。后续命令默认都在这里执行，除非特别说明。

## 创建虚拟环境

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

激活虚拟环境后，终端提示符前面通常会出现 `(.venv)`。这表示你之后运行的 `python`、`pip` 优先来自当前项目的虚拟环境。

如果 PowerShell 提示不能运行脚本，可以用管理员 PowerShell 或当前用户范围执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

## 安装核心包

核心安装只包含命令行运行所需的最小依赖：

```bash
pip install -e .
```

这里的 `-e` 是 editable install，也叫开发模式安装。它的意思是：命令入口会指向当前源码目录。以后你修改 `chrysalis/` 里的代码，不需要每次重新安装。

安装完成后，项目会注册这些命令：

| 命令 | 入口 |
| --- | --- |
| `chrysalis` | `chrysalis.kernel:main` |
| `chrysalis-gateway` | `chrysalis.gateway.main:main` |

如果你想确认命令到底从哪里来，可以打开 `pyproject.toml`，找到：

```toml
[project.scripts]
chrysalis = "chrysalis.kernel:main"
chrysalis-gateway = "chrysalis.gateway.main:main"
```

这表示你在终端输入 `chrysalis` 时，Python 实际会进入 `chrysalis/kernel.py` 里的 `main()` 函数。

## 安装可选能力

按需要安装 extra。可以分开安装，也可以合并安装，例如 `pip install -e ".[tui,vision]"`。

| Extra | 安装命令 | 用途 |
| --- | --- | --- |
| `tui` | `pip install -e ".[tui]"` | Textual 终端 UI |
| `vision` | `pip install -e ".[vision]"` | 屏幕截图和图像输入 |
| `ocr` | `pip install -e ".[ocr]"` | RapidOCR 图片文字识别 |
| `voice` | `pip install -e ".[voice]"` | TUI 语音输入 |
| `build` | `pip install -e ".[build]"` | PyInstaller 打包 Electron runtime |
| `dev` | `pip install -e ".[dev]"` | 运行测试 |

如果你只想先跑起来，先装：

```bash
pip install -e .
```

如果你想直接体验终端 UI，再装：

```bash
pip install -e ".[tui]"
```

如果你要用桌面端，Python 侧仍然需要先 `pip install -e .`，Node 侧再到 `desktop-electron/` 里安装依赖。

桌面端 runtime 打包需要 Python 构建依赖：

```powershell
pip install -e ".[build]"
```

## 这套安装实际对应哪些代码

如果你只是跟着命令装包，容易把“装什么”和“代码里谁在负责”拆开看。Chrysalis 这里最好按入口理解：

| 目标 | 代码位置 | 作用 |
| --- | --- | --- |
| 安装命令行和网关入口 | `pyproject.toml` | 注册 `chrysalis` 和 `chrysalis-gateway` |
| 读取 `.env` 和项目根目录 | `configs/config.py` | 决定项目根、默认目录和环境变量 |
| 解析一次性 / 交互式 / TUI 命令 | `chrysalis/kernel.py::main()` | 把 CLI 参数变成运行模式 |
| 装配会话、权限、LLM 和 AgentLoop | `chrysalis/kernel.py::Kernel.__init__()` | 生成真正跑任务的运行时 |
| 打开桌面端 | `desktop-electron/electron/runtimeBridge.ts` | 启动 Electron 并连接 `chrysalis.electron_runtime` |
| 只影响文档站 | `docs/.vitepress/config.mts` | 不参与运行时，只控制文档导航和页面 |

所以你可以把安装理解成两步：

1. 先把包和可选能力装进当前 Python 环境。
2. 再让入口命令指向项目里的 `main()`，把配置和依赖接上。

## 配置模型

复制环境变量模板：

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cp .env.example .env
```

编辑 `.env`，至少填入模型服务的 API Key：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.example.com/v1
CHRYSALIS_MODEL=your-model-name
CHRYSALIS_CONTEXT_WINDOW=28000
CHRYSALIS_PERMISSION_LEVEL=balanced
```

这里有两个容易混的点：

1. `provider` 决定 Chrysalis 用哪种协议和默认行为。
2. `base_url` 和 `model` 必须和你的模型服务商要求一致。

例如很多 OpenAI 兼容服务要求 URL 结尾包含 `/v1`，但也有服务不需要。配置不对时，最常见的错误就是鉴权失败、模型不存在或 404。

如果项目中存在 `configs/llm_models.json`，Chrysalis 会优先读取它作为多模型配置；如果桌面端设置中启用了模型配置，则桌面端设置优先级最高。详细优先级、字段含义和多模型写法见 [配置说明](/guide/configuration)。

## 验证安装

先确认命令能被找到：

```bash
chrysalis --help
```

再跑一个最小任务：

```bash
chrysalis "请用三句话介绍这个项目的用途"
```

如果这是你第一次调用模型，可以先用一个非常短的问题测试，避免长任务花太多 token：

```bash
chrysalis "请回答：1+1 等于几？"
```

如果模型配置正确，终端会输出一个 JSON，其中通常包含：

- `ok`：任务是否成功。
- `final`：模型给用户的最终回答。
- `usage`：本次任务的 token、turn 和费用估算。
- `context`：当前会话上下文占用情况。

如果返回 `ok: false`，先看 `error` 字段。常见问题通常是 API Key、base_url、model 名称或网络代理配置。

## 启动不同入口

一次性命令行任务：

```bash
chrysalis "总结 README.md 的结构"
```

连续交互模式：

```bash
chrysalis --interactive
```

终端 UI：

```bash
chrysalis --tui
```

桌面端：

```powershell
cd desktop-electron
npm install
npm run dev
```

这里的三个入口不是三套独立系统，它们都复用 Python 侧的 `Kernel`。所以你在 CLI 里创建的会话，TUI 和桌面端也能读取；你在桌面端改了启用的模型配置，也可能影响 CLI。

## 打包桌面端

构建 Electron 桌面端 Windows portable `.exe`：

```powershell
.\scripts\build_desktop_electron.ps1 -InstallNodeDeps
```

默认输出：

```text
desktop-electron\dist\release\ChrysalisDesktop-*-portable.exe
```

如果打包失败，先分两段排查：

```powershell
cd desktop-electron
npm run build:runtime
npm run build:renderer
npm run build:main
```

这样可以判断是 Python runtime 打包失败，还是前端构建失败，还是 Electron 主进程 TypeScript 编译失败。

## 常见安装问题

### `chrysalis` 命令找不到

确认当前 shell 已激活虚拟环境，并重新执行：

```bash
pip install -e .
```

也可以直接用模块入口测试：

```bash
python -m chrysalis.kernel --help
```

如果 `python -m chrysalis.kernel --help` 能运行，但 `chrysalis --help` 不行，通常说明当前 shell 没有使用虚拟环境里的 Scripts/bin 目录。

### TUI 或桌面端启动失败

检查对应 extra 是否安装：

```bash
pip install -e ".[tui]"
```

桌面端依赖 `desktop-electron` 下的 Node 依赖；如果缺包，进入该目录执行 `npm install`。打包 runtime 时还需要安装 `pip install -e ".[build]"`。

如果 TUI 提示找不到 `textual`，说明没有安装 `tui` extra：

```bash
pip install -e ".[tui]"
```

如果语音输入不可用，还需要：

```bash
pip install -e ".[voice]"
```

### 模型返回鉴权错误

优先检查这三项：

- `.env` 中的 `CHRYSALIS_API_KEY` 是否正确。
- `configs/llm_models.json` 是否覆盖了 `.env`。
- `base_url` 是否带有正确的 `/v1` 或服务商要求的路径。

### `pip install -e .` 很慢或失败

可以先升级 pip：

```bash
python -m pip install --upgrade pip setuptools wheel
```

如果是网络问题，可以使用你自己的镜像源或代理。Chrysalis 本身不会强制要求某个镜像。

### Python 版本不对

检查版本：

```bash
python --version
```

如果低于 3.11，请安装新版 Python，然后重新创建 `.venv`。虚拟环境使用的是创建时的 Python 解释器，换了 Python 后最好重建虚拟环境。

## 安装完成后下一步

安装只是第一步。接下来建议继续读：

1. [配置说明](/guide/configuration)：搞清楚 `.env`、`configs/llm_models.json`、桌面端设置谁优先。
2. [快速开始](/guide/quickstart)：跑一个完整任务，理解会话、队列和权限。
3. [Agent 原理概述](/tutorial/overview)：把安装命令和源码主线对应起来。
