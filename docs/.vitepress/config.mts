import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Chrysalis',
  description: '一个本地通用 Agent 框架',
  lang: 'zh-CN',
  base: '/Chrysalis-Agent/',

  themeConfig: {
    logo: '/logo.png',

    nav: [
      { text: '首页', link: '/' },
      { text: '使用教程', link: '/guide/installation' },
      { text: 'Agent 原理', link: '/tutorial/overview' },
    ],

    sidebar: {
      '/guide/': [
        {
          text: '使用教程',
          items: [
            { text: '安装指南', link: '/guide/installation' },
            { text: '配置说明', link: '/guide/configuration' },
            { text: '快速开始', link: '/guide/quickstart' },
            { text: 'TUI 使用', link: '/guide/tui' },
            { text: '桌面端', link: '/guide/desktop' },
          ]
        }
      ],
      '/tutorial/': [
        {
          text: 'Agent 原理教程',
          items: [
            { text: '概述', link: '/tutorial/overview' },
            { text: 'LLM History', link: '/tutorial/llm-history' },
            { text: '工具调用', link: '/tutorial/tools' },
            { text: '工作记忆', link: '/tutorial/working-memory' },
            { text: '长期记忆', link: '/tutorial/long-term-memory' },
            { text: '技能库', link: '/tutorial/skills' },
            { text: '上下文压缩', link: '/tutorial/context-compaction' },
          ]
        }
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/hsdaoqi/Chrysalis-Agent' }
    ],

    footer: {
      message: '基于 MIT 许可发布',
      copyright: 'Copyright © 2026 韩顺'
    }
  }
})
