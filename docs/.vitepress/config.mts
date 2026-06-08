import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(defineConfig({
  title: 'Chrysalis',
  description: '一个本地通用 Agent 框架 —— 从使用到读懂源码',
  lang: 'zh-CN',
  base: '/Chrysalis-Agent/',

  themeConfig: {
    logo: '/logo.png',

    nav: [
      { text: '首页', link: '/' },
      { text: '上手使用', link: '/guide/installation' },
      { text: 'Agent 原理', link: '/tutorial/overview' },
      { text: 'GitHub', link: 'https://github.com/hsdaoqi/Chrysalis-Agent' },
    ],

    sidebar: {
      '/guide/': [
        {
          text: '上手使用',
          items: [
            { text: '安装指南', link: '/guide/installation' },
            { text: '配置模型', link: '/guide/configuration' },
            { text: '快速开始', link: '/guide/quickstart' },
            { text: 'TUI 终端界面', link: '/guide/tui' },
            { text: 'Electron 桌面端', link: '/guide/desktop' },
            { text: '消息网关', link: '/guide/gateway' },
            { text: '安装浏览器扩展', link: '/guide/browser-extension' },
          ]
        }
      ],
      '/tutorial/': [
        {
          text: '入门：先建立全局观',
          items: [
            { text: '1. Agent 是什么', link: '/tutorial/overview' },
            { text: '2. Kernel 装配与观察-行动循环', link: '/tutorial/kernel-and-loop' },
          ]
        },
        {
          text: '模型层：让模型可被调用',
          items: [
            { text: '3. LLM History 与会话存储', link: '/tutorial/llm-history' },
            { text: '4. LLM 协议适配层', link: '/tutorial/llm-protocol' },
            { text: '5. 上下文压缩', link: '/tutorial/context-compaction' },
          ]
        },
        {
          text: '行动层：让模型真正做事',
          items: [
            { text: '6. 工具调用', link: '/tutorial/tools' },
            { text: '7. 权限系统', link: '/tutorial/permission' },
          ]
        },
        {
          text: '记忆层：让经验可以累积',
          items: [
            { text: '8. 工作记忆', link: '/tutorial/working-memory' },
            { text: '9. 长期记忆', link: '/tutorial/long-term-memory' },
            { text: '10. 技能库', link: '/tutorial/skills' },
          ]
        },
        {
          text: '进阶：架构与扩展',
          items: [
            { text: '11. 子 Agent、网关与桌面端', link: '/tutorial/architecture-extras' },
            { text: '12. 动手扩展 Chrysalis', link: '/tutorial/extending' },
          ]
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/hsdaoqi/Chrysalis-Agent' }
    ],

    docFooter: {
      prev: '上一章',
      next: '下一章',
    },

    outline: {
      level: [2, 3],
      label: '本页目录',
    },

    footer: {
      message: '基于 MIT 许可发布',
      copyright: 'Copyright © 2026 韩顺'
    }
  }
}))
