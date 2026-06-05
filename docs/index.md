---
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "Chrysalis"
  text: "本地通用 Agent 运行时"
  tagline: 从命令行、TUI 到桌面端，统一使用同一套 Kernel、工具链、会话历史、记忆系统与技能库
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quickstart
    - theme: alt
      text: 安装配置
      link: /guide/installation
    - theme: alt
      text: Agent 原理
      link: /tutorial/overview
    - theme: alt
      text: GitHub
      link: https://github.com/hsdaoqi/Chrysalis-Agent

features:
  - title: 本地运行优先
    details: 默认在项目目录下读写 data、memory 和 workspace，不依赖远端服务保存会话状态。
  - title: 三种使用入口
    details: CLI 适合脚本化调用，TUI 适合长任务观察，Electron 桌面端适合会话、附件和工作区管理。
  - title: OpenAI / Anthropic 兼容
    details: 支持 OpenAI 兼容接口、Anthropic 协议、多模型配置和 Failover 自动切换。
  - title: 工具调用闭环
    details: 文件读写、代码执行、浏览器 CDP、截图、OCR、子 Agent 与用户询问都走统一 Tool Registry。
  - title: 记忆分层
    details: LLM History 负责会话连续性，Working Memory 负责当前任务，memory 目录负责长期经验和 SOP。
  - title: 技能沉淀
    details: 成功任务可以沉淀为 skills 草稿，审核后提升为 active，后续任务会按相关性自动注入。
  - title: 权限与安全
    details: locked、balanced、full 三档权限控制会在文件修改、代码运行、浏览器操作等动作前拦截确认。
---
