# 配置说明

Chrysalis 的配置分为三层：模型连接、运行目录、权限策略。理解这三层后，就能判断一次任务会用哪个模型、在哪个目录读写、什么时候需要用户确认。

## 配置优先级

`AgentConfig.load_session_configs()` 的模型加载顺序是：

1. `data/desktop_settings.json`：桌面端设置中 `enabled: true` 时生效。
2. `configs/llm_models.json`：存在且内容是非空 JSON 数组时生效。
3. `.env`：前两项都不可用时，回退到单模型环境变量。

这意味着：如果你改了 `.env` 但模型没有变化，先检查 `configs/llm_models.json` 或桌面端设置是否覆盖了它。

## 配置是怎么进入运行时的

如果你按源码顺序看，会是这样一条链：

1. `configs/config.py` 先用 `load_dotenv(PROJECT_ROOT / ".env")` 读取环境变量。
2. `LLMConfig` 从这些环境变量里拿到 provider、api key、base_url、model、温度和上下文窗口。
3. `LLMConfig.to_session_config()` 把“配置文件里的概念”转换成 `SessionConfig`，顺便把 provider 映射成 `openai` 或 `anthropic` 协议。
4. `AgentConfig.__post_init__()` 确保 `skills/`、`data/`、`memory/`、`workspace/` 这些目录都存在。
5. `AgentConfig.load_session_configs()` 再决定当前任务到底用桌面端、多模型 JSON，还是 `.env` 单模型配置。

这就是为什么 Chrysalis 的配置不是“一个大 JSON”而已，而是分成了读取、归一化、路由三层。

## `.env` 单模型配置

`.env.example` 提供了最小模板：

```text
CHRYSALIS_LLM_PROVIDER=deepseek
CHRYSALIS_API_KEY=你的模型服务APIKey
CHRYSALIS_BASE_URL=https://api.deepseek.com
CHRYSALIS_MODEL=deepseek-v4-pro
CHRYSALIS_PERMISSION_LEVEL=balanced
```

常用环境变量：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `CHRYSALIS_LLM_PROVIDER` | 模型服务类型；`anthropic` / `claude` 走 Anthropic 协议，其他值走 OpenAI 兼容协议 | `deepseek` |
| `CHRYSALIS_API_KEY` | 模型 API Key | 空 |
| `CHRYSALIS_BASE_URL` | API 基础地址 | 根据 provider 推断 |
| `CHRYSALIS_MODEL` | 模型名称 | 根据 provider 推断 |
| `CHRYSALIS_TEMPERATURE` | 采样温度 | `0.2` |
| `CHRYSALIS_MAX_TOKENS` | 单次回复最大输出 token | `4096` |
| `CHRYSALIS_CONTEXT_WINDOW` | 模型上下文窗口，用于预算和压缩判断 | `28000` |
| `CHRYSALIS_MAX_RETRIES` | 模型请求最大重试次数 | `4` |
| `CHRYSALIS_API_TIMEOUT` | 模型请求读超时秒数 | `60` |
| `CHRYSALIS_PROXY` | 请求代理地址 | 空 |
| `CHRYSALIS_PERMISSION_LEVEL` | 权限等级：`locked`、`balanced`、`full` | `balanced` |
| `CHRYSALIS_MAX_TURNS` | 单个任务最多工具循环轮数 | `70` |
| `CHRYSALIS_INPUT_PRICE` / `CHRYSALIS_OUTPUT_PRICE` | 每百万 token 价格，用于费用估算 | `0` |

OpenAI 兼容服务的配置示例：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.example.com/v1
CHRYSALIS_MODEL=example-chat-model
```

Anthropic 协议配置示例：

```text
CHRYSALIS_LLM_PROVIDER=anthropic
CHRYSALIS_API_KEY=sk-ant-xxx
CHRYSALIS_BASE_URL=https://api.anthropic.com/v1
CHRYSALIS_MODEL=claude-3-5-sonnet-latest
```

## 多模型配置

`configs/llm_models.json` 是数组，数组顺序就是 Failover 顺序：第一个模型是主模型，后面的模型是备用模型。主模型失败时会自动切换到备用模型；切换后经过一段时间会尝试回到主模型。

推荐使用环境变量引用 API Key，避免把密钥写死到仓库：

```json
[
  {
    "name": "primary",
    "provider": "openai",
    "api_key": "${CHRYSALIS_API_KEY}",
    "base_url": "https://api.example.com/v1",
    "model": "primary-model",
    "context_window": 200000,
    "temperature": 0.2,
    "max_tokens": 4096,
    "max_retries": 4
  },
  {
    "name": "backup",
    "provider": "anthropic",
    "api_key": "${CHRYSALIS_BACKUP_API_KEY}",
    "base_url": "https://api.anthropic.com/v1",
    "model": "claude-3-5-sonnet-latest",
    "context_window": 200000
  }
]
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `name` | 在 TUI / 桌面端状态栏里显示的模型名称 |
| `provider` | 决定协议；`anthropic` / `claude` 使用 Anthropic，其余按 OpenAI 兼容处理 |
| `api_key` | 可直接写字符串，也可写 `${ENV_VAR}` 引用环境变量 |
| `base_url` | 服务商 API 地址 |
| `model` | 真实模型名 |
| `context_window` | 上下文窗口，影响压缩预算和进度条 |
| `temperature` | 采样温度 |
| `max_tokens` | 最大输出 token；为空时交给服务端默认值 |
| `max_retries` | 单模型内部重试次数 |
| `timeout` | 读超时秒数 |
| `proxy` | 代理地址 |
| `thinking` / `thinking_budget` | 支持思考块的模型可使用的扩展字段 |

## 运行目录

除非显式传入绝对路径，Chrysalis 会把长期状态放在项目根目录下。

| 路径 | 用途 |
| --- | --- |
| `data/sessions/` | 会话持久化文件，每个会话一个 JSON |
| `data/task_queue.json` | 交互模式任务队列 |
| `data/usage_history.jsonl` | token、turn、费用估算历史 |
| `data/permissions.json` | 永久授权记录 |
| `data/desktop_settings.json` | 桌面端模型和系统提示词设置 |
| `data/desktop_recovery.json` | 桌面端草稿恢复 |
| `data/cron/` | 定时任务定义和执行输出 |
| `data/task_outputs/tool_results/` | 大型工具输出归档 |
| `data/transcripts/` | 触发上下文应急压缩时保存的完整历史 |
| `memory/` | 长期记忆、SOP 和可复用脚本 |
| `skills/` | 技能库，保存可复用工作流、草稿和归档技能 |
| `workspace/` | 默认工作区，文件工具和代码工具常用根目录 |

## 权限等级

`CHRYSALIS_PERMISSION_LEVEL` 支持三档：

| 等级 | 行为 |
| --- | --- |
| `locked` | 更保守；看起来会修改本地状态的任务和工具更容易要求确认 |
| `balanced` | 默认；文件写入、代码运行、浏览器 JS、截图、子 Agent 等动作会询问 |
| `full` | 信任模式；不经过普通权限确认，适合只在自己可控环境中使用 |

别名：

- `strict`、`safe`、`ask` 会归一化为 `locked`。
- `normal`、`default` 会归一化为 `balanced`。
- `trusted`、`off`、`none` 会归一化为 `full`。

默认建议使用 `balanced`。如果你只是让 Chrysalis 读文档、总结项目，通常不会被频繁打断；如果任务需要写文件或执行脚本，它会在真正动手前请求确认。

## 桌面端配置

桌面端的设置页会写入 `data/desktop_settings.json`。当其中 `enabled` 为 `true` 时，它会覆盖 `.env` 和 `configs/llm_models.json`。

最小结构如下：

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

如果你希望所有入口都共用同一套模型配置，可以在桌面端设置中点击重置，让 `enabled` 回到 `false`，然后统一维护 `.env` 或 `configs/llm_models.json`。

## 如果你要新增一个配置项

最稳的改法是按这个顺序写：

1. 先判断它属于哪一层。
2. 如果是模型相关，就加到 `LLMConfig`。
3. 如果是运行目录或项目行为，就加到 `AgentConfig`。
4. 如果它要影响真正的会话参数，就继续传进 `SessionConfig`。
5. 如果桌面端也要改这个值，再同步 `data/desktop_settings.json` 和 Electron 设置页。
6. 最后把示例补到 `.env.example` 或 `configs/llm_models.example.json`。

一个简单判断法：

- 只影响单模型行为的，优先改 `configs/config.py::LLMConfig`。
- 只影响会话路由或多模型选择的，优先改 `configs/config.py::AgentConfig.load_session_configs()`。
- 只影响 UI 的，优先改桌面端设置文件和界面。
