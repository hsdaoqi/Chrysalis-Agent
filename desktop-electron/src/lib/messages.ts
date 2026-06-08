import type { RuntimeSnapshot, SessionSummary, ViewMessage } from '../types'

export const PROCESS_MESSAGE_KINDS = new Set<ViewMessage['kind']>([
  'tool',
  'thinking',
  'info',
  'status',
  'usage',
  'warning',
  'system',
])
export const PENDING_TASK_ID = 'pending'
export const LIVE_STREAM_BUFFER_LIMIT = 12_000
export const LIVE_STREAM_PREVIEW_LIMIT = 2_200
export const ORPHANED_TOOL_RESULT_PREFIX = '[orphaned tool result converted to text]'
import type { LiveSessionState } from '../app-types'
import { compactText, formatFileChangeBody, formatFileChangeSummary, fileChangeKey } from './format'

export function isTurnProcessMessage(message: ViewMessage): boolean {
  if (PROCESS_MESSAGE_KINDS.has(message.kind)) {
    return true
  }
  return message.kind === 'error' && message.title.trim().toLowerCase().startsWith('turn ')
}
export function filterVisibleChatMessages(messages: ViewMessage[], showTurnMessages: boolean): ViewMessage[] {
  const visible = messages.filter((message) => {
    if (!message.body.trim()) {
      return false
    }
    if (message.meta === 'pending_user') {
      return false
    }
    if (isTurnProcessMessage(message)) {
      return showTurnMessages
    }
    if (message.kind === 'diff') {
      return true
    }
    if (message.kind === 'user' || message.kind === 'assistant') {
      return true
    }
    if (message.kind === 'error') {
      return true
    }
    return false
  })
  return collapseConsecutiveDuplicateUsers(visible)
}
export function mergeHistoryAndLiveMessages(historyMessages: ViewMessage[], liveMessages: ViewMessage[]): ViewMessage[] {
  const historyUserIndexes = new Map<string, number>()
  const historyUserBodyIndexes = new Map<string, number>()
  const historyUserCount = countViewConversationTurns(historyMessages)
  historyMessages.forEach((message, index) => {
    if (message.kind !== 'user' || !message.body.trim()) {
      return
    }
    const bodyKey = normalizeViewMessageBody(message.body)
    historyUserIndexes.set(viewMessageDedupKey(message), historyTurnEndIndex(historyMessages, index))
    historyUserBodyIndexes.set(bodyKey, historyTurnEndIndex(historyMessages, index))
  })

  const taskAnchors = new Map<string, number>()
  liveMessages.forEach((message) => {
    if (message.kind !== 'user' || !message.body.trim()) {
      return
    }
    const taskId = normalizeLiveTaskId(message.taskId)
    const anchorIndex = historyUserIndexes.get(viewMessageDedupKey(message))
      ?? historyAnchorForLiveUserBody(message, historyUserBodyIndexes, historyUserCount)
    if (taskId && anchorIndex !== undefined) {
      taskAnchors.set(taskId, anchorIndex)
    }
  })

  const insertions = new Map<number, ViewMessage[]>()
  const appended: ViewMessage[] = []
  liveMessages.forEach((message) => {
    const duplicateUser = message.kind === 'user' && (
      historyUserIndexes.has(viewMessageDedupKey(message))
      || historyAnchorForLiveUserBody(message, historyUserBodyIndexes, historyUserCount) !== undefined
    )
    if (duplicateUser) {
      return
    }
    const taskId = normalizeLiveTaskId(message.taskId)
    const anchorIndex = taskId ? taskAnchors.get(taskId) : undefined
    if (anchorIndex !== undefined) {
      const items = insertions.get(anchorIndex) || []
      items.push(message)
      insertions.set(anchorIndex, items)
      return
    }
    appended.push(message)
  })

  const merged: ViewMessage[] = []
  historyMessages.forEach((message, index) => {
    merged.push(message)
    const anchored = insertions.get(index)
    if (anchored) {
      merged.push(...anchored)
    }
  })
  merged.push(...appended)
  return merged
}
function collapseConsecutiveDuplicateUsers(messages: ViewMessage[]): ViewMessage[] {
  const visible: ViewMessage[] = []
  for (const message of messages) {
    const previous = visible[visible.length - 1]
    if (
      previous?.kind === 'user'
      && message.kind === 'user'
      && normalizeViewMessageBody(previous.body) === normalizeViewMessageBody(message.body)
    ) {
      continue
    }
    visible.push(message)
  }
  return visible
}
function historyAnchorForLiveUserBody(
  message: ViewMessage,
  historyUserBodyIndexes: Map<string, number>,
  historyUserCount: number,
): number | undefined {
  const bodyKey = normalizeViewMessageBody(message.body)
  if (!bodyKey || !historyUserBodyIndexes.has(bodyKey)) {
    return undefined
  }
  if (message.turn && message.turn > historyUserCount) {
    return undefined
  }
  return historyUserBodyIndexes.get(bodyKey)
}
export function historyTurnEndIndex(messages: ViewMessage[], startIndex: number): number {
  let endIndex = startIndex
  for (let index = startIndex + 1; index < messages.length; index += 1) {
    if (messages[index].kind === 'user') {
      break
    }
    endIndex = index
  }
  return endIndex
}
export function liveMessagesForCurrentTask(live: LiveSessionState): ViewMessage[] {
  const taskId = normalizeLiveTaskId(live.taskId)
  if (!taskId) {
    return live.messages.filter((message) => (
      message.meta !== 'pending_user' && (live.started || !isTurnProcessMessage(message))
    ))
  }
  const diffTaskIds = liveDiffTaskIds(live.messages)
  const hasTaggedCurrentMessages = live.messages.some((message) => {
    const messageTaskId = normalizeLiveTaskId(message.taskId)
    return Boolean(messageTaskId && liveMessageBelongsToTask(message, taskId))
  })
  return live.messages.filter((message) => {
    if (message.meta === 'pending_user') {
      return false
    }
    const messageTaskId = normalizeLiveTaskId(message.taskId)
    if (isLiveFileChangeAnchorMessage(message, diffTaskIds)) {
      return true
    }
    if (!messageTaskId) {
      return !hasTaggedCurrentMessages
    }
    return liveMessageBelongsToTask(message, taskId)
  })
}
export function liveDiffTaskIds(messages: ViewMessage[]): Set<string> {
  const taskIds = new Set<string>()
  messages.forEach((message) => {
    if (message.kind !== 'diff') {
      return
    }
    const taskId = normalizeLiveTaskId(message.taskId)
    if (taskId) {
      taskIds.add(taskId)
    }
  })
  return taskIds
}
export function isLiveFileChangeAnchorMessage(message: ViewMessage, diffTaskIds: Set<string>): boolean {
  const taskId = normalizeLiveTaskId(message.taskId)
  if (!taskId) {
    return false
  }
  if (message.kind === 'diff') {
    return true
  }
  return message.kind === 'user' && diffTaskIds.has(taskId)
}
export function liveFileChangeAnchorMessages(messages: ViewMessage[]): ViewMessage[] {
  const diffTaskIds = liveDiffTaskIds(messages)
  return messages.filter((message) => isLiveFileChangeAnchorMessage(message, diffTaskIds))
}
export function tagPendingLiveMessages(messages: ViewMessage[], taskId: string): ViewMessage[] {
  const diffTaskIds = liveDiffTaskIds(messages)
  return messages.map((message) => {
    if (isLiveFileChangeAnchorMessage(message, diffTaskIds)) {
      return message
    }
    const currentTaskId = normalizeLiveTaskId(message.taskId)
    if (currentTaskId && currentTaskId !== PENDING_TASK_ID) {
      return message
    }
    return tagLiveMessageTask(message, taskId)
  })
}
export function liveMessageBelongsToTask(message: ViewMessage, taskId: string): boolean {
  const currentTaskId = normalizeLiveTaskId(taskId)
  const messageTaskId = normalizeLiveTaskId(message.taskId)
  if (!currentTaskId) {
    return !messageTaskId
  }
  if (!messageTaskId) {
    return false
  }
  if (currentTaskId === PENDING_TASK_ID) {
    return messageTaskId === PENDING_TASK_ID
  }
  return messageTaskId === currentTaskId || messageTaskId === PENDING_TASK_ID
}
export function liveTaskEventApplies(current: LiveSessionState, taskId: string): boolean {
  const incomingTaskId = normalizeLiveTaskId(taskId)
  const currentTaskId = normalizeLiveTaskId(current.taskId)
  if (!incomingTaskId) {
    return true
  }
  if (!currentTaskId) {
    return current.started
  }
  return currentTaskId === PENDING_TASK_ID || currentTaskId === incomingTaskId
}
export function liveTaskIdForEvent(current: LiveSessionState, taskId: string): string {
  return normalizeLiveTaskId(taskId) || normalizeLiveTaskId(current.taskId)
}
export function tagLiveMessageTask(message: ViewMessage, taskId: string): ViewMessage {
  const normalized = normalizeLiveTaskId(taskId)
  if (!normalized || normalizeLiveTaskId(message.taskId) === normalized) {
    return message
  }
  return {
    ...message,
    taskId: normalized,
  }
}
export function normalizeLiveTaskId(value: unknown): string {
  return String(value || '').trim()
}
export function viewMessageDedupKey(message: ViewMessage): string {
  return [
    message.kind,
    message.turn ?? '',
    normalizeViewMessageBody(message.body),
  ].join('\u0000')
}
export function normalizeViewMessageBody(body: string): string {
  return body.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim()
}
export function viewMessagesEqual(left: ViewMessage[], right: ViewMessage[]): boolean {
  if (left.length !== right.length) {
    return false
  }
  return left.every((leftMessage, index) => {
    const rightMessage = right[index]
    return Boolean(rightMessage)
      && leftMessage.id === rightMessage.id
      && leftMessage.kind === rightMessage.kind
      && leftMessage.title === rightMessage.title
      && leftMessage.body === rightMessage.body
      && leftMessage.turn === rightMessage.turn
      && leftMessage.taskId === rightMessage.taskId
      && leftMessage.meta === rightMessage.meta
      && leftMessage.status === rightMessage.status
      && leftMessage.streaming === rightMessage.streaming
      && JSON.stringify(leftMessage.details || []) === JSON.stringify(rightMessage.details || [])
  })
}
export function countViewConversationTurns(messages: ViewMessage[]): number {
  return (messages || []).filter((message) => message.kind === 'user' && message.body.trim()).length
}
export function countMissingLiveConversationTurns(
  activeHistory: RuntimeSnapshot['history'],
  liveMessages: ViewMessage[],
): number {
  const historyTurns = countConversationTurns(activeHistory || [])
  return (liveMessages || []).filter((message) => {
    if (message.kind !== 'user' || !message.body.trim()) {
      return false
    }
    return !message.turn || message.turn > historyTurns
  }).length
}
export function mergeLiveSessionSummaries(
  sessions: SessionSummary[],
  liveSessions: Record<string, LiveSessionState>,
  activeSessionId: string,
  activeHistory: RuntimeSnapshot['history'],
): SessionSummary[] {
  const activeTurns = countConversationTurns(activeHistory || [])
  const activeLiveMissingTurns = countMissingLiveConversationTurns(
    activeHistory || [],
    activeSessionId ? liveSessions[activeSessionId]?.messages || [] : [],
  )
  return sessions.map((session) => {
    const live = liveSessions[session.id]
    const liveTurns = session.id === activeSessionId
      ? activeLiveMissingTurns
      : countViewConversationTurns(live?.messages || [])
    const turns = session.id === activeSessionId
      ? Math.max(Number(session.turns || 0), activeTurns + liveTurns)
      : Math.max(Number(session.turns || 0), liveTurns)
    if (!live && turns === session.turns) {
      return session
    }
    return {
      ...session,
      turns,
      busy: Boolean(session.busy || live?.taskId),
      task_id: live?.taskId || session.task_id,
    }
  })
}
export function trimLiveStreamBuffer(text: string): string {
  if (text.length <= LIVE_STREAM_BUFFER_LIMIT) {
    return text
  }
  return text.slice(-LIVE_STREAM_BUFFER_LIMIT)
}
export function streamPreviewText(value: string): string {
  const text = stripSummaryMarkup(value)
  if (text.length <= LIVE_STREAM_PREVIEW_LIMIT) {
    return text.trim()
  }
  return `...\n${text.slice(-LIVE_STREAM_PREVIEW_LIMIT).trimStart()}`
}
export function createMessageId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
export function stripSummaryMarkup(value: string): string {
  const normalized = String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  return normalized
    .replace(/<summary>[\s\S]*?(<\/summary>|$)/gi, '')
    .replace(/<\/?summary>/gi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
export function countConversationTurns(history: RuntimeSnapshot['history']): number {
  return (history || []).filter((message) => {
    if (String(message.role || '').toLowerCase() !== 'user') {
      return false
    }
    const blocks = Array.isArray(message.blocks) ? message.blocks : []
    if (blocks.some((block) => String(block.type || '').toLowerCase() === 'tool_result')) {
      return false
    }
    const text = readCanonicalText(message).trim()
    if (isOrphanedToolResultText(text)) {
      return false
    }
    return Boolean(text)
  }).length
}
export function countViewChatMessages(messages: ViewMessage[]): number {
  return (messages || []).filter((message) => {
    if (!message.body.trim()) {
      return false
    }
    if (message.kind === 'user') {
      return true
    }
    return message.kind === 'assistant' && message.meta !== 'pending_user'
  }).length
}
export function readCanonicalText(message: RuntimeSnapshot['history'][number]): string {
  if (typeof message.content === 'string') {
    return message.content
  }
  const blocks = Array.isArray(message.blocks) ? message.blocks : []
  return blocks
    .map((block) => {
      if (typeof block.text === 'string') {
        return block.text
      }
      if (typeof block.content === 'string') {
        return block.content
      }
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

export function isOrphanedToolResultText(text: string): boolean {
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .trim()
    .startsWith(ORPHANED_TOOL_RESULT_PREFIX)
}
