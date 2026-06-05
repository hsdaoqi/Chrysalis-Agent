# 长期记忆

长期记忆负责跨会话沉淀经验。它不是把整段历史都塞回模型，而是把真正有复用价值的内容拆成几层：

- `memory/`：事实、SOP、流程和避坑。
- `skills/`：可复用工作流和技能库。
- `data/transcripts/`：上下文压缩时保存的完整会话快照。

如果说工作记忆是“本次任务的临时桌面”，那长期记忆就是“以后还会再来翻的抽屉”。

## 代码里是谁在管它

| 文件 | 作用 |
| --- | --- |
| `chrysalis/context_engine.py` | 组装长期记忆、技能、工作记忆和会话锚点 |
| `chrysalis/skills/store.py` | 扫描、搜索、查看、创建、提升和归档技能 |
| `chrysalis/skills/curator.py` | 从成功任务里生成技能草稿 |
| `memory/*.md` | SOP 和事实库 |
| `memory/*.py` | 可复用脚本 |

## 第 1 层：L1 索引

`memory/global_mem_insight.txt` 是索引层。它的目标不是解释所有细节，而是给模型一个“去哪里找”的地图。

典型内容是：

- 高频场景的索引。
- 少量红线规则。
- 指向 L2/L3 的最短入口。

例如它可能告诉模型：

```text
L0(META-SOP): memory_management_sop
L2: global_mem.txt
L3: plan_sop | verify_sop | git_sop
```

索引层要短、准、克制。它不是教程正文。

## 第 2 层：L2 全局事实库

`memory/global_mem.txt` 存的是经过验证的事实。比如：

- 已确认的路径。
- 已验证的环境信息。
- 项目级配置事实。
- 反复踩坑后总结出的全局规则。

这层适合“以后再遇到也还成立”的内容。

## 第 3 层：L3 SOP 和脚本

`memory/` 里的 `*.md` 和 `*.py` 是更细的可复用资产。你现在仓库里已经有这些典型文件：

- `plan_sop.md`
- `verify_sop.md`
- `git_sop.md`
- `web_setup_sop.md`
- `tmwebdriver_sop.md`
- `memory_management_sop.md`
- `memory_cleanup_sop.md`

这些文件适合写：

- 某类任务的标准操作步骤。
- 一旦忘记就会反复试错的坑。
- 特定工具的前置条件。

## 第 4 层：技能库 skills/

`skills/` 是和 `memory/` 并行的另一套长期层。区别是：

- `memory/` 更像事实库和 SOP 库。
- `skills/` 更像可检索、可评价、可提升的工作流库。

代码里对应的是：

- `chrysalis/skills/store.py::SkillStore`
- `chrysalis/skills/curator.py::SkillCurator`

`ContextEngine._related_skills()` 会根据任务、工作记忆和 SOP 线索，从 `skills/` 中搜索最相关的 active skill，然后把摘要注入系统提示词。

## ContextEngine 怎么读这些内容

`ContextEngine.assemble()` 的顺序可以理解为：

1. 读取 `global_mem_insight.txt`。
2. 注入 `WorkingMemory`。
3. 注入 TODO 提醒。
4. 注入 session_context。
5. 按关键词挑选相关的 `memory/*.md` 或 `memory/*.py`。
6. 按相关性挑选 `skills/` 中的 active skill。
7. 把会话锚点拼进去。

这意味着长期记忆不是固定塞一大坨文本，而是按任务动态组装。

## 技能库是怎么被搜索出来的

`SkillStore.search()` 会把任务文本、标签、工具名和 skill body 一起算相关性。它会优先看：

- 任务短语是否直接命中。
- query 里的 token 是否命中标题、描述、tags、tools。
- skill body 的正文是否包含关键步骤。

然后 `context_for_task()` 会生成一段很短的上下文，例如：

```text
[Relevant Skills]
- browser-login-flow (score=2.30)
  summary: a reusable browser login workflow
  when_to_use: 遇到网页登录墙时
  key_steps: 先扫描页面 | 再确认登录态 | 最后执行操作
  notes: 处理私密浏览器状态时需要用户确认
```

这段不会替代完整技能正文，它只是让模型知道“有这个技能，可以再看”。

## 一个完整的长期记忆工作流

### 情况 A：你要补一条 SOP

1. 先把事实验证清楚。
2. 写进 `memory/*.md`。
3. 在 `global_mem_insight.txt` 里加一行索引。
4. 如果是全局事实，再补一条 `global_mem.txt`。

### 情况 B：你要沉淀成一个技能

1. 让 Agent 先完成真实任务。
2. 如果任务成功且足够复杂，`SkillCurator` 可能自动生成草稿。
3. 人工检查草稿的 `SKILL.md`、`skill.json` 和 trace。
4. 用 `skill_promote` 提升为 active。
5. 后续任务会在 `ContextEngine` 里自动检索到它。

## 为什么不把所有内容都塞进一个文件

因为不同内容的生命周期不一样：

- 事实库要稳。
- SOP 要短。
- 技能要可执行、可审查、可迭代。
- 历史快照要可追溯，但不一定要直接进 prompt。

把这些混成一个大文件，模型会更难判断重点。

## 你会在代码里最常碰到的几个函数

| 函数 | 作用 |
| --- | --- |
| `ContextEngine._memory_sections()` | 选择本次注入的记忆段 |
| `ContextEngine._select_related_files()` | 从 `memory/` 里挑相关 SOP |
| `ContextEngine._related_skills()` | 从 `skills/` 里挑相关 skill |
| `SkillStore.search()` | 搜索技能 |
| `SkillStore.context_for_task()` | 生成技能摘要上下文 |
| `SkillCurator.maybe_create_draft()` | 从成功任务里生成技能草稿 |

## 如果你要新增一类长期经验

最稳的写法是分层：

1. 如果是事实，放 `memory/global_mem.txt`。
2. 如果是 SOP，放 `memory/*.md`。
3. 如果是可复用工作流，放 `skills/`。
4. 如果只是当前任务的临时状态，放 `WorkingMemory`，不要写进长期层。

一句话总结：**长期记忆不是一块大缓存，而是事实库、SOP 库和技能库共同组成的分层系统。**
