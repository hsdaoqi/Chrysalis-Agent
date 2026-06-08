import type { CacheUsageSummary, ContextAssembly, TraceEventNode, ViewMessage, WorkingSnapshot, WorkingTodoSnapshot } from '../types'
import type { TraceSummary, TraceDetail, TraceTaskGroup, CacheUsageByTask, CacheUsageByTurn } from '../app-types'
import { compactText, formatCompactCount, formatCost, formatMs, formatTimestamp, formatTraceTime, isRecord, stringifyValue, toTraceNumber } from './format'
import { contextAssemblyLine, contextAssemblyDetailText } from './context'
import { createMessageId, normalizeLiveTaskId, PENDING_TASK_ID } from './messages'
import { permissionSummary } from './pending'

export function getTodoSnapshot(working: WorkingSnapshot): WorkingTodoSnapshot {
  if (working.todo) {
    return working.todo
  }
  return {
    goal: working.todo_goal || '',
    rounds_since_todo: working.rounds_since_todo,
    todo_reminder_interval: working.todo_reminder_interval,
    total_count: working.total_count,
    pending_count: working.pending_count,
    completed_count: working.completed_count,
    todos: working.todos || [],
    active_todo_id: working.active_todo_id || '',
    active_todo_title: working.active_todo_title || '',
  }
}
export function traceUsageTotal(usage: unknown): number {
  if (!isRecord(usage)) {
    return 0
  }
  const total = toTraceNumber(usage.total_tokens)
  if (total > 0) {
    return total
  }
  return toTraceNumber(usage.prompt_tokens) + toTraceNumber(usage.completion_tokens)
}
export function traceUsageLine(usage: unknown): string {
  if (!isRecord(usage)) {
    return ''
  }
  const prompt = toTraceNumber(usage.prompt_tokens)
  const completion = toTraceNumber(usage.completion_tokens)
  const total = traceUsageTotal(usage)
  const parts = [
    prompt > 0 ? `${formatCompactCount(prompt)} in` : '',
    completion > 0 ? `${formatCompactCount(completion)} out` : '',
    total > 0 ? `${formatCompactCount(total)} total` : '',
  ].filter(Boolean)
  return parts.join(' / ')
}
export function emptyCacheUsageSummary(): CacheUsageSummary {
  return {
    readTokens: 0,
    writeTokens: 0,
    promptTokens: 0,
    inputTokens: 0,
    totalTokens: 0,
    calls: 0,
    hitRate: 0,
  }
}
export function addCacheUsageSummary(left: CacheUsageSummary, right: CacheUsageSummary): CacheUsageSummary {
  const readTokens = left.readTokens + right.readTokens
  const writeTokens = left.writeTokens + right.writeTokens
  const promptTokens = left.promptTokens + right.promptTokens
  const inputTokens = left.inputTokens + right.inputTokens
  const totalTokens = left.totalTokens + right.totalTokens
  const calls = left.calls + right.calls
  return {
    readTokens,
    writeTokens,
    promptTokens,
    inputTokens,
    totalTokens,
    calls,
    hitRate: inputTokens > 0 ? Math.max(0, Math.min(1, readTokens / inputTokens)) : 0,
  }
}
export function traceNodeCacheUsage(node: TraceEventNode | null | undefined): CacheUsageSummary {
  if (!node || !isRecord(node.usage)) {
    return emptyCacheUsageSummary()
  }
  const usage = node.usage
  const readTokens = toTraceNumber(usage.cache_read_tokens)
  const writeTokens = toTraceNumber(usage.cache_creation_tokens)
  const promptTokens = toTraceNumber(usage.prompt_tokens)
  const completionTokens = toTraceNumber(usage.completion_tokens)
  const totalTokens = traceUsageTotal(usage)
  const providerText = [
    node.protocol,
    node.provider,
    node.model,
    node.model_id,
  ].map((value) => stringifyValue(value).toLowerCase()).join(' ')
  const cacheIsSeparateInput = providerText.includes('anthropic') || providerText.includes('claude')
  const inputTokens = cacheIsSeparateInput
    ? promptTokens + readTokens + writeTokens
    : Math.max(promptTokens, readTokens + writeTokens, totalTokens - completionTokens)
  return {
    readTokens,
    writeTokens,
    promptTokens,
    inputTokens,
    totalTokens,
    calls: 1,
    hitRate: inputTokens > 0 ? Math.max(0, Math.min(1, readTokens / inputTokens)) : 0,
  }
}
export function cacheUsageHasData(summary: CacheUsageSummary | null | undefined): summary is CacheUsageSummary {
  return Boolean(summary && (summary.readTokens > 0 || summary.writeTokens > 0 || summary.inputTokens > 0))
}
export function formatCacheHitRate(summary: CacheUsageSummary | null | undefined): string {
  if (!summary || summary.inputTokens <= 0) {
    return '-'
  }
  return `${Math.round(Math.max(0, Math.min(1, summary.hitRate)) * 100)}%`
}
export function traceCacheLine(summary: CacheUsageSummary | null | undefined): string {
  if (!cacheUsageHasData(summary)) {
    return ''
  }
  const parts = [
    summary.readTokens > 0 ? `${formatCompactCount(summary.readTokens)} cache read` : '',
    summary.writeTokens > 0 ? `${formatCompactCount(summary.writeTokens)} cache write` : '',
    summary.inputTokens > 0 ? `${formatCacheHitRate(summary)} hit` : '',
  ].filter(Boolean)
  return parts.join(' / ')
}
export function traceUsageAndCacheLine(node: TraceEventNode | null | undefined): string {
  if (!node) {
    return ''
  }
  return [
    traceUsageLine(node.usage),
    traceCacheLine(traceNodeCacheUsage(node)),
  ].filter(Boolean).join(' / ')
}
export function formatTurnCacheLabel(summary: CacheUsageSummary | null | undefined): string {
  if (!cacheUsageHasData(summary)) {
    return ''
  }
  return `缓存 ${formatCompactCount(summary.readTokens)} / ${formatCacheHitRate(summary)}`
}
export function formatCacheTitle(summary: CacheUsageSummary | null | undefined): string {
  if (!cacheUsageHasData(summary)) {
    return ''
  }
  return [
    `缓存命中率 ${formatCacheHitRate(summary)}`,
    `缓存读取 ${formatCompactCount(summary.readTokens)}`,
    summary.writeTokens > 0 ? `缓存写入 ${formatCompactCount(summary.writeTokens)}` : '',
    summary.inputTokens > 0 ? `输入 ${formatCompactCount(summary.inputTokens)}` : '',
    summary.calls > 0 ? `${formatCompactCount(summary.calls)} calls` : '',
  ].filter(Boolean).join(' / ')
}
export function summarizeCacheUsage(nodes: TraceEventNode[]): CacheUsageSummary {
  const llmCompleteNodes = nodes.filter((node) => node.kind === 'llm_complete')
  const usageNodes = llmCompleteNodes.length > 0 ? llmCompleteNodes : nodes.filter((node) => node.kind === 'task_completed')
  return usageNodes.reduce((total, node) => addCacheUsageSummary(total, traceNodeCacheUsage(node)), emptyCacheUsageSummary())
}
export function traceContextLine(context: TraceEventNode['context'] | null | undefined): string {
  if (!context) {
    return ''
  }
  const tokens = toTraceNumber(context.tokens_estimate)
  const windowSize = toTraceNumber(context.context_window)
  const ratio = toTraceNumber(context.ratio)
  const parts = [
    tokens > 0 ? `${formatCompactCount(tokens)}${windowSize > 0 ? `/${formatCompactCount(windowSize)}` : ''} tokens` : '',
    ratio > 0 ? `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%` : '',
    toTraceNumber(context.messages) > 0 ? `${formatCompactCount(toTraceNumber(context.messages))} messages` : '',
    toTraceNumber(context.tools) > 0 ? `${formatCompactCount(toTraceNumber(context.tools))} tools` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}
export function traceBlockLine(context: TraceEventNode['context'] | null | undefined): string {
  if (!context?.blocks || !isRecord(context.blocks)) {
    return ''
  }
  return Object.entries(context.blocks)
    .filter(([, value]) => toTraceNumber(value) > 0)
    .slice(0, 6)
    .map(([key, value]) => `${key}:${formatCompactCount(toTraceNumber(value))}`)
    .join(' · ')
}
export function traceAssemblyLine(node: TraceEventNode | null): string {
  if (!node || node.kind !== 'context_assembled') {
    return ''
  }
  const included = Array.isArray(node.included)
    ? node.included.map((item) => stringifyValue(item)).filter(Boolean).slice(0, 5).join(', ')
    : isRecord(node.included)
    ? Object.entries(node.included)
        .filter(([, value]) => Boolean(value))
        .map(([key]) => key)
        .slice(0, 5)
        .join(', ')
    : ''
  const assembly = isRecord(node.budget) ? node.budget as ContextAssembly : null
  const parts = [
    toTraceNumber(node.system_chars) > 0 ? `${formatCompactCount(toTraceNumber(node.system_chars))} system chars` : '',
    contextAssemblyLine(assembly),
    toTraceNumber(node.task_chars) > 0 ? `${formatCompactCount(toTraceNumber(node.task_chars))} task chars` : '',
    toTraceNumber(node.session_context_chars) > 0 ? `${formatCompactCount(toTraceNumber(node.session_context_chars))} session chars` : '',
    toTraceNumber(node.history_lines) > 0 ? `${formatCompactCount(toTraceNumber(node.history_lines))} history lines` : '',
    included ? `included ${included}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}
export function tracePlanLine(snapshot: WorkingSnapshot | null | undefined): string {
  if (!snapshot) {
    return ''
  }
  const plan = snapshot.plan
  const goal = stringifyValue(plan?.goal || snapshot.plan_goal || '').trim()
  const active = stringifyValue(plan?.active_step_title || snapshot.plan_active_step_title || '').trim()
  const completed = toTraceNumber(plan?.completed_steps ?? snapshot.plan_completed_steps)
  const total = toTraceNumber(plan?.total_steps ?? snapshot.plan_total_steps)
  const pending = toTraceNumber(plan?.pending_steps ?? snapshot.plan_pending_steps)
  const status = stringifyValue(plan?.status || snapshot.plan_status || '').trim()
  const parts = [
    status ? `status ${status}` : '',
    total > 0 ? `${formatCompactCount(completed)}/${formatCompactCount(total)} steps` : '',
    pending > 0 ? `${formatCompactCount(pending)} pending` : '',
    active ? `active ${active}` : '',
    goal ? compactText(goal, 90) : '',
  ].filter(Boolean)
  return parts.join(' · ')
}
export function traceTodoLine(snapshot: WorkingSnapshot | null | undefined): string {
  if (!snapshot) {
    return ''
  }
  const todo = getTodoSnapshot(snapshot)
  const total = toTraceNumber(todo.total_count)
  const completed = toTraceNumber(todo.completed_count)
  const pending = toTraceNumber(todo.pending_count)
  const active = stringifyValue(todo.active_todo_title || '').trim()
  const parts = [
    total > 0 ? `${formatCompactCount(completed)}/${formatCompactCount(total)} done` : '',
    pending > 0 ? `${formatCompactCount(pending)} open` : '',
    active ? `active ${active}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
}
export function summarizeTrace(nodes: TraceEventNode[]): TraceSummary {
  const llmCompleteNodes = nodes.filter((node) => node.kind === 'llm_complete')
  const usageNodes = llmCompleteNodes.length > 0 ? llmCompleteNodes : nodes.filter((node) => node.kind === 'task_completed')
  const taskCompleted = [...nodes].reverse().find((node) => node.kind === 'task_completed')
  const modelStarts = nodes.filter((node) => node.kind === 'llm_start').length
  const toolStarts = nodes.filter((node) => node.kind === 'tool_started').length
  const permissionRequests = nodes.filter((node) => node.kind === 'permission_requested').length
  return {
    modelCalls: modelStarts || llmCompleteNodes.length,
    tools: toolStarts || nodes.filter((node) => node.kind === 'tool_completed').length,
    permissions: permissionRequests || nodes.filter((node) => node.kind.startsWith('permission_')).length,
    tokens: usageNodes.reduce((total, node) => total + traceUsageTotal(node.usage), 0),
    cost: usageNodes.reduce((total, node) => total + toTraceNumber(node.cost), 0),
    elapsedMs: taskCompleted ? toTraceNumber(taskCompleted.elapsed_ms) : usageNodes.reduce((total, node) => total + toTraceNumber(node.elapsed_ms), 0),
  }
}
export function normalizeTraceNode(raw: unknown): TraceEventNode | null {
  if (!isRecord(raw)) {
    return null
  }
  const kind = stringifyValue(raw.kind || '').trim()
  if (!kind) {
    return null
  }
  const timestamp = stringifyValue(raw.timestamp || new Date().toISOString()).trim()
  const id = stringifyValue(raw.id || '').trim() || createMessageId(`trace-${kind}`)
  const node = {
    ...raw,
    id,
    kind,
    timestamp,
  } as TraceEventNode
  const sequence = Number(raw.sequence)
  if (Number.isFinite(sequence)) {
    node.sequence = sequence
  }
  const turn = Number(raw.turn)
  if (Number.isFinite(turn) && turn > 0) {
    node.turn = turn
  }
  return node
}
export function compareTraceNodes(left: TraceEventNode, right: TraceEventNode): number {
  const leftTime = Date.parse(left.timestamp || '')
  const rightTime = Date.parse(right.timestamp || '')
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
    return leftTime - rightTime
  }
  const leftSequence = Number(left.sequence)
  const rightSequence = Number(right.sequence)
  if (Number.isFinite(leftSequence) && Number.isFinite(rightSequence) && leftSequence !== rightSequence) {
    return leftSequence - rightSequence
  }
  return String(left.id || '').localeCompare(String(right.id || ''))
}
export function sameTraceNodes(left: TraceEventNode[], right: TraceEventNode[]): boolean {
  if (left.length !== right.length) {
    return false
  }
  for (let index = 0; index < left.length; index += 1) {
    const a = left[index]
    const b = right[index]
    if (
      a.id !== b.id ||
      a.kind !== b.kind ||
      a.timestamp !== b.timestamp ||
      a.status !== b.status ||
      a.turn !== b.turn ||
      a.task_id !== b.task_id ||
      a.final_preview !== b.final_preview ||
      a.content_preview !== b.content_preview
    ) {
      return false
    }
  }
  return true
}
export function traceNodeTaskId(node: TraceEventNode): string {
  const explicit = stringifyValue(node.task_id || node.taskId || '').trim()
  if (explicit) {
    return explicit
  }
  const id = stringifyValue(node.id || '').trim()
  const match = new RegExp(`^(.*)-\\d+-${node.kind}$`).exec(id)
  return match?.[1] || ''
}
export function traceShortTaskId(id: string): string {
  const clean = id.trim()
  return clean.length > 8 ? clean.slice(0, 8) : clean
}
export function traceTaskStatus(nodes: TraceEventNode[]): TraceTaskGroup['status'] {
  const terminal = [...nodes].reverse().find((node) => node.kind === 'task_completed')
  if (terminal) {
    if (terminal.need_user) {
      return 'waiting'
    }
    if (terminal.ok === false || terminal.cancelled) {
      return 'error'
    }
    return 'done'
  }
  if ([...nodes].reverse().some((node) => node.kind === 'permission_requested' || node.need_user)) {
    return 'waiting'
  }
  return 'running'
}
export function traceTaskLabel(nodes: TraceEventNode[], index: number, id: string): string {
  const started = nodes.find((node) => node.kind === 'task_started')
  const preview = compactText(started?.task_preview || started?.content_preview || '', 48)
  if (preview) {
    return preview
  }
  if (id === 'legacy') {
    return 'Legacy trace'
  }
  return `Task ${index + 1} - ${traceShortTaskId(id)}`
}
export function traceTaskSubtitle(nodes: TraceEventNode[], status: TraceTaskGroup['status']): string {
  const first = nodes[0]
  const parts = [
    status,
    `${formatCompactCount(nodes.length)} events`,
    first?.timestamp ? formatTraceTime(first.timestamp) : '',
  ].filter(Boolean)
  return parts.join(' - ')
}
export function groupTraceByTask(nodes: TraceEventNode[]): TraceTaskGroup[] {
  const groups: TraceTaskGroup[] = []
  const byId = new Map<string, TraceTaskGroup>()
  let currentTaskId = ''

  for (const node of [...nodes].sort(compareTraceNodes)) {
    const explicitTaskId = traceNodeTaskId(node)
    if (node.kind === 'task_started' && explicitTaskId) {
      currentTaskId = explicitTaskId
    }
    const id = explicitTaskId || currentTaskId || 'legacy'
    let group = byId.get(id)
    if (!group) {
      group = {
        id,
        label: '',
        subtitle: '',
        status: 'running',
        nodes: [],
      }
      byId.set(id, group)
      groups.push(group)
    }
    group.nodes.push(node)
  }

  return groups.map((group, index) => {
    const status = traceTaskStatus(group.nodes)
    return {
      ...group,
      status,
      label: traceTaskLabel(group.nodes, index, group.id),
      subtitle: traceTaskSubtitle(group.nodes, status),
    }
  })
}
export function buildCacheUsageByTask(groups: TraceTaskGroup[]): CacheUsageByTask {
  const byTask: CacheUsageByTask = new Map()
  for (const group of groups) {
    const byTurn: CacheUsageByTurn = new Map()
    for (const node of group.nodes) {
      if (node.kind !== 'llm_complete' || !node.turn) {
        continue
      }
      const usage = traceNodeCacheUsage(node)
      if (!cacheUsageHasData(usage)) {
        continue
      }
      const current = byTurn.get(node.turn) || emptyCacheUsageSummary()
      byTurn.set(node.turn, addCacheUsageSummary(current, usage))
    }
    if (byTurn.size > 0) {
      byTask.set(group.id, byTurn)
    }
  }
  return byTask
}
export function cacheUsageForTurn(cacheByTask: CacheUsageByTask, taskId: string, turn?: number): CacheUsageSummary | null {
  if (!taskId || !turn) {
    return null
  }
  return cacheByTask.get(taskId)?.get(turn) || null
}
export function attachCacheUsageToMessages(
  messages: ViewMessage[],
  cacheByTask: CacheUsageByTask,
  defaultTaskId: string,
): ViewMessage[] {
  let changed = false
  const next = messages.map((message) => {
    if (message.kind !== 'tool') {
      return message
    }
    const messageTaskId = normalizeLiveTaskId(message.taskId)
    const taskId = messageTaskId === PENDING_TASK_ID ? defaultTaskId : messageTaskId || defaultTaskId
    const cache = cacheUsageForTurn(cacheByTask, taskId, message.turn)
    if (!cacheUsageHasData(cache)) {
      return message
    }
    changed = true
    return { ...message, cache }
  })
  return changed ? next : messages
}
export function traceKindClass(node: TraceEventNode): string {
  if (node.kind.startsWith('llm_')) {
    return 'model'
  }
  if (node.kind.startsWith('tool_')) {
    return node.ok === false ? 'tool error' : 'tool'
  }
  if (node.kind.startsWith('permission_')) {
    return node.kind === 'permission_denied' ? 'permission error' : 'permission'
  }
  if (node.kind === 'context_assembled') {
    return 'context'
  }
  if (node.kind === 'working_updated') {
    return 'working'
  }
  if (node.kind === 'task_completed' && (node.ok === false || node.need_user || node.cancelled)) {
    return 'task warning'
  }
  if (node.kind.startsWith('task_')) {
    return 'task'
  }
  return 'status'
}
export function traceTitle(node: TraceEventNode | null): string {
  if (!node) {
    return '等待运行事件'
  }
  const model = stringifyValue(node.model || node.model_id || '').trim()
  const tool = stringifyValue(node.tool || '').trim()
  if (node.kind === 'task_started') {
    return '任务开始'
  }
  if (node.kind === 'task_completed') {
    if (node.cancelled) {
      return '任务已取消'
    }
    if (node.need_user) {
      return '等待用户处理'
    }
    return node.ok === false ? '任务异常结束' : '任务完成'
  }
  if (node.kind === 'status') {
    return `状态 · ${stringifyValue(node.status || 'running')}`
  }
  if (node.kind === 'context_assembled') {
    return '上下文已组装'
  }
  if (node.kind === 'llm_start') {
    return `模型调用开始${model ? ` · ${model}` : ''}`
  }
  if (node.kind === 'llm_complete') {
    if (node.cancelled) {
      return '模型调用取消'
    }
    if (Boolean(node.is_error)) {
      return '模型调用异常'
    }
    return `模型调用完成${model ? ` · ${model}` : ''}`
  }
  if (node.kind === 'tool_started') {
    return `运行工具${tool ? ` · ${tool}` : ''}`
  }
  if (node.kind === 'tool_completed') {
    return `${node.ok === false ? '工具失败' : '工具完成'}${tool ? ` · ${tool}` : ''}`
  }
  if (node.kind === 'permission_requested') {
    return `权限请求${tool ? ` · ${tool}` : ''}`
  }
  if (node.kind === 'permission_resolved') {
    return `权限处理 · ${stringifyValue(node.action || 'resolved')}`
  }
  if (node.kind === 'permission_denied') {
    return '权限拒绝'
  }
  if (node.kind === 'working_updated') {
    return tracePlanLine(node.snapshot) ? '计划进度更新' : '工作记忆更新'
  }
  return node.kind.replace(/_/g, ' ')
}
export function traceSubtitle(node: TraceEventNode | null): string {
  if (!node) {
    return '下一次任务开始后会自动记录'
  }
  const parts = [
    node.turn ? `Turn ${node.turn}` : '',
    node.protocol ? String(node.protocol) : '',
    node.call_id ? `call ${String(node.call_id).slice(0, 8)}` : '',
    traceUsageAndCacheLine(node),
    toTraceNumber(node.cost) > 0 ? formatCost(toTraceNumber(node.cost)) : '',
    toTraceNumber(node.elapsed_ms) > 0 ? formatMs(toTraceNumber(node.elapsed_ms)) : '',
    traceContextLine(node.context),
    traceAssemblyLine(node),
    tracePlanLine(node.snapshot) || traceTodoLine(node.snapshot),
  ].filter(Boolean)
  return parts.join(' · ') || node.kind
}
export function traceDetails(node: TraceEventNode | null): TraceDetail[] {
  if (!node) {
    return []
  }
  const details: TraceDetail[] = []
  const push = (label: string, value: unknown, limit = 220) => {
    const text = typeof value === 'string' ? value : stringifyValue(value)
    const preview = compactText(text, limit)
    if (preview) {
      details.push({ label, value: preview })
    }
  }
  push('时间', node.timestamp ? formatTimestamp(node.timestamp) : '')
  push('序号', node.sequence ?? '')
  push('类型', node.kind)
  push('模型', [node.model, node.model_id, node.protocol].filter(Boolean).join(' · '))
  push('轮次', node.turn ? `Turn ${node.turn}` : '')
  push('上下文', traceContextLine(node.context))
  push('组装', traceAssemblyLine(node))
  push('Context budget', contextAssemblyDetailText(isRecord(node.budget) ? node.budget as ContextAssembly : null), 900)
  push('Blocks', traceBlockLine(node.context))
  push('Token', traceUsageAndCacheLine(node))
  push('成本', toTraceNumber(node.cost) > 0 ? formatCost(toTraceNumber(node.cost)) : '')
  push('耗时', toTraceNumber(node.elapsed_ms) > 0 ? formatMs(toTraceNumber(node.elapsed_ms)) : '')
  push('工具', node.tool || '')
  push('Args', node.args, 360)
  push('Observation', node.observation, 360)
  push('权限', isRecord(node.request) ? permissionSummary(node.request) : node.reason || node.action || '')
  push('Plan', tracePlanLine(node.snapshot))
  push('TODO', traceTodoLine(node.snapshot))
  push('预览', node.content_preview || node.final_preview || node.task_preview || '')
  return details
}
