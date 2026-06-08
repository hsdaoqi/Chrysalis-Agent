---
layout: home

hero:
  name: "Chrysalis"
  text: "一个能读懂的本地 Agent 框架"
  tagline: 它不只是接一个大模型，而是把“模型 + 工具 + 记忆 + 权限 + 会话”组装成一个能在你本机真正做事的 Agent。这套文档会带你从“跑起来”一路读到“源码怎么工作”。
  image:
    src: /logo.png
    alt: Chrysalis
  actions:
    - theme: brand
      text: 5 分钟跑起来
      link: /guide/installation
    - theme: alt
      text: 读懂 Agent 原理
      link: /tutorial/overview
    - theme: alt
      text: GitHub
      link: https://github.com/hsdaoqi/Chrysalis-Agent

features:
  - icon: 🧩
    title: 一套内核，多个入口
    details: CLI、连续交互、TUI、Electron 桌面端、QQ/微信/飞书网关，背后都是同一个 Python Kernel。会话、配置、权限、记忆全部共享。
  - icon: 🔌
    title: OpenAI / Anthropic 双协议
    details: 内部维护一份统一的 canonical history，出口处再转成 OpenAI Chat、OpenAI Responses 或 Anthropic 协议，并支持多模型 Failover 自动切换。
  - icon: 🛠️
    title: 工具调用闭环
    details: 文件读写、代码执行、浏览器扫描、网页抓取、截图、OCR、子 Agent、技能管理，全部通过 Tool Registry 暴露给模型，并经过权限引擎把关。
  - icon: 🧠
    title: 四层记忆系统
    details: LLM History 存完整会话，Working Memory 管单次任务进度，memory/ 存事实与 SOP，skills/ 沉淀可复用工作流。
  - icon: 📉
    title: 长任务不爆上下文
    details: 运行时按 raw → micro → snip → full 四级压缩历史，并修复 tool_use / tool_result 配对，尽量保住关键事实与协议合法性。
  - icon: 🔒
    title: 本地安全边界
    details: locked / balanced / full 三档权限，会在写文件、跑脚本、执行浏览器 JS、截图、读敏感路径前判断是否需要你确认。
---

## 这套文档怎么读

Chrysalis 的文档分成两部分。你不需要从头读到尾，按自己的目标挑就行。

```mermaid
flowchart LR
  A[你想做什么?] --> B[只想用起来]
  A --> C[想读懂 / 改造源码]
  B --> B1[安装 → 配置 → 快速开始]
  B1 --> B2[挑一个入口: TUI / 桌面端 / 网关]
  C --> C1[先读 Agent 是什么]
  C1 --> C2[再读 Kernel 与循环]
  C2 --> C3[按专题深入: 模型层 / 行动层 / 记忆层]
  C3 --> C4[最后动手扩展]
```

### 第一部分：上手使用（`guide/`）

如果你只想先把它用起来，按顺序读这几篇就够：

1. [安装指南](/guide/installation) —— 把环境和命令装好。
2. [配置模型](/guide/configuration) —— 让模型 API 连得上，理清三层配置优先级。
3. [快速开始](/guide/quickstart) —— 实际跑一个任务，体验工具调用、权限确认、会话与队列。
4. 然后按喜好挑一个入口：[TUI 终端界面](/guide/tui)、[Electron 桌面端](/guide/desktop) 或 [消息网关](/guide/gateway)。

### 第二部分：Agent 原理（`tutorial/`）

如果你想读懂 Chrysalis 是怎么工作的，甚至想改造它，这部分是一本“边讲原理边拆源码”的小书。每一章都从一个真实问题出发，带你读真实代码，配流程图，并在结尾给动手练习。

建议从 [第 1 章：Agent 是什么](/tutorial/overview) 开始顺着读。如果你只关心某个子系统，也可以直接跳：

- 想搞清楚一次任务怎么跑完整个闭环 → [第 2 章 Kernel 与循环](/tutorial/kernel-and-loop)
- 想接入新模型 / 理解协议转换 → [第 4 章 LLM 协议适配层](/tutorial/llm-protocol)
- 想加一个新工具 → [第 6 章 工具调用](/tutorial/tools) + [第 12 章 动手扩展](/tutorial/extending)
- 想搞懂权限边界 → [第 7 章 权限系统](/tutorial/permission)
- 想让 Agent 记住经验 → [第 8~10 章 记忆与技能](/tutorial/working-memory)

::: tip 一句话理解 Chrysalis
普通聊天机器人是“你问一句、它答一句”。Chrysalis 是“你给一个目标，它按步骤调用本机工具，把任务真的做完”。这套文档讲的就是它如何做到这一点。
:::
