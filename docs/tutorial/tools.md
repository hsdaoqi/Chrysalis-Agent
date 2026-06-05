# 工具调用

Chrysalis 的能力不是靠“聊天”堆出来的，而是靠工具调用闭环。模型决定要做什么，工具真正去执行，Observation 再回到模型继续推理。

## 工具链总流程

一次工具调用大致会经历：

```text
模型输出 tool_use / JSON
  -> PermissionEngine 判断是否需要确认
  -> run_tool() 执行对应函数
  -> observation 返回 ok / error / 额外标记
  -> AgentLoop._handle_agent_tool_side_effects()
  -> 结果压缩后送回模型
  -> 模型决定继续调用工具还是输出 final
```

这就是 Chrysalis 真正做事的地方。

## 工具是怎么注册的

工具通过 `@tool(...)` 装饰器注册到全局 registry。导入 `chrysalis.tools` 时，会自动加载：

- `file_tools`
- `code_tools`
- `web_tools`
- `agent_tools`
- `skill_tools`
- `vision_tools`

然后 `chrysalis/tools/__init__.py` 会自动生成：

- `TOOL_PROMPT`：给 JSON-in-text 模式用。
- `TOOLS_SCHEMA`：给 OpenAI function calling 用。

这意味着你新增一个工具，只要按本地代码风格加一个函数并导入模块，通常就能自动进入工具列表。

## 文件工具

| 工具 | 作用 |
| --- | --- |
| `file_read` | 读取文件内容，可按行号或关键词定位 |
| `file_write` | 写入、追加或前置文本 |
| `file_patch` | 替换唯一匹配文本块 |

### `file_read`

它会先通过 `safe_path()` 解析路径，再读取文本。支持：

- `start`：起始行号。
- `count`：读取行数。
- `keyword`：按关键词定位。
- `show_linenos`：是否显示行号。

如果读的是敏感路径，比如 `.env`、私钥或受保护目录，`PermissionEngine` 可能要求确认。

### `file_write`

支持三种模式：

- `overwrite`
- `append`
- `prepend`

工具本身只负责写文件，是否允许由权限引擎决定。

### `file_patch`

`file_patch` 设计得很保守：`old_content` 必须唯一匹配，否则会失败。这样能减少误改。

建议流程是：

1. 先 `file_read`。
2. 确认唯一目标块。
3. 再 `file_patch`。
4. 最后回读或看 diff。

## 代码工具

| 工具 | 作用 |
| --- | --- |
| `code_run` | 执行 Python、PowerShell 或 Bash |

### Python 分支

`code_run` 的 Python 模式会：

1. 检查高风险片段。
2. 在临时文件里拼接 prelude。
3. 用 `subprocess.run()` 执行。
4. 尝试解析最后一行 JSON 作为结构化返回。

### Shell 分支

Shell 分支支持 `powershell` 和 `bash`，并会拦截一批危险命令模式，例如删除、强制重置和关机类命令。

这也是为什么它适合做“受控脚本执行”，不适合直接当无约束 shell。

## 网页工具

| 工具 | 作用 |
| --- | --- |
| `web_scan` | 打开或扫描真实浏览器标签页 |
| `web_execute_js` | 在当前标签页执行 JavaScript |

这两个工具走的是本机浏览器的 CDP 调试协议，不是重新起一个模拟浏览器。

典型流程是：

1. `web_scan` 打开页面或扫描当前标签。
2. 页面如果需要登录、验证或授权，工具结果会提示用户操作。
3. `web_execute_js` 在当前标签页做脚本化交互。

## 视觉工具

| 工具 | 作用 |
| --- | --- |
| `screenshot` | 截取当前屏幕内容并附图给模型 |
| `ocr` | 对图片做文字识别 |

这类工具适合：

- 读界面文字。
- 检查屏幕状态。
- 处理无法直接用 DOM 或文件读出的内容。

它们分别依赖 `vision` 和 `ocr` extra。

## Agent 控制工具

| 工具 | 作用 |
| --- | --- |
| `ask_user` | 遇到阻塞时询问用户 |
| `update_working_checkpoint` | 更新当前任务的工作记忆 |
| `start_long_term_update` | 标记任务值得沉淀到长期记忆 |
| `todo_write` | 维护当前任务的 TODO 列表 |
| `spawn_subagent` | 派生子 Agent 并行执行子任务 |

这些工具不是直接产出最终答案，而是影响任务执行状态。

## 技能工具

这组工具和 `skills/` 目录对应，是 Chrysalis 的“技能库管理接口”。

| 工具 | 作用 |
| --- | --- |
| `skill_list` | 列出可用技能 |
| `skill_search` | 搜索相关技能 |
| `skill_view` | 查看某个技能正文或链接文件 |
| `skill_create` | 创建技能草稿或正式技能 |
| `skill_promote` | 将草稿提升为 active |
| `skill_archive` | 归档技能 |

### `skill_create`

`skill_create` 会调用 `SkillStore.create()`，在 `skills/` 下生成一套结构化技能目录。最重要的是：

- `skill.json`：机器可读元数据。
- `SKILL.md`：模型可读正文。

如果是草稿，默认放到 `skills/.drafts/` 下；如果是 active，则放到 `skills/<category>/<name>/`。

### `skill_search`

`skill_search` 会把任务关键词、tags、tools、正文一起打分，然后返回最相关的技能摘要。

### `skill_view`

`skill_view` 可以查看：

- `SKILL.md`
- linked files，例如 `references/`、`templates/`、`scripts/`、`assets/`

### `skill_promote` / `skill_archive`

这两个工具分别负责把草稿提升为 active，或者把旧技能归档。

## 这些工具是怎么被模型看到的

`Tool Registry` 会把每个工具的名字、说明和参数转成两种形式：

1. JSON-in-text 的文本 prompt。
2. OpenAI function calling 的 schema。

所以模型既可以在纯 JSON 模式下选工具，也可以在 function calling 模式下直接发起 tool call。

## 安全和权限

不是所有工具都会直接执行。`PermissionEngine` 会在这些场景里拦住它：

- 文件修改。
- 代码执行。
- 浏览器 JS。
- 截图。
- 派生子 Agent。
- 敏感路径读取。

权限等级见 [配置说明](/guide/configuration)。

## `workspace` 的作用

大多数文件和代码工具默认围绕项目的 `workspace/` 工作。这样做有两个好处：

1. 降低误写到仓库外部的风险。
2. 让模型更清楚“当前工作沙箱在哪里”。

如果你传绝对路径，`safe_path()` 仍会先做检查。

## 工具调用的几个小例子

### 读文件

```json
{"tool":"file_read","args":{"path":"docs/guide/installation.md","start":1,"count":80}}
```

### 改文件

```json
{"tool":"file_patch","args":{"path":"docs/index.md","old_content":"旧文本","new_content":"新文本"}}
```

### 跑脚本

```json
{"tool":"code_run","args":{"type":"python","script":"print('hello')"}}
```

### 维护 TODO

```json
{"tool":"todo_write","args":{"goal":"完善 docs","action":"set","todos":["重写安装指南","补充工具调用","补充技能库"]}}
```

### 创建技能草稿

```json
{"tool":"skill_create","args":{"name":"browser-login-flow","description":"网页登录流程","body":"# ...","category":"browser","status":"draft"}}
```

## 如果你要新增一个工具

按这个顺序写最稳：

1. 先想清楚这个工具是“只读”还是“会改状态”。
2. 在 `chrysalis/tools/` 下新建模块，或者直接补到现有模块里。
3. 用 `@tool(...)` 注册名字、说明和参数。
4. 函数签名保持 `(args: dict, workspace: Path | None = None) -> dict`。
5. 如果涉及文件，先走 `safe_path()`，不要自己拼路径。
6. 返回值里至少要有 `ok`，必要时再带 `error`、`path`、`content`、`need_user`、`_checkpoint`、`_long_term`。
7. 最后把模块导入 `chrysalis/tools/__init__.py`，让 registry 自动发现它。

最小模板可以长这样：

```python
from pathlib import Path

from chrysalis.tools.registry import tool
from chrysalis.tools.safety import safe_path


@tool("note_write", "把备注写入项目内文件", params={
    "path": "文件路径",
    "content": "要写入的文本",
})
def note_write(args: dict, workspace: Path | None = None) -> dict:
    target = safe_path(args["path"], workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(args.get("content", "")), encoding="utf-8")
    return {"ok": True, "path": str(target)}
```

这个模板和仓库里现有工具的关系很直接：

- `file_tools.py`：典型读写文件工具，先 `safe_path()` 再读写。
- `agent_tools.py`：不一定改文件，但会返回 `_checkpoint`、`_long_term`、`need_user` 这类状态标记。
- `skill_tools.py`：工具本身很薄，只负责把参数转给 `SkillStore`。

### 工具注册后发生什么

`chrysalis/tools/__init__.py` 导入模块后，会自动生成：

- `TOOL_PROMPT`：给 JSON-in-text 模式看的工具说明。
- `TOOLS_SCHEMA`：给 function calling 模式看的 schema。

所以新增工具时，你不是只写一个函数，而是在接入整条链路：

1. 模型看到工具说明。
2. 模型输出 tool call。
3. `run_tool()` 分发到你的函数。
4. `AgentLoop` 再根据结果做后续处理。

### 如果你的工具会改变工作流

优先想清楚它属于哪类结果：

- 只返回数据：直接 `{"ok": True, ...}`。
- 需要用户决策：返回 `{"ok": False, "need_user": True, ...}`。
- 要提示工作记忆更新：返回 `{"ok": True, "_checkpoint": True, ...}`。
- 要提示长期沉淀：返回 `{"ok": True, "_long_term": True, ...}`。

一句话总结：**工具的写法不难，难的是让它进入 Chrysalis 的注册、权限和回写链路里。**
