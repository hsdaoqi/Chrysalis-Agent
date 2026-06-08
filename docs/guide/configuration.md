# 配置说明

Chrysalis 的配置分为三层：模型连接、运行目录、权限策略。理解这三层后，你就能回答三个最常见的问题：

```text
这次任务到底会用哪个模型？
Agent 默认会在哪个目录读写文件？
为什么有些操作会弹出权限确认？
```

如果你是第一次配置，先记住一条主线：

```text
.env 适合单模型入门。
configs/llm_models.json 适合多模型和备用模型。
data/desktop_settings.json 适合桌面端覆盖配置。
```

## 配置优先级

`AgentConfig.load_session_configs()` 的模型加载顺序是：

1. `data/desktop_settings.json`：桌面端设置中 `enabled: true` 时生效。
2. `configs/llm_models.json`：存在且内容是非空 JSON 数组时生效。
3. `.env`：前两项都不可用时，回退到单模型环境变量。

这意味着：如果你改了 `.env` 但模型没有变化，先检查 `configs/llm_models.json` 或桌面端设置是否覆盖了它。

一个非常实用的排查顺序：

1. 先看桌面端是否启用了模型设置。
2. 再看 `configs/llm_models.json` 是否存在且不是空数组。
3. 最后才看 `.env`。

这不是为了复杂，而是为了让不同使用方式都方便：

| 使用方式 | 推荐配置位置 |
| --- | --- |
| 刚开始用，只接一个模型 | `.env` |
| 想配置主模型和备用模型 | `configs/llm_models.json` |
| 希望在桌面端界面里调整模型 | `data/desktop_settings.json` |

## 配置是怎么进入运行时的

如果你按源码顺序看，会是这样一条链：

1. `configs/config.py` 先用 `load_dotenv(PROJECT_ROOT / ".env")` 读取环境变量。
2. `LLMConfig` 从这些环境变量里拿到 provider、api key、base_url、model、温度和上下文窗口。
3. `LLMConfig.to_session_config()` 把“配置文件里的概念”转换成 `SessionConfig`，顺便把 provider 映射成 `openai` 或 `anthropic` 协议。
4. `AgentConfig.__post_init__()` 确保 `skills/`、`data/`、`memory/`、`workspace/` 这些目录都存在。
5. `AgentConfig.load_session_configs()` 再决定当前任务到底用桌面端、多模型 JSON，还是 `.env` 单模型配置。

这就是为什么 Chrysalis 的配置不是“一个大 JSON”而已，而是分成了读取、归一化、路由三层。

用更白话的方式说：

1. `.env` 只是原始文本。
2. `LLMConfig` 把原始文本变成 Python 对象。
3. `SessionConfig` 把配置变成模型会话真正需要的参数。
4. `create_client()` 再决定创建单模型会话还是 Failover 会话。

这条链路的好处是，后面无论你来自 `.env`、JSON 文件还是桌面端设置，最终都能变成统一的 `SessionConfig`。

## `.env` 单模型配置

`.env.example` 提供了最小模板：

```text
CHRYSALIS_LLM_PROVIDER=deepseek
CHRYSALIS_API_KEY=你的模型服务APIKey
CHRYSALIS_BASE_URL=https://api.deepseek.com
CHRYSALIS_MODEL=deepseek-chat
CHRYSALIS_PERMISSION_LEVEL=balanced
```

如果你不确定怎么填，可以按下面的方式理解：

```text
provider   告诉 Chrysalis 走哪类协议。
api_key    证明你有权限调用模型服务。
base_url   模型服务的入口地址。
model      具体要调用哪个模型。
```

先填对这四个，项目就能跑最小任务。

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
| `CHRYSALIS_PROMPT_CACHE_ENABLED` | 是否启用 provider 支持的 prompt cache 标记 | `true` 或由代码默认决定 |

OpenAI 兼容服务的配置示例：

```text
CHRYSALIS_LLM_PROVIDER=openai
CHRYSALIS_API_KEY=sk-xxx
CHRYSALIS_BASE_URL=https://api.example.com/v1
CHRYSALIS_MODEL=example-chat-model
```

如果服务商文档写的是“OpenAI compatible”，通常就可以使用这种方式。这里的 `provider=openai` 不一定表示你必须用 OpenAI 官方服务，它更多表示“使用 OpenAI 风格的请求格式”。

Anthropic 协议配置示例：

```text
CHRYSALIS_LLM_PROVIDER=anthropic
CHRYSALIS_API_KEY=sk-ant-xxx
CHRYSALIS_BASE_URL=https://api.anthropic.com/v1
CHRYSALIS_MODEL=claude-3-5-sonnet-latest
```

如果 provider 是 `anthropic` 或 `claude`，Chrysalis 会在 `LLMConfig.to_session_config()` 里把它归一化为 Anthropic 协议。

### 常见 `.env` 错误

| 错误现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| 401 / unauthorized | API Key 错误或没有权限 | 重新复制 key，确认没有多余空格 |
| 404 / model not found | `model` 名称不对 | 到服务商后台确认真实模型名 |
| 404 / route not found | `base_url` 路径不对 | 检查是否需要 `/v1` |
| 一直超时 | 网络或代理问题 | 配置 `CHRYSALIS_PROXY` 或检查网络 |
| 改 `.env` 不生效 | 被 JSON 或桌面端覆盖 | 按配置优先级排查 |

## 多模型配置

`configs/llm_models.json` 是数组，数组顺序就是 Failover 顺序：第一个模型是主模型，后面的模型是备用模型。主模型失败时会自动切换到备用模型；切换后经过一段时间会尝试回到主模型。

最适合用多模型配置的场景：

- 主模型偶尔不稳定，希望自动切到备用模型。
- 想给不同模型配置不同 `context_window`。
- 想把 API Key 分散到不同环境变量里。
- 想在 TUI 或桌面端状态里看到更清楚的模型名称。

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

这里的 `${CHRYSALIS_API_KEY}` 会从环境变量里取值。这样做比直接把 key 写进 JSON 更安全，也更适合提交配置模板。

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

### Failover 怎么工作

当 `configs/llm_models.json` 中有多个模型时，`create_client()` 会创建 `FailoverSession`。它对外看起来和普通 `BaseSession` 一样，但内部持有多个模型会话。

简化流程：

```text
请求主模型
  -> 成功：直接返回
  -> 失败：切到备用模型
  -> 备用模型成功：继续任务
  -> 经过 spring back 时间后：尝试回到主模型
```

这层设计让 `AgentLoop` 不需要关心“现在到底是哪个模型在回答”。它只和 `LLMClient` 对话。Failover 的完整机制（轮询、300 秒回切）见 [第 4 章 4.6 节](/tutorial/llm-protocol#_4-6-多模型容错-failoversession)。

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

零基础读者可以这样记：

```text
data/      程序运行过程中产生的数据。
memory/    给 Agent 长期看的事实、SOP、脚本。
skills/    可复用工作流。
workspace/ Agent 做任务时默认放文件的地方。
```

这几个目录的定位不同，不建议混用。比如临时输出文件放 `workspace/` 或 `data/task_outputs/` 更合适；长期 SOP 放 `memory/` 更合适；能复用的流程放 `skills/` 更合适。

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

### 哪些工具通常会触发权限确认

| 动作 | 为什么要确认 |
| --- | --- |
| `file_write` / `file_patch` | 会修改本地文件 |
| `code_run` | 可能执行任意代码或 shell 命令 |
| `web_execute_js` | 会在你的本机浏览器页面里执行脚本 |
| `screenshot` | 会读取当前屏幕内容，可能包含隐私 |
| 敏感路径读取 | 例如 `.env`、密钥文件、权限记录 |
| `spawn_subagent` | 会派生新的 Agent 上下文继续执行任务 |

永久授权会保存到：

```text
data/permissions.json
```

如果你想回到更保守的状态，可以删除对应授权记录，或直接切换到 `locked`。

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

桌面端覆盖配置很适合“临时试一个模型”。但如果你希望团队或多入口长期保持一致，建议把稳定配置写进 `.env` 或 `configs/llm_models.json`，桌面端只做调试。

## 网关配置

如果你要把 Chrysalis 接到 QQ、个人微信或飞书，需要安装：

```bash
pip install -e ".[gateway]"
```

常见变量：

| 变量 | 用途 |
| --- | --- |
| `CHRYSALIS_GATEWAY_ALLOWED_TOOLS` | 远程聊天额外允许的工具列表 |
| `CHRYSALIS_ONEBOT_WS_URL` | NapCat / Lagrange 等 OneBot v11 WebSocket 地址 |
| `CHRYSALIS_ONEBOT_ACCESS_TOKEN` | OneBot 鉴权 token |
| `CHRYSALIS_ONEBOT_REQUIRE_MENTION` | 群聊是否需要 @ 才触发 |
| `CHRYSALIS_WECHAT_TOKEN_FILE` | 个人微信登录 token 保存路径 |
| `CHRYSALIS_FEISHU_APP_ID` | 飞书自建应用 app id |
| `CHRYSALIS_FEISHU_APP_SECRET` | 飞书自建应用 app secret |
| `CHRYSALIS_FEISHU_REQUIRE_MENTION` | 飞书群聊是否需要 @ 才触发 |

远程网关默认更保守。原因很简单：QQ、微信、飞书里的用户不一定可信，也不能像本机用户一样看到并确认权限弹窗。

因此 `GatewayPermissionEngine` 会拒绝远程用户触发需要本机确认的动作。即使你用 `CHRYSALIS_GATEWAY_ALLOWED_TOOLS=*` 暴露全部工具名，需要本机权限确认的操作也不会交给远程聊天用户批准。

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

## 配置排查清单

遇到问题时可以照这张表查：

| 问题 | 检查点 |
| --- | --- |
| 不知道用了哪个模型 | 看桌面端设置是否 enabled，再看 `configs/llm_models.json`，最后看 `.env` |
| API Key 不生效 | 确认没有被 JSON 或桌面端覆盖，确认 key 没有空格 |
| 读写目录不符合预期 | 看工具参数是否传了绝对路径，默认工作区通常是 `workspace/` |
| 权限一直弹 | 看 `CHRYSALIS_PERMISSION_LEVEL` 是否是 `locked`，以及是否没有永久授权 |
| 权限完全不弹 | 看是否设置成 `full` 或别名 `trusted` / `off` / `none` |
| 网关里工具不可用 | 看 `CHRYSALIS_GATEWAY_ALLOWED_TOOLS` 和网关权限策略 |
| 桌面端和 CLI 模型不同 | 看 `data/desktop_settings.json` 的 `enabled` |

一句话总结：**Chrysalis 的配置不是只为了“连上模型”，它同时决定模型路由、运行目录、安全边界和多入口行为。**
