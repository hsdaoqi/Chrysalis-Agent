import type { CanonicalBlock, CanonicalMessage, ViewMessage } from './types'

interface PendingToolCard {
  ids: Set<string>
  resolvedIds: Set<string>
  hasError: boolean
  toolNames: Set<string>
}

interface ToolBinding {
  cardIndex: number
  toolName: string
  toolUseId: string
}

interface SplitText {
  summary: string
  body: string
}

type MutableViewMessage = ViewMessage

const INTERNAL_PROMPT_KINDS = new Set([
  'internal',
  'internal_prompt',
  'review_prompt',
  'repair_prompt',
  'continue_prompt',
  'tool_followup',
])

const INTERNAL_ASSISTANT_KINDS = new Set([
  'internal',
  'internal_candidate',
  'review_candidate',
  'repair_candidate',
])

const INTERNAL_PROMPT_PREFIXES = [
  'Self-check failed.',
]

const ORPHANED_TOOL_RESULT_PREFIX = '[orphaned tool result converted to text]'

export function normalizeHistory(history: CanonicalMessage[]): ViewMessage[] {
  const messages: MutableViewMessage[] = []
  const pendingTools = new Map<number, PendingToolCard>()
  const toolBindings = new Map<string, ToolBinding>()

  let currentTurn = 0
  let turnCounter = 0
  let currentToolTurn = 0

  history.forEach((message, index) => {
    const role = normalizeRole(message.role)
    const blocks = Array.isArray(message.blocks) ? message.blocks.filter((block): block is CanonicalBlock => isBlock(block)) : []
    const rawText = readText(message.content)
    const displayText = messageDisplayText(message)

    if (role === 'user') {
      if (isOrphanedToolResultText(displayText || rawText || joinBlockText(blocks))) {
        const toolText = normalizeToolResultText(displayText || rawText || joinBlockText(blocks))
        if (toolText) {
          messages.push({
            id: `orphaned-tool-result-${messages.length}`,
            kind: 'tool',
            role: 'assistant',
            title: 'Tool Result',
            body: compactText(stripOrphanedToolResultPrefix(toolText), 180),
            meta: 'done',
            status: 'done',
            details: [toolText],
          })
        }
        return
      }

      const toolResultBlocks = blocks.filter((block) => String(block.type || '').toLowerCase() === 'tool_result')
      if (toolResultBlocks.length > 0) {
        toolResultBlocks.forEach((block) => {
          attachToolResult(messages, pendingTools, toolBindings, block)
        })

        const visibleText = normalizeUserText(rawText)
        if (visibleText) {
          appendTrailingUserNote(messages, currentTurn, visibleText)
        }
        return
      }

      if (isInternalPromptMessage(message, blocks, rawText)) {
        return
      }

      const userText = normalizeUserText(displayText || rawText || joinBlockText(blocks))
      if (!userText) {
        return
      }

      currentTurn = ++turnCounter
      currentToolTurn = 0
      messages.push({
        id: `turn-${currentTurn}-user-${messages.length}`,
        kind: 'user',
        role: 'user',
        title: 'User',
        body: userText,
        turn: currentTurn,
      })
      return
    }

    if (role === 'assistant') {
      if (isInternalAssistantCandidate(history, index)) {
        return
      }

      const toolUseBlocks = blocks.filter((block) => String(block.type || '').toLowerCase() === 'tool_use')
      const thinkingBlocks = blocks.filter((block) => String(block.type || '').toLowerCase() === 'thinking')
      const { summary, body } = extractAssistantText(blocks, rawText)

      if (toolUseBlocks.length > 0) {
        if (currentTurn === 0) {
          currentTurn = ++turnCounter
        }

        const firstTool = toolUseBlocks[0]
        const firstToolName = String(firstTool?.name || firstTool?.id || 'tool')
        const firstArgs = compactText(stringifyValue(firstTool?.arguments), 64)
        const toolTurn = ++currentToolTurn
        const toolCardIndex = messages.length
        const toolCard: MutableViewMessage = {
          id: `turn-${currentTurn}-tool-${toolTurn}-${toolCardIndex}`,
          kind: 'tool',
          role: 'assistant',
          title: `Turn ${toolTurn} - running ${firstToolName}${firstArgs ? `(${firstArgs})` : ''} ...`,
          body: summary || compactText(body, 180) || `Calling ${toolUseBlocks.length} tools`,
          turn: toolTurn,
          meta: 'running',
          status: 'running',
          details: [],
        }

        const details: string[] = []
        if (summary) {
          details.push(`Thought:\n${summary}`)
        }
        if (body && body !== summary) {
          details.push(`Text:\n${body}`)
        }
        const thinking = compactText(joinThoughts(thinkingBlocks), 800)
        if (thinking) {
          details.push(`Thought:\n${thinking}`)
        }

        const pending: PendingToolCard = {
          ids: new Set(),
          resolvedIds: new Set(),
          hasError: false,
          toolNames: new Set(),
        }

        toolUseBlocks.forEach((block, index) => {
          const toolName = String(block.name || block.id || `tool-${index + 1}`)
          const toolUseId = toolUseIdFor(block, index, toolCardIndex)
          const args = compactText(stringifyValue(block.arguments), 1500)
          details.push(`Tool ${index + 1}: ${toolName}\nArgs:\n${args || '(empty)'}`)
          pending.ids.add(toolUseId)
          pending.toolNames.add(toolName.toLowerCase())
          toolBindings.set(toolUseId, {
            cardIndex: toolCardIndex,
            toolName,
            toolUseId,
          })
        })

        const isWaitingForUser = pending.toolNames.size > 0 && Array.from(pending.toolNames).every((name) => name === 'ask_user')
        const question = isWaitingForUser ? readToolQuestion(toolUseBlocks[0]) : ''
        if (isWaitingForUser) {
          toolCard.title = `Turn ${toolTurn} - waiting ask_user`
          toolCard.body = question || toolCard.body
          toolCard.meta = 'pending_user'
          toolCard.status = 'done'
          if (question) {
            details.push(`Question:\n${question}`)
          }
        }

        toolCard.details = details
        pendingTools.set(toolCardIndex, pending)
        messages.push(toolCard)
        return
      }

      const finalText = normalizeAssistantText(body)
      if (finalText) {
        if (currentTurn === 0) {
          currentTurn = ++turnCounter
        }
        const details: string[] = []
        const thinking = compactText(joinThoughts(thinkingBlocks), 800)
        if (thinking) {
          details.push(`Thought:
${thinking}`)
        }
        messages.push({
          id: `turn-${currentTurn}-assistant-${messages.length}`,
          kind: 'assistant',
          role: 'assistant',
          title: 'Assistant',
          body: finalText,
          turn: currentTurn,
          details,
        })
        return
      }

      const thinking = compactText(joinThoughts(thinkingBlocks), 800)
      if (thinking) {
        if (currentTurn === 0) {
          currentTurn = ++turnCounter
        }
        messages.push({
          id: `turn-${currentTurn}-thinking-${messages.length}`,
          kind: 'thinking',
          role: 'assistant',
          title: 'Thinking',
          body: thinking,
          turn: currentTurn,
          meta: 'collapsed',
          details: [`Full thought:
${joinThoughts(thinkingBlocks)}`],
        })
      }
      return
    }

    const systemText = normalizeSystemText(rawText || joinBlockText(blocks))
    if (!systemText) {
      return
    }

    messages.push({
      id: `system-${messages.length}`,
      kind: 'system',
      role,
      title: titleForRole(role),
      body: systemText,
    })
  })

  return messages
}

function attachToolResult(
  messages: MutableViewMessage[],
  pendingTools: Map<number, PendingToolCard>,
  toolBindings: Map<string, ToolBinding>,
  block: CanonicalBlock,
): void {
  const toolUseId = String(block.tool_use_id || '').trim()
  const binding = toolUseId ? toolBindings.get(toolUseId) : undefined
  const content = normalizeToolResultText(readText(block.content) || readText(block.text) || readText(block.arguments))
  const name = binding?.toolName || String(block.name || block.tool_use_id || 'tool_result')
  const detail = block.is_error
    ? `Error · ${name}\n${content || 'Tool execution failed'}`
    : `Result · ${name}\n${content || 'Tool returned no content'}`

  if (!binding) {
    messages.push({
      id: `tool-result-${messages.length}`,
      kind: block.is_error ? 'error' : 'tool',
      role: 'assistant',
      title: `Turn ? - ${block.is_error ? 'error' : 'ok'} ${name}`,
      body: compactText(content || 'Tool result', 180),
      meta: block.is_error ? 'error' : 'done',
      status: block.is_error ? 'error' : 'done',
      details: [detail],
    })
    return
  }

  const card = messages[binding.cardIndex]
  if (!card) {
    return
  }

  const existingDetails = Array.isArray(card.details) ? [...card.details] : []
  existingDetails.push(detail)
  const pending = pendingTools.get(binding.cardIndex)
  if (pending) {
    pending.resolvedIds.add(binding.toolUseId)
    if (block.is_error) {
      pending.hasError = true
    }
    const resolved = pending.resolvedIds.size >= pending.ids.size && pending.ids.size > 0
    card.status = pending.hasError || block.is_error ? 'error' : resolved ? 'done' : 'running'
    card.meta = card.status
  } else {
    card.status = block.is_error ? 'error' : 'done'
    card.meta = card.status
  }

  card.body = card.body || compactText(content || 'Tool result', 180)
  card.details = existingDetails
  if (block.is_error) {
    card.kind = 'error'
  }
  const toolStatus = card.status === 'error' ? 'error' : 'ok'
  const summary = compactText(content || (block.is_error ? 'error' : 'done'), 48) || 'done'
  card.title = `Turn ${card.turn || 0} - ${toolStatus} ${name} - ${summary}`
  card.body = compactText(content || card.body || 'Tool result', 180)
}

function readToolQuestion(block?: CanonicalBlock): string {
  if (!block) {
    return ''
  }
  const raw = block.arguments
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return compactText(readText((parsed as Record<string, unknown>).question), 180)
      }
    } catch {
      return ''
    }
  }
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return compactText(readText((raw as Record<string, unknown>).question), 180)
  }
  return ''
}

function appendTrailingUserNote(messages: MutableViewMessage[], turn: number, text: string): void {
  if (!text) {
    return
  }
  const last = messages[messages.length - 1]
  if (last && last.turn === turn && last.kind === 'user') {
    last.body = `${last.body}\n\n${text}`.trim()
    return
  }
  messages.push({
    id: `turn-${turn}-user-note-${messages.length}`,
    kind: 'info',
    role: 'user',
    title: 'Note',
    body: text,
    turn: turn > 0 ? turn : undefined,
    meta: 'note',
  })
}

function extractAssistantText(blocks: CanonicalBlock[], rawText: string): SplitText {
  const source = joinTextBlocks(blocks) || rawText
  const normalized = normalizeLineBreaks(source)
  if (!normalized) {
    return { summary: '', body: '' }
  }

  const summaryParts: string[] = []
  const bodyParts: string[] = []
  let cursor = 0
  const regex = /<summary>([\s\S]*?)<\/summary>/gi

  for (let match = regex.exec(normalized); match; match = regex.exec(normalized)) {
    const before = normalized.slice(cursor, match.index).trim()
    if (before) {
      bodyParts.push(before)
    }
    const summary = normalizeLineBreaks(match[1]).trim()
    if (summary) {
      summaryParts.push(summary)
    }
    cursor = match.index + match[0].length
  }

  const tail = normalized.slice(cursor).trim()
  if (tail) {
    bodyParts.push(tail)
  }

  let body = bodyParts.join('\n\n').trim()
  body = body.replace(/<\/?summary>/gi, '').trim()
  body = normalizeLineBreaks(body)

  return {
    summary: summaryParts.join('\n\n').trim(),
    body,
  }
}

function joinTextBlocks(blocks: CanonicalBlock[]): string {
  return blocks
    .filter((block) => String(block.type || '').toLowerCase() === 'text')
    .map((block) => readText(block.text))
    .filter(Boolean)
    .join('\n\n')
}

function joinThoughts(blocks: CanonicalBlock[]): string {
  return blocks
    .filter((block) => String(block.type || '').toLowerCase() === 'thinking')
    .map((block) => readText(block.text))
    .filter(Boolean)
    .join('\n\n')
}

function joinBlockText(blocks: CanonicalBlock[]): string {
  return blocks
    .map((block) => readText(block.text || block.content))
    .filter(Boolean)
    .join('\n\n')
}

function normalizeAssistantText(text: string): string {
  return normalizeVisibleText(text)
}

function normalizeUserText(text: string): string {
  return normalizeVisibleText(stripSessionContext(text))
}

function normalizeSystemText(text: string): string {
  return normalizeVisibleText(stripSummaryMarkup(text))
}

function normalizeToolResultText(text: string): string {
  return normalizeVisibleText(text)
}

function normalizeVisibleText(text: string): string {
  return stripSummaryMarkup(normalizeLineBreaks(text)).trim()
}

function stripSessionContext(text: string): string {
  const normalized = normalizeLineBreaks(text)
  if (!normalized) {
    return ''
  }

  const markers = [
    '## Runtime Context',
    '# Runtime Context',
    '[Relevant Skills]',
    '[Relevant L2/L3]',
    '[Runtime Continuation]',
    '### [SESSION CONTEXT]',
    '# [SESSION CONTEXT]',
    '[SESSION CONTEXT]',
    '<recent_turns>',
    '</recent_turns>',
    '## 当前短期工作记忆',
    '### 当前短期工作记忆',
  ]

  let cutIndex = -1
  for (const marker of markers) {
    const index = normalized.indexOf(marker)
    if (index >= 0 && (cutIndex < 0 || index < cutIndex)) {
      cutIndex = index
    }
  }

  return cutIndex >= 0 ? normalized.slice(0, cutIndex).trim() : normalized.trim()
}

function stripSummaryMarkup(text: string): string {
  return normalizeLineBreaks(text)
    .replace(/<summary>[\s\S]*?<\/summary>/gi, (match) => {
      const inner = match.replace(/<\/?summary>/gi, '')
      return inner && inner.includes('<') ? inner : ''
    })
    .replace(/<\/?summary>/gi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function compactText(text: string, limit: number): string {
  const normalized = normalizeLineBreaks(text)
    .replace(/\s+/g, ' ')
    .trim()
  if (normalized.length <= limit) {
    return normalized
  }
  return `${normalized.slice(0, Math.max(0, limit - 3)).trimEnd()}...`
}

function toolUseIdFor(block: CanonicalBlock, index: number, cardIndex: number): string {
  const raw = String(block.id || block.tool_use_id || '').trim()
  if (raw) {
    return raw
  }
  return `tool-${cardIndex}-${index}`
}

function titleForRole(role: string): string {
  if (role === 'assistant') {
    return 'Assistant'
  }
  if (role === 'user') {
    return 'User'
  }
  if (role === 'system') {
    return 'System'
  }
  return role || 'Message'
}

function normalizeRole(role: unknown): string {
  return String(role || 'system').trim().toLowerCase()
}

function isInternalAssistantCandidate(history: CanonicalMessage[], index: number): boolean {
  const message = history[index]
  if (!message || normalizeRole(message.role) !== 'assistant') {
    return false
  }
  if (INTERNAL_ASSISTANT_KINDS.has(messageUiKind(message))) {
    return true
  }
  const next = history[index + 1]
  if (!next || normalizeRole(next.role) !== 'user') {
    return false
  }
  const blocks = Array.isArray(next.blocks) ? next.blocks.filter((block): block is CanonicalBlock => isBlock(block)) : []
  return isInternalPromptMessage(next, blocks, readText(next.content))
}

function isInternalPromptMessage(message: CanonicalMessage, blocks: CanonicalBlock[], rawText: string): boolean {
  if (normalizeRole(message.role) !== 'user') {
    return false
  }
  if (INTERNAL_PROMPT_KINDS.has(messageUiKind(message))) {
    return true
  }
  return isInternalPromptText(rawText || joinTextBlocks(blocks) || joinBlockText(blocks))
}

function isInternalPromptText(text: string): boolean {
  const normalized = normalizeLineBreaks(text).trim()
  if (!normalized) {
    return false
  }
  const firstLine = normalized.split('\n')[0].trim()
  if (INTERNAL_PROMPT_PREFIXES.some((prefix) => firstLine.startsWith(prefix))) {
    return true
  }
  if (firstLine.startsWith('JSON ') && normalized.includes('final JSON')) {
    return true
  }
  return normalized.includes('Review turn:') && normalized.includes('Current answer:')
}

function isOrphanedToolResultText(text: string): boolean {
  return normalizeLineBreaks(text).trim().startsWith(ORPHANED_TOOL_RESULT_PREFIX)
}

function stripOrphanedToolResultPrefix(text: string): string {
  const normalized = normalizeLineBreaks(text).trim()
  if (!normalized.startsWith(ORPHANED_TOOL_RESULT_PREFIX)) {
    return normalized
  }
  return normalized.slice(ORPHANED_TOOL_RESULT_PREFIX.length).trim()
}

function messageDisplayText(message: CanonicalMessage): string {
  const meta = message.meta && typeof message.meta === 'object' ? message.meta as Record<string, unknown> : {}
  const ui = meta.ui && typeof meta.ui === 'object' ? meta.ui as Record<string, unknown> : {}
  return readText(ui.display_text || meta.display_text)
}

function messageUiKind(message: CanonicalMessage): string {
  const meta = message.meta && typeof message.meta === 'object' ? message.meta as Record<string, unknown> : {}
  const ui = meta.ui && typeof meta.ui === 'object' ? meta.ui as Record<string, unknown> : {}
  const uiKind = String(ui.kind || '').trim().toLowerCase()
  if (uiKind) {
    return uiKind
  }
  return String(meta.kind || '').trim().toLowerCase()
}

function readText(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (value == null) {
    return ''
  }
  if (Array.isArray(value)) {
    return value.map(readText).filter(Boolean).join('\n')
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function stringifyValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (value == null) {
    return ''
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function isBlock(block: unknown): block is CanonicalBlock {
  return Boolean(block && typeof block === 'object')
}

function normalizeLineBreaks(text: string): string {
  return String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
}
