import type { AttachmentSummary, SessionSummary, RuntimeSnapshot, WorkingTodoSnapshot, WorkingTodoItem } from '../types'

const BTW_COMMAND_RE = /^\/btw(?:\s+|$)/i

export function hasTodoSnapshot(todo: WorkingTodoSnapshot): boolean {
  return Boolean(todo.goal || (todo.todos && todo.todos.length > 0))
}
export function todoItemStatusClass(status: string): string {
  const normalized = status.trim().toLowerCase().replace(/[\s-]+/g, '_')
  if (['completed', 'done'].includes(normalized)) {
    return 'done'
  }
  if (normalized === 'blocked') {
    return 'blocked'
  }
  if (normalized === 'active' || normalized === 'in_progress') {
    return 'active'
  }
  return 'pending'
}
export function todoItemActive(item: WorkingTodoItem, activeId: string): boolean {
  return item.id === activeId
}
export function composeDisplayTask(task: string, attachments: AttachmentSummary[]): string {
  const cleanTask = task.trim()
  if (attachments.length === 0) {
    return cleanTask
  }
  const lines = [cleanTask || 'Task with attachments', '', 'Attachments:']
  attachments.forEach((attachment) => {
    lines.push(`- ${attachment.name} (${attachment.kind})`)
  })
  return lines.join('\n').trim()
}
export function filterSessions(sessions: SessionSummary[], query: string): SessionSummary[] {
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
  if (terms.length === 0) {
    return sessions
  }
  return sessions.filter((session) => {
    const haystack = [session.id, session.title, session.model, session.updated_at].join(' ').toLowerCase()
    return terms.every((term) => haystack.includes(term))
  })
}
export function parseBtwCommand(text: string): string | null {
  const trimmed = text.trim()
  if (!BTW_COMMAND_RE.test(trimmed)) {
    return null
  }
  return trimmed.replace(BTW_COMMAND_RE, '').trim()
}
export function roleLabel(role: string): string {
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
export function statusLabel(status?: string): string {
  if (!status) {
    return ''
  }
  if (status === 'running') {
    return 'running'
  }
  if (status === 'done') {
    return 'done'
  }
  if (status === 'error') {
    return '失败'
  }
  if (status === 'collapsed') {
    return 'collapsed'
  }
  if (status === 'note') {
    return '备注'
  }
  return status
}
export function stripAnsi(value: string): string {
  return String(value || '').replace(/\u001b\[[0-9;]*m/g, '').replace(/\u001b\][^\u0007]*\u0007/g, '')
}
export function mergeLiveContextUsage(
  context: RuntimeSnapshot['context'] | null | undefined,
  visibleMessageCount: number,
): RuntimeSnapshot['context'] | null {
  if (!context) {
    return null
  }
  const base = context || {}
  const baseChars = Number(base.chars || 0)
  return {
    ...base,
    chars: baseChars,
    messages: visibleMessageCount,
  }
}
