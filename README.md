# Chrysalis

这是一个从 GenericAgent 思路重新搭起来的极简种子项目。

目标不是一开始就做大，而是先让架构足够清楚，之后一点点长能力：

```text
用户任务
  -> Kernel 总装配
  -> AgentLoop 行动循环
  -> DeepSeek 模型
  -> 原子工具
  -> Memory 记忆
  -> 必要时自动沉淀一个技能
```

## 目录结构

```text
chrysalis/
  config.py        项目根路径和环境变量配置
  llm/deepseek.py  通过 OpenAI-compatible API 调用 DeepSeek
  tools.py         最小原子工具
  observation.py   工具观察结果压缩
  memory.py        memory/global_mem_insight.txt + data/memory.json
  trace.py         data/trace.log 运行轨迹摘要
  reflect.py       基于 trace.log 生成复盘报告
  skills.py        搜索并执行已有技能
  agent_loop.py    观察-行动循环
  evolve.py        最小技能写入器
  kernel.py        CLI 入口和模块装配

data/              机器可读的运行状态和 trace.log
data/reflections/  自动生成的运行复盘报告
memory/            人类可读的长期记忆和 SOP
skills/            后续沉淀出的技能
workspace/         工具默认读写的工作目录
```

所有默认路径都固定在项目主目录下，不跟随启动命令时的 shell 当前目录乱跑。

## 运行

```powershell
chrysalis "列出 workspace 里的文件"
```

或者：

```powershell
python -m chrysalis.kernel "列出 workspace 里的文件"
```

运行时默认会在终端显示每轮摘要，例如“第 1 轮：调用工具 file_list”。这些进度写到 stderr，最终 JSON 仍写到 stdout。需要安静输出时：

```powershell
python -m chrysalis.kernel --quiet "你的任务"
```

默认不会因为一次成功就新增技能。内核会按执行轨迹自动判断：只有超过 15 轮、工具轨迹足够复杂、没有调用过已有技能、也没有重复沉淀过时，才会写入一个回放技能。

在 `.env` 里配置 DeepSeek：

```text
CHRYSALIS_API_KEY=...
CHRYSALIS_MODEL=deepseek-chat
CHRYSALIS_BASE_URL=https://api.deepseek.com/v1
```

## 当前设计

这一版故意不把旧项目里的完整自进化流水线一次性塞回来。

它先保留一个容易读懂的小循环：

```text
把 L1 记忆注入 system -> 模型决定调用技能/工具 -> 观察结果 -> 再决定 -> 给最终回答
```

已经具备的克制沉淀规则：

- 简单任务不会写技能
- 调用过已有技能不会再写重复技能
- 只有超过 15 轮且有多次工具调用的成功流程，才会考虑沉淀
- 每次运行会向 `data/trace.log` 写入一行轨迹摘要
- 长摘要保留前 200 字和后 200 字，中间用省略号连接
- CLI 默认显示每轮行动摘要，避免长任务静默等待
- 工具观察会压缩后再喂给下一轮模型，避免大文件/网页撑爆上下文
- 可以用 `python -m chrysalis.kernel "复盘最近运行"` 生成复盘报告

后续我们再逐步加：

- 技能验证
- 失败修复
- 更强的长期记忆
- 更准确的技能泛化与去重

## 当前原子工具

- `file_list`：列出文件
- `file_read`：读取文本文件
- `file_write`：写入文本文件
- `web_fetch`：获取网页
- `code_run`：执行一段短 Python 代码

普通相对路径默认落在 `workspace/`。如果要读取项目资料，可以直接使用 `memory/...`、`data/...`、`skills/...`、`chrysalis/...`、`tests/...` 这类项目相对路径；也可以使用 `../` 或绝对路径访问其他目录。

## 运行复盘

```powershell
python -m chrysalis.reflect
```

或者：

```powershell
python -m chrysalis.kernel "复盘最近运行"
```

报告会写入 `data/reflections/`，只提供建议，不会自动改记忆或新增技能。










