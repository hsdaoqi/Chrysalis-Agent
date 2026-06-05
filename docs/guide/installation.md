# 安装指南

Chrysalis 是一个本地 Python Agent 项目。安装完成后，同一套运行时可以通过命令行、TUI 和 Electron 桌面端使用：

- `chrysalis "任务"`：一次性命令行任务。
- `chrysalis --interactive`：连续交互模式，适合命令行会话、任务队列和 cron 管理。
- `chrysalis --tui`：终端 UI。
- `desktop-electron`：Electron 桌面端工程。

## 前置要求

| 依赖 | 版本 / 说明 |
| --- | --- |
| Python | `3.11+`，以 `pyproject.toml` 的 `requires-python = ">=3.11"` 为准 |
| Git | 用于克隆仓库和日常版本管理 |
| PowerShell | Windows 下构建桌面端 `.exe` 时使用 |
| Node.js | 运行或打包 Electron 桌面端，以及本地预览或构建 `docs` 站点时需要 |

建议使用虚拟环境，避免把依赖装到全局 Python。

## 克隆项目

```bash
git clone https://github.com/hsdaoqi/Chrysalis-Agent.git
cd Chrysalis-Agent
```

如果已经在本地项目目录中，确认当前目录是项目根目录即可：

```bash
python -c "from pathlib import Path; print(Path.cwd())"
```

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

## 安装核心包

核心安装只包含命令行运行所需的最小依赖：

```bash
pip install -e .
```

安装完成后，项目会注册这些命令：

| 命令 | 入口 |
| --- | --- |
| `chrysalis` | `chrysalis.kernel:main` |
| `chrysalis-gateway` | `chrysalis.gateway.main:main` |

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

如果模型配置正确，终端会输出一个 JSON，其中通常包含：

- `ok`：任务是否成功。
- `final`：模型给用户的最终回答。
- `usage`：本次任务的 token、turn 和费用估算。
- `context`：当前会话上下文占用情况。

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

## 打包桌面端

构建 Electron 桌面端 Windows portable `.exe`：

```powershell
.\scripts\build_desktop_electron.ps1 -InstallNodeDeps
```

默认输出：

```text
desktop-electron\dist\release\ChrysalisDesktop-*-portable.exe
```

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

### TUI 或桌面端启动失败

检查对应 extra 是否安装：

```bash
pip install -e ".[tui]"
```

桌面端依赖 `desktop-electron` 下的 Node 依赖；如果缺包，进入该目录执行 `npm install`。打包 runtime 时还需要安装 `pip install -e ".[build]"`。

### 模型返回鉴权错误

优先检查这三项：

- `.env` 中的 `CHRYSALIS_API_KEY` 是否正确。
- `configs/llm_models.json` 是否覆盖了 `.env`。
- `base_url` 是否带有正确的 `/v1` 或服务商要求的路径。
