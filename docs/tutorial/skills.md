# 技能库

技能库是 Chrysalis 的“可复用工作流仓库”。它和 `memory/` 不一样：

- `memory/` 更像事实和 SOP。
- `skills/` 更像真正可执行、可搜索、可晋升的能力单元。

如果一个任务做得很顺，之后还会反复遇到，最适合沉淀成 skill。

## 代码位置

| 文件 | 作用 |
| --- | --- |
| `chrysalis/skills/store.py` | 技能的存储、搜索、查看、创建、提升、归档 |
| `chrysalis/skills/curator.py` | 从成功任务里自动生成技能草稿 |
| `chrysalis/context_engine.py` | 把相关技能注入当前任务上下文 |
| `chrysalis/tools/skill_tools.py` | 暴露 `skill_*` 工具给模型使用 |

## 第 1 步：先理解技能的目录结构

`SkillStore` 默认管理的是这样的目录：

```text
skills/
  browser/
    browser-login-flow/
      skill.json
      SKILL.md
      references/
      templates/
      scripts/
      assets/
  file/
    safe-patch-workflow/
      skill.json
      SKILL.md
  .drafts/
    temp-skill/
  .archive/
    old-skill/
```

每个技能目录里最关键的是两个文件：

| 文件 | 作用 |
| --- | --- |
| `skill.json` | 机器可读元数据 |
| `SKILL.md` | 模型和人都能读的正文 |

## 第 2 步：看 `skill.json` 放什么

`SkillStore.create()` 会写入这样的元数据：

```json
{
  "id": "browser.browser-login-flow",
  "name": "browser-login-flow",
  "title": "Browser Login Flow",
  "description": "网页登录流程",
  "category": "browser",
  "tags": ["browser", "login"],
  "status": "draft",
  "version": "1.0.0",
  "created_at": "...",
  "updated_at": "...",
  "stats": {
    "uses": 0,
    "successes": 0,
    "failures": 0,
    "last_used_at": null
  }
}
```

这些字段不是摆设：

- `status` 决定它是 draft、active 还是 archived。
- `tags` 和 `category` 参与搜索。
- `stats` 记录以后被用过多少次。
- `description` 和 `title` 会直接进入检索摘要。

## 第 3 步：看 `SKILL.md` 怎么写

如果没有正文，`SkillStore` 会生成一个默认模板，结构是：

```md
# Title

Description

## When To Use

## Steps

## Failure Modes
```

实际写技能时，最重要的是这三块：

1. `When To Use`：什么时候该用。
2. `Steps`：具体怎么做。
3. `Failure Modes`：常见失败和恢复方式。

这和普通文档不一样。技能文档要尽量短，而且直接面向复用。

## 第 4 步：技能是怎么被找到的

`SkillStore.search()` 会把这些内容一起算相关性：

- skill id / name / title
- description
- category
- tags
- when_to_use
- tools
- sop_refs
- body 正文

所以模型不是“瞎猜技能”，而是根据任务语义和关键词检索。

`SkillStore.context_for_task()` 会把搜索结果压缩成短上下文，类似：

```text
[Relevant Skills]
- browser-login-flow (score=2.30)
  summary: a reusable browser login workflow
  when_to_use: 遇到网页登录墙时; 需要复用登录步骤时
  key_steps: 先扫描页面 | 再确认登录态 | 再执行目标动作
  notes: 处理私密浏览器状态时需要用户确认
```

`ContextEngine._related_skills()` 就是把这段文本注入当前系统提示词的入口。

## 第 5 步：技能是怎么自动生成的

任务成功后，`AgentLoop.run()` 会调用：

```text
SkillCurator.maybe_create_draft(...)
```

`SkillCurator` 的判断逻辑很保守：

- 任务必须成功。
- 不能处于 need_user 或 cancelled 状态。
- 如果 `start_long_term_update` 被调用过，优先考虑生成草稿。
- 否则看任务是否足够复杂，例如：
  - `turns >= min_turns`
  - 或工具调用次数够多

生成草稿后，会把任务轨迹写到 `trace.json`，方便后续审查。

## 第 6 步：手工创建和管理技能

模型可以通过工具直接管理技能库：

| 工具 | 用途 |
| --- | --- |
| `skill_list` | 列出技能 |
| `skill_search` | 搜索相关技能 |
| `skill_view` | 查看技能正文或 linked files |
| `skill_create` | 新建草稿或正式技能 |
| `skill_promote` | 把草稿提升为 active |
| `skill_archive` | 归档旧技能 |

### `skill_create`

最常见的写法是先创建草稿：

```json
{
  "tool": "skill_create",
  "args": {
    "name": "browser-login-flow",
    "description": "网页登录流程",
    "body": "# Browser Login Flow\n\n## When To Use\n- ...\n\n## Steps\n1. ...",
    "category": "browser",
    "status": "draft"
  }
}
```

### `skill_promote`

当草稿已经审完、确认可复用后，再提升为 active：

```json
{"tool":"skill_promote","args":{"name":"browser-login-flow"}}
```

### `skill_view`

`skill_view` 可以看：

- `SKILL.md`
- `references/`
- `templates/`
- `scripts/`
- `assets/`

这很适合一个技能下面有额外模板或脚本时使用。

## 第 7 步：技能和长期记忆的区别

这两个东西很容易混：

| 项目 | 长期记忆 | 技能库 |
| --- | --- | --- |
| 重点 | 事实、SOP、索引 | 可复用工作流 |
| 载体 | `memory/` | `skills/` |
| 进入 prompt 方式 | `ContextEngine._memory_sections()` | `ContextEngine._related_skills()` |
| 生成方式 | 人工维护为主 | `SkillCurator` 可自动生成草稿 |
| 管理工具 | 直接编辑文件 | `skill_*` 工具 |

一句话说：**长期记忆告诉模型“世界是什么样”，技能库告诉模型“这种活应该怎么做”。**

## 一个推荐的技能写法

如果你自己要写技能，建议按这个顺序：

1. 先写一句触发条件。
2. 再写 3 到 7 步的稳定流程。
3. 再列 2 到 5 个失败模式。
4. 如果有脚本或模板，把它们放进 `scripts/` 或 `templates/`。
5. 如果这个技能以后会被重复调用，尽量保持正文简短。

## 从零手写一个 skill

如果不用 `skill_create`，也可以直接按 `SkillStore` 的目录约定手写：

1. 先创建目录：`skills/<category>/<name>/`。
2. 写 `skill.json`，其中 `id` 最好保持 `<category>.<name>`。
3. 写 `SKILL.md`，正文给模型读，不要写成普通长文。
4. 有模板就放进 `templates/`，有脚本就放进 `scripts/`，有参考资料就放进 `references/`。
5. 用 `skill_search` 或 `SkillStore.search()` 搜一下，确认标题、描述、tags 和正文能被命中。

最小目录如下：

```text
skills/
  docs/
    docs-code-walkthrough/
      skill.json
      SKILL.md
      templates/
      references/
```

`SkillStore.search()` 会同时看 `name`、`title`、`description`、`category`、`tags`、`when_to_use` 和正文，所以不要只把关键词写在正文深处。最稳的写法是：

- `title` 写成人能看懂的名字。
- `description` 写一句可搜索摘要。
- `tags` 写任务里常出现的关键词。
- `SKILL.md` 的 `When To Use` 明确触发条件。

## 你在代码里最常会改的函数

| 函数 | 作用 |
| --- | --- |
| `SkillStore.create()` | 创建技能 |
| `SkillStore.search()` | 搜索技能 |
| `SkillStore.context_for_task()` | 生成技能上下文 |
| `SkillStore.promote()` | 提升草稿 |
| `SkillStore.archive()` | 归档技能 |
| `SkillCurator.maybe_create_draft()` | 自动生成草稿 |
| `SkillCurator._should_create()` | 决定是否值得生成草稿 |

## 为什么要单独一层技能库

因为有些经验不仅是“事实”，而且是“流程”：

- 事实可以写进 `memory/global_mem.txt`。
- SOP 可以写进 `memory/*.md`。
- 但可复用流程更适合单独做成 skill，方便搜索、审查和晋升。

一句话总结：**技能库是 Chrysalis 把一次成功做法变成下一次可复用能力的正式入口。**
