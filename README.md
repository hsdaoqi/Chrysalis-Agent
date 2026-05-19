# Chrysalis

一个目标为在保证任务完成率的情况下能够尽可能节省token的通用型agent，类似小龙虾，爱马仕

## 快速开始

```bash
git clone https://github.com/hsdaoqi/Chrysalis-Agent.git
cd Chrysalis-Agent
pip install -e .
```

复制 `.env.example` 为 `.env`，填入 API Key：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.deepseek.com
CHRYSALIS_MODEL=deepseek-v4-pro
```

支持任何 OpenAI 兼容的 API（DeepSeek、中转站、Claude via 中转站等）。

## 运行方式

```bash
# 单次任务
chrysalis "帮我重构 utils.py"

# 交互模式
chrysalis --interactive

# TUI 模式（需要 pip install -e ".[tui]"）
chrysalis --tui
```

## 架构

```text
用户任务 → Kernel 装配 → AgentLoop 行动循环 → LLM (function calling)
                                    ↓
                              原子工具执行 → 观察压缩 → 下一轮
                                    ↓
                              Memory 记忆体系 → 经验沉淀
```

## 工具集（10 个）

| 工具 | 用途 |
|------|------|
| `code_run` | 代码执行器（Python / PowerShell / Bash） |
| `file_read` | 读取文件，支持行号定位和关键词搜索 |
| `file_write` | 写入/追加/前插文件 |
| `file_patch` | 替换文件中唯一匹配的文本块 |
| `web_scan` | 用本机浏览器扫描网页，返回简化 HTML |
| `web_execute_js` | 在浏览器标签页执行 JS |
| `update_working_checkpoint` | 更新任务内短期工作记忆 |
| `ask_user` | 遇到阻塞时询问用户 |
| `start_long_term_update` | 标记当前任务有可沉淀经验 |
| `spawn_subagent` | 派生并行子 agent 执行子任务 |

shell 命令（git、npm 等）通过 `code_run(type="powershell")` 执行。

## 记忆体系（4 层）

```text
L0  META-SOP        memory_management_sop.md — 记忆写入的宪法
L1  Insight Index   global_mem_insight.txt — 极简索引（<30行），每轮注入
L2  Fact Store      global_mem.txt — 环境事实（路径、配置、凭据名）
L3  SOPs            memory/*.md — 操作规范（git、web、plan、verify…）
L4  Session Archive data/l4_session/ — 压缩后的历史会话
```

原则：**No Execution, No Memory** — 只有工具验证过的事实才能写入。

## LLM 模块

- 支持 OpenAI 和 Anthropic 协议，自动适配
- Native function calling（不再用 JSON-in-text）
- 流式输出 + 上下文自动裁剪
- 多模型 failover + 指数退避重试
- 原始 prompt/response 日志记录

## 目录结构

```text
chrysalis/
  kernel.py          CLI 入口 + Kernel 装配
  agent_loop.py      观察-行动循环（function calling / JSON-in-text 双模式）
  session.py         跨 session 持久化上下文
  working.py         任务内短期工作记忆
  observation.py     工具观察结果压缩
  subagent.py        并行子 agent 派生
  browser.py         CDP 浏览器控制
  task_queue.py      任务队列
  compress_session.py  L4 会话压缩归档
  llm/               LLM 模块（streaming、failover、context trim）
  tools/             原子工具注册表
  tui/               终端 UI（Textual）

configs/config.py    项目路径和运行时配置
utils/               文本工具、prompt 组装、进度回调
memory/              长期记忆和 SOP
data/                运行状态、session 持久化、日志
workspace/           工具默认工作目录
```

## 设计原则

- 工具集精简（10 个），复杂操作通过 code_run 组合
- Function calling 优先，JSON-in-text 作为 fallback
- 记忆分层，L1 极简索引每轮注入，按需读取 L2/L3
- 安全策略：危险 shell 命令拦截、密钥文件保护、代码沙箱
- 失败升级：1 次→读错误，2 次→探测环境，3 次→换方案或问用户


![Star History](https://www.star-history.com/?repos=hsdaoqi%2FChrysalis-Agent&type=date&legend=top-left)
