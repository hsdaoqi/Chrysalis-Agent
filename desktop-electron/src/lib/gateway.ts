import type { GatewayStatus, GatewayPlatformSnapshot, GatewayActivity, GatewayActivityEvent, ViewMessage } from '../types'
import type { LiveSessionState } from '../app-types'
import { compactText, isRecord, stringifyValue, toTraceNumber } from './format'
import { normalizeLiveTaskId, streamPreviewText, trimLiveStreamBuffer } from './messages'

export function gatewayStatusLabel(status?: GatewayStatus): string {
  if (status === 'running') {
    return '运行中'
  }
  if (status === 'failed') {
    return '连接失败'
  }
  if (status === 'configured') {
    return '已配置'
  }
  return '未配置'
}
export function gatewayStatusClass(status?: GatewayStatus): string {
  if (status === 'running') {
    return 'running'
  }
  if (status === 'failed') {
    return 'failed'
  }
  if (status === 'configured') {
    return 'configured'
  }
  return 'not-configured'
}
export function gatewayStatusDetail(platform: GatewayPlatformSnapshot): string {
  if (platform.running && platform.pid) {
    return `PID ${platform.pid}`
  }
  if (platform.status === 'failed') {
    return platform.last_error || platform.install_hint || '最近一次启动失败'
  }
  if (platform.status === 'not_configured') {
    return platform.configuration_error || platform.config_summary || '需要补充连接配置'
  }
  return platform.config_summary || platform.launch_platform || ''
}
export function gatewayCopyText(platform: GatewayPlatformSnapshot): string {
  const diagnostics = [
    platform.last_error,
    platform.configuration_error,
    platform.install_hint,
  ].filter(Boolean)
  if (diagnostics.length === 0) {
    return ''
  }
  return [
    ...diagnostics,
    platform.command ? `Command: ${platform.command}` : '',
    platform.log_file ? `Log: ${platform.log_file}` : '',
  ].filter(Boolean).join('\n')
}
export function gatewayLiveStateFromActivity(activity: GatewayActivity): { sessionId: string; state: LiveSessionState } | null {
  const sessionId = stringifyValue(activity.session_id).trim()
  const taskId = normalizeLiveTaskId(activity.task_id)
  if (!sessionId || !taskId) {
    return null
  }

  const messages: ViewMessage[] = []
  const userBody = stringifyValue(activity.task_preview).trim()
  if (userBody) {
    messages.push({
      id: gatewayMessageId(taskId, 'user'),
      kind: 'user',
      role: 'user',
      title: gatewayUserTitle(activity),
      body: userBody,
      turn: 1,
      taskId,
    })
  }

  let latestTurn = Math.max(0, toTraceNumber(activity.turn))
  const events = gatewayActivityEvents(activity)
  events.forEach((event, index) => {
    const kind = stringifyValue(event.kind).trim()
    if (kind === 'tool_started') {
      const turn = gatewayEventTurn(event, latestTurn + 1)
      latestTurn = Math.max(latestTurn, turn)
      const tool = stringifyValue(event.tool || 'tool').trim() || 'tool'
      const argsText = stringifyValue(event.args).trim()
      const thoughtText = stringifyValue(event.thought).trim()
      messages.push({
        id: gatewayEventMessageId(event, taskId, index, 'tool-started'),
        kind: 'tool',
        role: 'assistant',
        title: `Turn ${turn} - running ${tool} ...`,
        body: compactText(argsText, 160) || 'Args pending',
        details: [
          ...(thoughtText ? [`Thought:\n${thoughtText}`] : []),
          ...(argsText ? [`Args:\n${argsText}`] : []),
        ],
        meta: 'running',
        status: 'running',
        turn,
        taskId,
      })
      return
    }
    if (kind === 'tool_completed') {
      const turn = gatewayEventTurn(event, latestTurn)
      latestTurn = Math.max(latestTurn, turn)
      gatewayCompleteLiveTool(messages, event, taskId, index, turn)
    }
  })

  const streamBuffer = trimLiveStreamBuffer(stringifyValue(activity.stream))
  const streamPreview = streamPreviewText(streamBuffer)
  if (streamPreview) {
    messages.push({
      id: gatewayMessageId(taskId, 'stream'),
      kind: 'assistant',
      role: 'assistant',
      title: 'Assistant',
      body: streamPreview,
      streaming: true,
      turn: latestTurn || undefined,
      taskId,
    })
  }

  return {
    sessionId,
    state: {
      messages,
      streamBuffer,
      turn: latestTurn || null,
      taskId,
      started: true,
    },
  }
}
export function gatewayActivityEvents(activity: GatewayActivity): GatewayActivityEvent[] {
  return Array.isArray(activity.events)
    ? activity.events.filter((event): event is GatewayActivityEvent => isRecord(event))
    : []
}
export function gatewayUserTitle(activity: GatewayActivity): string {
  const platform = stringifyValue(activity.platform || '').trim()
  const source = isRecord(activity.source) ? activity.source : {}
  const label = stringifyValue(source.description || source.chat_name || source.user_name || '').trim()
  return [platform ? platform.toUpperCase() : 'Gateway', label].filter(Boolean).join(' · ')
}
export function gatewayEventTurn(event: GatewayActivityEvent, fallback: number): number {
  const turn = toTraceNumber(event.turn)
  return turn > 0 ? turn : Math.max(1, fallback)
}
export function gatewayMessageId(taskId: string, suffix: string): string {
  return `gateway-${taskId}-${suffix}`
}
export function gatewayEventMessageId(event: GatewayActivityEvent, taskId: string, index: number, suffix: string): string {
  const eventId = stringifyValue(event.id).trim()
  return eventId ? `gateway-${eventId}` : `gateway-${taskId}-${index}-${suffix}`
}
export function gatewayCompleteLiveTool(
  messages: ViewMessage[],
  event: GatewayActivityEvent,
  taskId: string,
  index: number,
  turn: number,
): void {
  const tool = stringifyValue(event.tool || 'tool').trim() || 'tool'
  const observationText = stringifyValue(event.observation).trim()
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const item = messages[messageIndex]
    if (item.kind !== 'tool' || item.status !== 'running' || item.turn !== turn) {
      continue
    }
    const details = Array.isArray(item.details) ? [...item.details] : []
    details.push(`Observation:\n${observationText || 'No observation'}`)
    messages[messageIndex] = {
      ...item,
      title: `Turn ${turn} - ok ${tool} - ${compactText(observationText || 'done', 48)}`,
      body: compactText(observationText || item.body || 'No observation', 180),
      details,
      meta: 'done',
      status: 'done',
      taskId,
    }
    return
  }
  messages.push({
    id: gatewayEventMessageId(event, taskId, index, 'tool-completed'),
    kind: 'tool',
    role: 'assistant',
    title: `Turn ${turn} - ok ${tool} - ${compactText(observationText || 'done', 48)}`,
    body: compactText(observationText || 'No observation', 180),
    details: observationText ? [`Observation:\n${observationText}`] : [],
    meta: 'done',
    status: 'done',
    turn,
    taskId,
  })
}
