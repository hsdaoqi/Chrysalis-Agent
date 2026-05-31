# 安装指南

## 前置要求

- Python 3.10+
- Git

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://github.com/hsdaoqi/Chrysalis-Agent.git
cd Chrysalis-Agent
```

### 2. 安装核心包

```bash
pip install -e .
```

### 3. 安装 TUI（可选）

```bash
pip install -e ".[tui]"
```

### 4. 安装桌面端（可选）

```bash
pip install -e ".[desktop]"
chrysalis-desktop
```

### 5. 打包成 Windows exe

```powershell
.\scripts\build_desktop.ps1
```

## 配置

复制环境变量模板并编辑：

```bash
cp .env.example .env
```
