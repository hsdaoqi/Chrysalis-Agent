import type { CacheUsageSummary, PermissionLevel, TraceEventNode, ViewMessage } from './types'
import type { CronSchedule, CronJob, CronDaemonSnapshot } from './types'

export interface CronFormState {
  id: string
  name: string
  scheduleType: 'once' | 'periodic'
  period: string
  runAt: string
  time: string
  startAt: string
  weekday: string
  day: string
  month: string
  intervalCount: string
  intervalUnit: 'minutes' | 'hours' | 'days' | 'weeks' | 'months' | 'years'
  prompt: string
  script: string
  noAgent: boolean
  contextFrom: string
  workdir: string
  repeatTimes: string
  maxDelayMinutes: string
}

export interface LiveSessionState {
  messages: ViewMessage[]
  streamBuffer: string
  turn: number | null
  taskId: string
  started: boolean
  pausedForUser?: boolean
}

export interface PendingChoice {
  label: string
  value: string
  description: string
}
export interface PendingRequestState {
  id: string
  sessionId: string
  kind: 'ask_user' | 'permission'
  title: string
  question: string
  summary: string
  tool: string
  risk: string
  reason: string
  choices: PendingChoice[]
}

export interface ReviewEditState {
  itemId: string
  title: string
  target: string
  description: string
  content: string
}

export interface TraceSummary {
  modelCalls: number
  tools: number
  permissions: number
  tokens: number
  cost: number
  elapsedMs: number
}
export interface TraceDetail {
  label: string
  value: string
}
export interface TraceTaskGroup {
  id: string
  label: string
  subtitle: string
  status: 'running' | 'done' | 'error' | 'waiting'
  nodes: TraceEventNode[]
}
export type CacheUsageByTurn = Map<number, CacheUsageSummary>
export type CacheUsageByTask = Map<string, CacheUsageByTurn>

export interface SettingsFormState {
  enabled: boolean
  name: string
  provider: string
  apiKey: string
  baseUrl: string
  model: string
  wireApi: string
  contextWindow: string
  temperature: string
  maxTokens: string
  maxRetries: string
  timeout: string
  proxy: string
  thinking: string
  thinkingBudget: string
  systemPrompt: string
  permissionLevel: PermissionLevel
}
