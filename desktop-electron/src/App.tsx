import { useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import {
  ArrowLeft,
  AlertTriangle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Command,
  Copy,
  FileText,
  MessageSquare,
  Minus,
  Paperclip,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Settings,
  Shield,
  Square,
  Trash2,
  Terminal,
  Wrench,
  X,
} from 'lucide-react'
import { ProseMirrorComposer } from './editor/ProseMirrorComposer'
import { normalizeHistory } from './history'
import { composeDisplayTask, filterSessions, hasTodoSnapshot, mergeLiveContextUsage, parseBtwCommand, roleLabel, statusLabel, stripAnsi, todoItemActive, todoItemStatusClass } from './lib/misc'
import { PERMISSION_LEVEL_OPTIONS, createSettingsForm, normalizePermissionLevel, permissionLevelLabel, settingsPayload } from './lib/settings'
import { addCacheUsageSummary, attachCacheUsageToMessages, buildCacheUsageByTask, cacheUsageForTurn, cacheUsageHasData, compareTraceNodes, emptyCacheUsageSummary, formatCacheHitRate, formatCacheTitle, formatTurnCacheLabel, getTodoSnapshot, groupTraceByTask, normalizeTraceNode, sameTraceNodes, summarizeCacheUsage, summarizeTrace, traceAssemblyLine, traceBlockLine, traceCacheLine, traceContextLine, traceDetails, traceKindClass, traceNodeCacheUsage, traceNodeTaskId, tracePlanLine, traceShortTaskId, traceSubtitle, traceTaskLabel, traceTaskStatus, traceTaskSubtitle, traceTitle, traceTodoLine, traceUsageAndCacheLine, traceUsageLine, traceUsageTotal } from './lib/trace'
import { MEMORY_TARGET_OPTIONS, createReviewEditState, normalizedReviewSummary, reviewItemSummary, reviewItemTitle, reviewQualityLine, reviewStatusLabel, reviewSummaryCards, reviewTargetLabel } from './lib/review'
import { gatewayActivityEvents, gatewayCompleteLiveTool, gatewayCopyText, gatewayEventMessageId, gatewayEventTurn, gatewayLiveStateFromActivity, gatewayMessageId, gatewayStatusClass, gatewayStatusDetail, gatewayStatusLabel, gatewayUserTitle } from './lib/gateway'
import { createPendingRequest, dedupePendingChoices, extractInlinePendingChoices, pendingChoices, permissionSummary } from './lib/pending'
import { contextAssemblyDetailText, contextAssemblyLine, contextAssemblySections, contextRecallItemLine, contextSectionBudgetLine, contextSectionReason, contextSectionRecallItems } from './lib/context'
import { PENDING_TASK_ID, countConversationTurns, countMissingLiveConversationTurns, countViewChatMessages, countViewConversationTurns, createMessageId, filterVisibleChatMessages, historyTurnEndIndex, isLiveFileChangeAnchorMessage, isTurnProcessMessage, liveDiffTaskIds, liveFileChangeAnchorMessages, liveMessageBelongsToTask, liveMessagesForCurrentTask, liveTaskEventApplies, liveTaskIdForEvent, mergeHistoryAndLiveMessages, mergeLiveSessionSummaries, normalizeLiveTaskId, normalizeViewMessageBody, readCanonicalText, streamPreviewText, stripSummaryMarkup, tagLiveMessageTask, tagPendingLiveMessages, trimLiveStreamBuffer, viewMessageDedupKey, viewMessagesEqual } from './lib/messages'
import { buildCronSchedule, buildCronSpec, createCronForm, createCronFormFromJob, cronDaemonLabel, cronIntervalFormValues, cronIntervalUnitLabel, cronJobNotice, cronJobStatus, cronLastStatusLabel, cronScheduleLabel, normalizeCronDateTimeInput, normalizeCronIntervalUnit, parseCronContext, positiveCronNumber, toLocalDateTimeValue } from './lib/cron'
import type { CacheUsageByTask, CacheUsageByTurn, CronFormState, LiveSessionState, PendingChoice, PendingRequestState, ReviewEditState, SettingsFormState, TraceDetail, TraceSummary, TraceTaskGroup } from './app-types'
import { buildFileChange, clampNumber, compactText, diffStats, fileChangeKey, fileChangeTotals, fileNameFromPath, formatBytes, formatCompactCount, formatCost, formatFileChangeBody, formatFileChangeSummary, formatMs, formatReviewTime, formatSessionAge, formatTimestamp, formatTraceTime, isRecord, parseFloatOrFallback, parseIntOrFallback, stringifyValue, toTraceNumber } from './lib/format'
import type {
  AttachmentSummary,
  CacheUsageSummary,
  ContextAssembly,
  ContextAssemblySection,
  ContextAssemblySectionItem,
  CronDaemonSnapshot,
  CronJob,
  CronJobCreateSpec,
  CronSchedule,
  CronSnapshot,
  GatewayActivity,
  GatewayActivityEvent,
  GatewayLogResponse,
  GatewayPlatformSnapshot,
  GatewaySnapshot,
  GatewayStatus,
  FileChangeSummary,
  PermissionLevel,
  ReviewItem,
  ReviewPatch,
  ReviewSummary,
  ReviewSnapshot,
  ReviewStatus,
  RuntimeEvent,
  RuntimeResponse,
  RuntimeSnapshot,
  SessionSummary,
  SettingsSnapshot,
  TaskReviewSummary,
  TraceEventNode,
  ViewMessage,
  WorkingSnapshot,
  WorkingTodoItem,
  WorkingTodoSnapshot,
} from './types'

type Page = 'chat' | 'settings' | 'cron' | 'reviews' | 'gateway'
type InspectorMode = 'context' | 'trace'
type ReviewFilter = 'pending' | 'all' | 'approved' | 'discarded'



interface RenameState {
  open: boolean
  sessionId: string
  title: string
}





interface GrowthNoticeState {
  id: string
  title: string
  summary: string
  itemIds: string[]
  count: number
}

interface GatewayLogState {
  platformId: string
  loading: boolean
  text: string
  path: string
}













interface HistoryCacheState {
  sessionId: string
  signature: string
  messages: ViewMessage[]
}




type ResizeEdge = 'left' | 'right'

interface WorkspaceLayoutState {
  sidebarWidth: number
  inspectorWidth: number
}

interface WorkspaceResizeDrag {
  edge: ResizeEdge
  startX: number
  sidebarWidth: number
  inspectorWidth: number
  containerWidth: number
}

const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayoutState = {
  sidebarWidth: 236,
  inspectorWidth: 290,
}

const WORKSPACE_RESIZER_WIDTH = 10
const WORKSPACE_RESIZE_LIMITS = {
  sidebarMin: 190,
  sidebarMax: 420,
  inspectorMin: 230,
  inspectorMax: 520,
  centerMin: 480,
}


const WIRE_API_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'chat', label: 'chat' },
  { value: 'responses', label: 'responses' },
]

const THINKING_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'disabled', label: 'disabled' },
  { value: 'minimal', label: 'minimal' },
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
  { value: 'xhigh', label: 'xhigh' },
  { value: 'enabled', label: 'enabled' },
]

function emptyLiveSessionState(): LiveSessionState {
  return {
    messages: [],
    streamBuffer: '',
    turn: null,
    taskId: '',
    started: false,
    pausedForUser: false,
  }
}

const emptyWorking: WorkingSnapshot = {}

const emptyCronSnapshot: CronSnapshot = {
  daemon: {
    running: false,
    interval_seconds: 60,
    started_at: null,
    last_tick_at: null,
    last_count: 0,
    last_error: null,
  },
  jobs: [],
}

const emptyReviewSnapshot: ReviewSnapshot = {
  items: [],
  stats: {},
  errors: [],
}

const emptyGatewaySnapshot: GatewaySnapshot = {
  platforms: [],
}

const REVIEW_FILTERS: Array<{ value: ReviewFilter; label: string }> = [
  { value: 'pending', label: '待审核' },
  { value: 'all', label: '全部' },
  { value: 'approved', label: '已批准' },
  { value: 'discarded', label: '已丢弃' },
]





























































































































































function createGrowthNotice(summary: unknown, taskId = ''): GrowthNoticeState | null {
  if (!isRecord(summary) || !summary.has_candidates) {
    return null
  }
  const typed = summary as TaskReviewSummary
  const rawItems = Array.isArray(summary.items) ? summary.items : []
  const itemIds = rawItems
    .map((item) => isRecord(item) ? stringifyValue(item.id).trim() : '')
    .filter(Boolean)
  const count = Math.max(toTraceNumber(summary.count), itemIds.length)
  if (count <= 0 || itemIds.length === 0) {
    return null
  }
  const titles = rawItems
    .map((item) => isRecord(item) ? stringifyValue(item.title || item.label || item.id).trim() : '')
    .filter(Boolean)
    .slice(0, 3)
  const title = stringifyValue(typed.headline || '').trim() || `发现 ${count} 个成长候选`
  const reason = stringifyValue(typed.reason || '').trim()
  return {
    id: `${taskId || Date.now()}-${itemIds.join('|')}`,
    title,
    summary: [reason, titles.join(' · ')].filter(Boolean).join(' - '),
    itemIds,
    count,
  }
}

































































































function traceIcon(node: TraceEventNode | null): ReactNode {
  if (!node) {
    return <Clock3 size={14} />
  }
  const tone = traceKindClass(node)
  if (tone.includes('model')) {
    return <Bot size={14} />
  }
  if (tone.includes('tool')) {
    return <Wrench size={14} />
  }
  if (tone.includes('permission') || tone.includes('warning') || tone.includes('error')) {
    return <AlertTriangle size={14} />
  }
  if (tone.includes('context') || tone.includes('working')) {
    return <FileText size={14} />
  }
  if (tone.includes('task')) {
    return <CheckCircle2 size={14} />
  }
  return <Clock3 size={14} />
}

















function kindIcon(message: ViewMessage): ReactNode {
  if (message.kind === 'user') {
    return <MessageSquare size={14} />
  }
  if (message.kind === 'assistant') {
    return <Bot size={14} />
  }
  if (message.kind === 'tool') {
    return <Wrench size={14} />
  }
  if (message.kind === 'thinking') {
    return <Clock3 size={14} />
  }
  if (message.kind === 'diff') {
    return <FileText size={14} />
  }
  if (message.kind === 'warning' || message.kind === 'error') {
    return <AlertTriangle size={14} />
  }
  if (message.kind === 'usage' || message.kind === 'status') {
    return <CheckCircle2 size={14} />
  }
  return <FileText size={14} />
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  const tag = target.tagName.toLowerCase()
  return target.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select'
}

function extractDropPaths(event: DragEvent<HTMLElement>): string[] {
  return Array.from(event.dataTransfer.files)
    .map((file) => (file as File & { path?: string }).path || '')
    .filter(Boolean)
}

export function App() {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null)
  const snapshotRef = useRef<RuntimeSnapshot | null>(null)
  const [historyMessages, setHistoryMessages] = useState<ViewMessage[]>([])
  const [working, setWorking] = useState<WorkingSnapshot>(emptyWorking)
  const [statusText, setStatusText] = useState('ready')
  const [noticeText, setNoticeText] = useState('')
  const [errorText, setErrorText] = useState('')
  const [ready, setReady] = useState(false)
  const [page, setPage] = useState<Page>('chat')
  const [searchText, setSearchText] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteText, setPaletteText] = useState('')
  const [draftText, setDraftText] = useState('')
  const [composerResetKey, setComposerResetKey] = useState(0)
  const [attachments, setAttachments] = useState<AttachmentSummary[]>([])
  const [pendingRequest, setPendingRequest] = useState<PendingRequestState | null>(null)
  const [resumableSessionId, setResumableSessionId] = useState('')
  const [permissionMenuOpen, setPermissionMenuOpen] = useState(false)
  const [settingsForm, setSettingsForm] = useState<SettingsFormState>(() => createSettingsForm())
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [cronForm, setCronForm] = useState<CronFormState>(() => createCronForm())
  const [cronSelectedJobId, setCronSelectedJobId] = useState('')
  const [cronEditingJobId, setCronEditingJobId] = useState('')
  const [cronDaemonInterval, setCronDaemonInterval] = useState('60')
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>('pending')
  const [selectedReviewId, setSelectedReviewId] = useState('')
  const [reviewDraft, setReviewDraft] = useState<ReviewEditState>(() => createReviewEditState())
  const [reviewSaving, setReviewSaving] = useState('')
  const [growthNotice, setGrowthNotice] = useState<GrowthNoticeState | null>(null)
  const [gatewayLog, setGatewayLog] = useState<GatewayLogState>({ platformId: '', loading: false, text: '', path: '' })
  const [renameState, setRenameState] = useState<RenameState>({ open: false, sessionId: '', title: '' })
  const [dragDepth, setDragDepth] = useState(0)
  const [stickyToBottom, setStickyToBottom] = useState(true)
  const [liveSessions, setLiveSessions] = useState<Record<string, LiveSessionState>>({})
  const [inspectorMode, setInspectorMode] = useState<InspectorMode>('context')
  const [traceBySession, setTraceBySession] = useState<Record<string, TraceEventNode[]>>({})
  const [selectedTraceTaskId, setSelectedTraceTaskId] = useState('')
  const [traceReplayIndex, setTraceReplayIndex] = useState<number | null>(null)
  const [tracePlaying, setTracePlaying] = useState(false)
  const [workspaceLayout, setWorkspaceLayout] = useState<WorkspaceLayoutState>(DEFAULT_WORKSPACE_LAYOUT)
  const [resizingEdge, setResizingEdge] = useState<ResizeEdge | null>(null)
  const liveSessionsRef = useRef<Record<string, LiveSessionState>>({})
  const traceBySessionRef = useRef<Record<string, TraceEventNode[]>>({})
  const historyCacheRef = useRef<HistoryCacheState>({ sessionId: '', signature: '', messages: [] })
  const workspaceRef = useRef<HTMLDivElement | null>(null)
  const workspaceResizeRef = useRef<WorkspaceResizeDrag | null>(null)
  const messageScrollRef = useRef<HTMLDivElement | null>(null)
  const paletteInputRef = useRef<HTMLInputElement | null>(null)
  const renameInputRef = useRef<HTMLInputElement | null>(null)

  const allSessions = snapshot?.sessions || []
  const activeSessionId = snapshot?.active_session_id || ''
  const activeLiveSession = activeSessionId ? liveSessions[activeSessionId] || emptyLiveSessionState() : emptyLiveSessionState()
  const displaySessions = useMemo(
    () => mergeLiveSessionSummaries(allSessions, liveSessions, activeSessionId, snapshot?.history || []),
    [allSessions, liveSessions, activeSessionId, snapshot?.history],
  )
  const filterText = paletteOpen ? paletteText : searchText
  const sessions = useMemo(() => filterSessions(displaySessions, filterText), [displaySessions, filterText])
  const activeSession = useMemo(
    () => displaySessions.find((session) => session.id === activeSessionId),
    [displaySessions, activeSessionId],
  )
  const activeSessionBusy = Boolean(activeSession?.busy)
  const busy = Boolean(activeSessionBusy || activeLiveSession.taskId)
  const permissionLevel = normalizePermissionLevel(snapshot?.settings?.permission_level || settingsForm.permissionLevel)
  const cronSnapshot = snapshot?.cron || emptyCronSnapshot
  const reviewSnapshot = snapshot?.reviews || emptyReviewSnapshot
  const gatewaySnapshot = snapshot?.gateway || emptyGatewaySnapshot
  const gatewayPlatforms = gatewaySnapshot.platforms || []
  const gatewayRunningCount = gatewayPlatforms.filter((platform) => platform.status === 'running').length
  const gatewayConfiguredCount = gatewayPlatforms.filter((platform) => platform.status === 'configured' || platform.status === 'running').length
  const gatewayFailedCount = gatewayPlatforms.filter((platform) => platform.status === 'failed').length
  const reviewItems = reviewSnapshot.items || []
  const reviewStats = reviewSnapshot.stats || {}
  const filteredReviewItems = useMemo(
    () => reviewItems.filter((item) => reviewFilter === 'all' || item.status === reviewFilter),
    [reviewFilter, reviewItems],
  )
  const selectedReviewItem = useMemo(
    () => filteredReviewItems.find((item) => item.id === selectedReviewId) || filteredReviewItems[0] || reviewItems.find((item) => item.id === selectedReviewId) || null,
    [filteredReviewItems, reviewItems, selectedReviewId],
  )
  const cronJobs = useMemo(
    () => [...(cronSnapshot.jobs || [])].sort((left, right) => {
      const leftKey = left.state?.next_run_at || left.created_at || left.id
      const rightKey = right.state?.next_run_at || right.created_at || right.id
      return String(leftKey).localeCompare(String(rightKey))
    }),
    [cronSnapshot.jobs],
  )
  const selectedCronJob = useMemo(
    () => cronJobs.find((job) => job.id === cronSelectedJobId) || null,
    [cronJobs, cronSelectedJobId],
  )
  const totalSessions = displaySessions.length
  const attachedFiles = attachments
  const activeLiveMessages = useMemo(
    () => liveMessagesForCurrentTask(activeLiveSession),
    [activeLiveSession],
  )
  const messages = useMemo(
    () => mergeHistoryAndLiveMessages(historyMessages, activeLiveMessages),
    [historyMessages, activeLiveMessages],
  )
  const showTurnMessages = !!(busy || activeLiveSession.started || pendingRequest?.sessionId === activeSessionId)
  const historyChatMessages = useMemo(() => filterVisibleChatMessages(historyMessages, false), [historyMessages])
  const liveChatMessages = useMemo(
    () => filterVisibleChatMessages(activeLiveMessages, showTurnMessages),
    [activeLiveMessages, showTurnMessages],
  )
  const chatMessages = useMemo(
    () => mergeHistoryAndLiveMessages(historyChatMessages, liveChatMessages),
    [historyChatMessages, liveChatMessages],
  )
  const workspaceStyle = useMemo(() => ({
    '--sidebar-width': `${workspaceLayout.sidebarWidth}px`,
    '--inspector-width': `${workspaceLayout.inspectorWidth}px`,
  }) as CSSProperties, [workspaceLayout.inspectorWidth, workspaceLayout.sidebarWidth])
  const visibleMessageCount = useMemo(() => countViewChatMessages(chatMessages), [chatMessages])
  const conversationTurns = useMemo(() => countViewConversationTurns(chatMessages), [chatMessages])
  const activePendingRequest = pendingRequest?.sessionId === activeSessionId ? pendingRequest : null
  const pendingChoices = activePendingRequest?.choices || []
  const todoState = useMemo(() => getTodoSnapshot(working), [working])
  const todoVisible = useMemo(() => hasTodoSnapshot(todoState), [todoState])
  const contextUsage = useMemo(
    () => mergeLiveContextUsage(snapshot?.context || null, visibleMessageCount),
    [snapshot?.context, visibleMessageCount],
  )
  const contextPercent = Math.round(Math.max(0, Math.min(1, Number(contextUsage?.ratio || 0))) * 100)
  const contextTokens = Number(contextUsage?.tokens_estimate || 0)
  const contextWindow = Number(contextUsage?.context_window || 0)
  const contextTokenLabel = contextWindow > 0
    ? `${formatCompactCount(contextTokens)} / ${formatCompactCount(contextWindow)} tokens`
    : `${formatCompactCount(contextTokens)} tokens`
  const memoryTags = useMemo(() => {
    const tags = new Set<string>()
    if (todoState.todos && todoState.todos.length > 0) {
      tags.add('todo')
    }
    if (todoState.active_todo_title) {
      tags.add('active-todo')
    }
    if (Number(todoState.completed_count || 0) > 0) {
      tags.add('progress')
    }
    if (snapshot?.workspace_root) {
      tags.add('workspace')
    }
    if (activeSession?.model || snapshot?.model) {
      tags.add('model')
    }
    return Array.from(tags).slice(0, 6)
  }, [activeSession?.model, todoState.active_todo_title, todoState.completed_count, todoState.todos, snapshot?.model, snapshot?.workspace_root])
  const sessionTrace = activeSessionId ? traceBySession[activeSessionId] || [] : []
  const traceTaskGroups = useMemo(() => groupTraceByTask(sessionTrace), [sessionTrace])
  const latestTraceTaskId = traceTaskGroups.length > 0 ? traceTaskGroups[traceTaskGroups.length - 1].id : ''
  const cacheUsageByTask = useMemo(() => buildCacheUsageByTask(traceTaskGroups), [traceTaskGroups])
  const sessionCacheUsage = useMemo(() => summarizeCacheUsage(sessionTrace), [sessionTrace])
  const liveCacheTaskId = normalizeLiveTaskId(activeLiveSession.taskId)
  const defaultCacheTaskId = liveCacheTaskId === PENDING_TASK_ID ? latestTraceTaskId : liveCacheTaskId || latestTraceTaskId
  const chatMessagesWithCache = useMemo(
    () => attachCacheUsageToMessages(
      chatMessages,
      cacheUsageByTask,
      defaultCacheTaskId,
    ),
    [cacheUsageByTask, chatMessages, defaultCacheTaskId],
  )
  const effectiveTraceTaskId = selectedTraceTaskId && traceTaskGroups.some((group) => group.id === selectedTraceTaskId)
    ? selectedTraceTaskId
    : latestTraceTaskId
  const activeTraceTask = traceTaskGroups.find((group) => group.id === effectiveTraceTaskId) || null
  const activeTrace = activeTraceTask?.nodes || []
  const latestContextTrace = useMemo(
    () => [...sessionTrace].reverse().find((node) => node.kind === 'context_assembled') || null,
    [sessionTrace],
  )
  const contextAssembly = useMemo(() => {
    if (isRecord(latestContextTrace?.budget)) {
      return latestContextTrace.budget as ContextAssembly
    }
    return contextUsage?.assembly || null
  }, [contextUsage?.assembly, latestContextTrace])
  const contextSections = useMemo(() => contextAssemblySections(contextAssembly), [contextAssembly])
  const activeTraceSummary = useMemo(() => summarizeTrace(activeTrace), [activeTrace])
  const traceLive = traceReplayIndex === null && effectiveTraceTaskId === latestTraceTaskId
  const replayIndex = activeTrace.length === 0
    ? -1
    : traceReplayIndex === null
      ? activeTrace.length - 1
      : Math.max(0, Math.min(traceReplayIndex, activeTrace.length - 1))
  const replayNode = replayIndex >= 0 ? activeTrace[replayIndex] : null
  const inspectorSubtitle = inspectorMode === 'trace'
    ? `${activeTraceTask?.label || 'No task'} - ${activeTrace.length}/${sessionTrace.length} events - ${activeTraceSummary.modelCalls} calls`
    : `${contextPercent}% - ${contextTokenLabel}${contextSections.length > 0 ? ` - ${contextSections.length} sections` : ''}`

  useEffect(() => {
    let disposed = false
    const dispose = window.chrysalis.onEvent((event) => {
      if (!disposed) {
        void handleEvent(event)
      }
    })
    void refreshSnapshot()
    return () => {
      disposed = true
      dispose()
    }
  }, [])

  useEffect(() => {
    const sessionId = snapshot?.active_session_id || ''
    if (!sessionId) {
      return
    }
    const timer = window.setTimeout(() => {
      void window.chrysalis.saveDraft(draftText)
    }, 150)
    return () => window.clearTimeout(timer)
  }, [draftText, snapshot?.active_session_id])

  useEffect(() => {
    if (page === 'settings' && snapshot?.settings && !settingsDirty) {
      setSettingsForm(createSettingsForm(snapshot.settings))
    }
  }, [page, snapshot?.settings, settingsDirty])

  useEffect(() => {
    if (page !== 'cron') {
      return
    }
    if (cronJobs.length === 0) {
      if (cronSelectedJobId) {
        setCronSelectedJobId('')
      }
      if (cronEditingJobId) {
        setCronEditingJobId('')
        setCronForm(createCronForm())
      }
      return
    }
    if (!cronSelectedJobId || !cronJobs.some((job) => job.id === cronSelectedJobId)) {
      setCronSelectedJobId(cronJobs[0].id)
    }
    if (cronEditingJobId && !cronJobs.some((job) => job.id === cronEditingJobId)) {
      setCronEditingJobId('')
      setCronForm(createCronForm())
    }
  }, [cronEditingJobId, cronJobs, cronSelectedJobId, page])

  useEffect(() => {
    if (page !== 'reviews') {
      return
    }
    if (filteredReviewItems.length === 0) {
      if (selectedReviewId) {
        setSelectedReviewId('')
      }
      return
    }
    if (!selectedReviewId || !filteredReviewItems.some((item) => item.id === selectedReviewId)) {
      setSelectedReviewId(filteredReviewItems[0].id)
    }
  }, [filteredReviewItems, page, reviewItems, selectedReviewId])

  useEffect(() => {
    setReviewDraft(createReviewEditState(selectedReviewItem))
  }, [selectedReviewItem?.id, selectedReviewItem?.updated_at])

  useEffect(() => {
    if (!stickyToBottom || page !== 'chat') {
      return
    }
    const node = messageScrollRef.current
    if (!node) {
      return
    }
    node.scrollTop = node.scrollHeight
  }, [chatMessagesWithCache, page, stickyToBottom])

  useEffect(() => {
    const sessionIds = new Set(allSessions.map((session) => session.id))
    setLiveSessions((current) => {
      let changed = false
      const next: Record<string, LiveSessionState> = {}
      for (const [sessionId, state] of Object.entries(current)) {
        if (!sessionIds.has(sessionId)) {
          changed = true
          continue
        }
        next[sessionId] = state
      }
      return changed ? next : current
    })
  }, [allSessions])

  useEffect(() => {
    liveSessionsRef.current = liveSessions
  }, [liveSessions])

  useEffect(() => {
    traceBySessionRef.current = traceBySession
  }, [traceBySession])

  useEffect(() => {
    setSelectedTraceTaskId('')
    setTraceReplayIndex(null)
    setTracePlaying(false)
  }, [activeSessionId])

  useEffect(() => {
    if (traceTaskGroups.length === 0) {
      if (selectedTraceTaskId) {
        setSelectedTraceTaskId('')
      }
      return
    }
    if (!selectedTraceTaskId || !traceTaskGroups.some((group) => group.id === selectedTraceTaskId)) {
      setSelectedTraceTaskId(latestTraceTaskId)
      setTraceReplayIndex(null)
      setTracePlaying(false)
    }
  }, [latestTraceTaskId, selectedTraceTaskId, traceTaskGroups])

  useEffect(() => {
    if (!tracePlaying) {
      return
    }
    if (activeTrace.length === 0) {
      setTracePlaying(false)
      return
    }
    const timer = window.setInterval(() => {
      setTraceReplayIndex((current) => {
        const cursor = current === null ? -1 : current
        const next = Math.min(cursor + 1, activeTrace.length - 1)
        if (next >= activeTrace.length - 1) {
          window.setTimeout(() => setTracePlaying(false), 0)
        }
        return next
      })
    }, 650)
    return () => window.clearInterval(timer)
  }, [tracePlaying, activeTrace.length, activeSessionId])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const ctrlOrMeta = event.ctrlKey || event.metaKey
      if (ctrlOrMeta && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        openPalette()
        return
      }
      if (ctrlOrMeta && event.key === ',') {
        event.preventDefault()
        void openSettingsPage()
        return
      }
      if (ctrlOrMeta && event.key.toLowerCase() === 'c' && busy && !isEditableTarget(event.target)) {
        event.preventDefault()
        void handleCancel()
        return
      }
      if (event.key === 'Escape') {
        if (permissionMenuOpen) {
          setPermissionMenuOpen(false)
          return
        }
        if (paletteOpen) {
          closePalette()
          return
        }
        if (renameState.open) {
          closeRenameDialog()
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [busy, paletteOpen, permissionMenuOpen, renameState.open, searchText])

  async function refreshSnapshot(): Promise<void> {
    setStatusText('loading')
    const response = await window.chrysalis.snapshot()
    applySnapshotResponse(response, 'Failed to load snapshot')
  }

  function getConversationTurn(sessionId: string): number {
    const session = sessionId === (snapshotRef.current?.active_session_id || '') ? snapshotRef.current : null
    return countConversationTurns(session?.history || []) + 1
  }

  function updateLiveSession(sessionId: string, updater: (current: LiveSessionState) => LiveSessionState): void {
    if (!sessionId) {
      return
    }
    const current = liveSessionsRef.current
    const next = updater(current[sessionId] || emptyLiveSessionState())
    const sessions = { ...current, [sessionId]: next }
    liveSessionsRef.current = sessions
    setLiveSessions(sessions)
  }

  function appendTraceEvent(sessionId: string, raw: unknown, taskId = ''): void {
    const node = normalizeTraceNode(raw)
    if (!sessionId || !node) {
      return
    }
    const normalizedTaskId = normalizeLiveTaskId(taskId)
    if (normalizedTaskId && !normalizeLiveTaskId(node.task_id || node.taskId)) {
      node.task_id = normalizedTaskId
    }
    if (!node.session_id) {
      node.session_id = sessionId
    }
    const current = traceBySessionRef.current
    const previous = current[sessionId] || []
    const withoutDuplicate = previous.filter((item) => item.id !== node.id)
    const nextSession = [...withoutDuplicate, node].slice(-600)
    const next = { ...current, [sessionId]: nextSession }
    traceBySessionRef.current = next
    setTraceBySession(next)
    if (node.kind === 'task_started') {
      const taskId = traceNodeTaskId(node)
      if (taskId) {
        setSelectedTraceTaskId(taskId)
        setTraceReplayIndex(null)
        setTracePlaying(false)
      }
    }
  }

  function applyTraceSnapshot(sessionId: string, rawNodes: unknown): void {
    if (!sessionId || !Array.isArray(rawNodes)) {
      return
    }
    const incoming = rawNodes
      .map((item) => normalizeTraceNode(item))
      .filter((item): item is TraceEventNode => Boolean(item))
    const current = traceBySessionRef.current
    const previous = current[sessionId] || []
    const byId = new Map<string, TraceEventNode>()
    for (const node of [...incoming, ...previous]) {
      byId.set(node.id, node)
    }
    const nextSession = Array.from(byId.values()).sort(compareTraceNodes).slice(-600)
    if (sameTraceNodes(previous, nextSession)) {
      return
    }
    const next = { ...current, [sessionId]: nextSession }
    traceBySessionRef.current = next
    setTraceBySession(next)
  }

  function seekTrace(index: number): void {
    if (activeTrace.length === 0) {
      return
    }
    setTraceReplayIndex(Math.max(0, Math.min(index, activeTrace.length - 1)))
    setTracePlaying(false)
  }

  function selectTraceTask(taskId: string): void {
    if (!taskId || taskId === effectiveTraceTaskId) {
      return
    }
    setSelectedTraceTaskId(taskId)
    setTraceReplayIndex(null)
    setTracePlaying(false)
  }

  function setTraceLiveMode(): void {
    if (latestTraceTaskId) {
      setSelectedTraceTaskId(latestTraceTaskId)
    }
    setTraceReplayIndex(null)
    setTracePlaying(false)
  }

  function toggleTracePlayback(): void {
    if (activeTrace.length === 0) {
      return
    }
    if (tracePlaying) {
      setTracePlaying(false)
      return
    }
    setTraceReplayIndex((current) => {
      if (current === null || current >= activeTrace.length - 1) {
        return 0
      }
      return Math.max(0, current)
    })
    setTracePlaying(true)
  }

  function clearLiveSession(sessionId: string): void {
    if (!sessionId) {
      return
    }
    const current = liveSessionsRef.current
    if (!current[sessionId]) {
      return
    }
    const next = { ...current }
    delete next[sessionId]
    liveSessionsRef.current = next
    setLiveSessions(next)
  }

  function dropLiveTask(sessionId: string, taskId = ''): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => ({
      ...current,
      taskId: '',
      started: false,
      pausedForUser: false,
      messages: current.messages.filter((message) => {
        if (message.meta === 'pending_user') {
          return false
        }
        return !isTurnProcessMessage(message) || !liveMessageBelongsToTask(message, taskId || current.taskId)
      }).map((message) => (
        message.kind === 'diff' && liveMessageBelongsToTask(message, taskId || current.taskId)
          ? { ...message, status: 'done', meta: 'diff' }
          : message
      )),
    }))
  }

  function settleLiveTaskForPending(sessionId: string, taskId = '', finalText = ''): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      return {
        ...current,
        taskId: '',
        started: true,
        pausedForUser: true,
        streamBuffer: '',
        messages: settleLiveFileChanges(
          settleRunningLiveTools(
            dropStreamingAssistant(current.messages).filter((message) => message.meta !== 'pending_user'),
            'done',
            finalText,
          ),
          resolvedTaskId,
        ),
      }
    })
  }

  function reconcileLiveTasksWithSnapshot(data: RuntimeSnapshot): void {
    const current = liveSessionsRef.current
    const seenSessionIds = new Set<string>()
    const busySessionIds = new Set<string>()

    for (const session of data.sessions || []) {
      if (!session.id) {
        continue
      }
      seenSessionIds.add(session.id)
      if (session.busy || session.task_id) {
        busySessionIds.add(session.id)
      }
    }

    if (data.active_session_id) {
      seenSessionIds.add(data.active_session_id)
      if (data.busy) {
        busySessionIds.add(data.active_session_id)
      }
    }

    let changed = false
    const next = { ...current }
    for (const [sessionId, state] of Object.entries(current)) {
      if (!seenSessionIds.has(sessionId) || busySessionIds.has(sessionId)) {
        continue
      }
      if (state.pausedForUser) {
        continue
      }
      if (!state.taskId && !state.started && !state.streamBuffer) {
        continue
      }
      changed = true
      next[sessionId] = {
        ...state,
        taskId: '',
        started: false,
        pausedForUser: false,
        streamBuffer: '',
        messages: stripTurnProcessMessages(settleLiveFileChanges(settleRunningLiveTools(dropStreamingAssistant(state.messages), 'done', ''), state.taskId)),
      }
    }

    if (changed) {
      liveSessionsRef.current = next
      setLiveSessions(next)
    }
  }

  function mergeGatewayActivitiesIntoLiveSessions(activities: GatewayActivity[]): void {
    if (!Array.isArray(activities) || activities.length === 0) {
      return
    }
    const current = liveSessionsRef.current
    const next = { ...current }
    let changed = false

    for (const activity of activities) {
      const converted = gatewayLiveStateFromActivity(activity)
      if (!converted) {
        continue
      }
      const previous = next[converted.sessionId]
      if (
        previous &&
        previous.taskId === converted.state.taskId &&
        previous.started === converted.state.started &&
        previous.streamBuffer === converted.state.streamBuffer &&
        previous.turn === converted.state.turn &&
        viewMessagesEqual(previous.messages, converted.state.messages)
      ) {
        continue
      }
      next[converted.sessionId] = converted.state
      changed = true
    }

    if (changed) {
      liveSessionsRef.current = next
      setLiveSessions(next)
    }
  }

  function dropAllLiveTasks(): void {
    const current = liveSessionsRef.current
    let changed = false
    const next = { ...current }

    for (const [sessionId, state] of Object.entries(current)) {
      if (!state.taskId && !state.started && !state.streamBuffer) {
        continue
      }
      changed = true
      next[sessionId] = {
        ...state,
        taskId: '',
        started: false,
        pausedForUser: false,
        streamBuffer: '',
        messages: stripTurnProcessMessages(settleLiveFileChanges(settleRunningLiveTools(dropStreamingAssistant(state.messages), 'error', 'Runtime disconnected'), state.taskId)),
      }
    }

    if (changed) {
      liveSessionsRef.current = next
      setLiveSessions(next)
    }
  }

  function liveSessionShouldPersist(sessionId: string): boolean {
    const live = liveSessionsRef.current[sessionId]
    return Boolean(live && (
      live.started ||
      live.taskId ||
      live.streamBuffer ||
      live.messages.some((message) => message.kind === 'diff')
    ))
  }

  function seedMessagesForLiveTaskStart(current: LiveSessionState, taskId: string): ViewMessage[] {
    if (current.pausedForUser) {
      return dropStreamingAssistant(current.messages)
        .filter((message) => message.meta !== 'pending_user')
        .map((message) => tagLiveMessageTask(message, taskId))
    }
    const belongsToStartingTask = current.taskId === PENDING_TASK_ID || (!!taskId && current.taskId === taskId)
    if (!belongsToStartingTask) {
      return []
    }
    const startingTaskId = taskId || current.taskId
    return dropStreamingAssistant(current.messages)
      .filter((message) => {
        if (message.kind === 'diff') {
          return !liveMessageBelongsToTask(message, startingTaskId)
        }
        return message.kind === 'user'
      })
      .map((message) => (
        message.kind === 'diff'
          ? message
          : tagLiveMessageTask(message, startingTaskId)
      ))
  }

  function snapshotContainsLiveTurn(sessionId: string, data: RuntimeSnapshot, finalText = ''): boolean {
    const messages = normalizeHistory(data.history || [])
    const live = liveSessionsRef.current[sessionId]
    const liveUser = live?.messages.find((message) => message.kind === 'user')?.body.trim()
    const hasUser = liveUser
      ? messages.some((message) => message.kind === 'user' && message.body.trim() === liveUser)
      : messages.some((message) => message.kind === 'user' || message.kind === 'assistant')
    const final = finalText.trim()
    const hasFinal = !final || messages.some((message) => {
      if (message.kind !== 'assistant' && message.kind !== 'error') {
        return false
      }
      const body = message.body.trim()
      return body === final || body.includes(final) || final.includes(body)
    })
    return hasUser && hasFinal
  }

  function appendLiveFinal(
    sessionId: string,
    finalText: string,
    kind: 'assistant' | 'error',
    title: string,
    settleStatus: 'done' | 'error' = kind === 'error' ? 'error' : 'done',
    taskId = '',
  ): void {
    if (!sessionId || !finalText.trim()) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const messages = stripTurnProcessMessages(
        settleLiveFileChanges(
          settleRunningLiveTools(dropStreamingAssistant(current.messages), settleStatus, finalText),
          resolvedTaskId,
        ),
      )
      if (messages.some((message) => message.body === finalText && message.kind === kind)) {
        return {
          ...current,
          taskId: '',
          started: false,
          streamBuffer: '',
          messages,
        }
      }
      return {
        ...current,
        taskId: '',
        started: false,
        streamBuffer: '',
        messages: [
          ...messages,
          {
            id: createMessageId(kind === 'error' ? 'error' : 'assistant'),
            kind,
            role: 'assistant',
            title,
            body: finalText,
            turn: current.turn ?? undefined,
            taskId: resolvedTaskId || undefined,
            status: kind === 'error' ? 'error' : undefined,
          },
        ],
      }
    })
  }

  function appendLivePendingRequest(sessionId: string, request: PendingRequestState): void {
    if (!sessionId || (!request.question && request.choices.length === 0)) {
      return
    }
    const body = [
      request.kind === 'permission' && request.tool ? `${request.tool}${request.risk ? ` · ${request.risk}` : ''}` : '',
      request.summary && request.summary !== request.question ? request.summary : '',
      request.question,
    ].filter(Boolean).join('\n')
    updateLiveSession(sessionId, (current) => {
      const resolvedTaskId = liveTaskIdForEvent(current, '')
      const messages = stripTurnProcessMessages(
        settleLiveFileChanges(
          settleRunningLiveTools(dropStreamingAssistant(current.messages), 'done', request.question || request.summary),
          resolvedTaskId,
        ),
      )
      if (messages.some((message) => message.meta === 'pending_user' && message.body === body)) {
        return {
          ...current,
          taskId: '',
          started: false,
          streamBuffer: '',
          messages,
        }
      }
      const pendingTurn = current.turn ?? undefined
      const pendingMessages: ViewMessage[] = [
        {
          id: createMessageId('pending-user'),
          kind: 'warning',
          role: 'assistant',
          title: request.title,
          body: body || 'Waiting for input',
          turn: pendingTurn,
          taskId: resolvedTaskId || undefined,
          meta: 'pending_user',
          status: 'waiting',
        },
      ]
      if (request.question) {
        pendingMessages.push({
          id: createMessageId('pending-question'),
          kind: 'assistant',
          role: 'assistant',
          title: 'Assistant',
          body: request.question,
          turn: pendingTurn,
          taskId: resolvedTaskId || undefined,
          meta: 'pending_user',
        })
      }
      return {
        ...current,
        taskId: '',
        started: false,
        streamBuffer: '',
        messages: [...messages, ...pendingMessages],
      }
    })
  }

  function clearLivePendingMessages(sessionId: string): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => ({
      ...current,
      messages: current.messages.filter((message) => message.meta !== 'pending_user'),
    }))
  }

  function appendLiveStream(sessionId: string, chunk: string, taskId = ''): void {
    if (!sessionId || !chunk) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const streamBuffer = trimLiveStreamBuffer(`${current.streamBuffer || ''}${chunk}`)
      const preview = streamPreviewText(streamBuffer)
      if (!preview) {
        return {
          ...current,
          taskId: resolvedTaskId || current.taskId,
          streamBuffer,
        }
      }
      const messages = [...current.messages]
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const item = messages[index]
        if (item.kind === 'assistant' && item.streaming && liveMessageBelongsToTask(item, resolvedTaskId)) {
          messages[index] = {
            ...item,
            body: preview,
            taskId: resolvedTaskId || item.taskId,
          }
          return {
            ...current,
            taskId: resolvedTaskId || current.taskId,
            messages,
            streamBuffer,
          }
        }
      }
      const streamingMessage: ViewMessage = {
        id: createMessageId(`stream-${sessionId}`),
        kind: 'assistant',
        role: 'assistant',
        title: 'Assistant',
        body: preview,
        streaming: true,
        turn: current.turn ?? undefined,
        taskId: resolvedTaskId || undefined,
      }
      messages.push(streamingMessage)
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        messages,
        streamBuffer,
      }
    })
  }

  function appendLiveGuidance(sessionId: string, guidance: string, taskId = ''): void {
    const text = guidance.trim()
    if (!sessionId || !text) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      if (current.messages.some((message) => (
        message.meta === 'guidance'
        && message.body.trim() === text
        && liveMessageBelongsToTask(message, resolvedTaskId)
      ))) {
        return current
      }
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        started: true,
        messages: [
          ...current.messages,
          {
            id: createMessageId('guidance'),
            kind: 'user',
            role: 'user',
            title: 'Guidance',
            body: text,
            turn: getConversationTurn(sessionId),
            taskId: resolvedTaskId || undefined,
            meta: 'guidance',
          },
        ],
      }
    })
  }

  function beginLiveTool(sessionId: string, tool: string, argsText: string, explicitTurn?: number, taskId = ''): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const thoughtText = compactText(stripSummaryMarkup(current.streamBuffer), 800)
      const fallbackTurn = (current.turn ?? 0) + 1
      const turn = explicitTurn && explicitTurn > 0 ? explicitTurn : fallbackTurn
      const toolMessage: ViewMessage = {
        id: createMessageId(`tool-${sessionId}`),
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
        taskId: resolvedTaskId || undefined,
      }
      const nextMessages: ViewMessage[] = [...dropStreamingAssistant(current.messages), toolMessage]
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        messages: nextMessages,
        streamBuffer: '',
        turn: Math.max(current.turn ?? 0, turn),
      }
    })
  }

  function appendLiveToolStream(sessionId: string, tool: string, chunk: string, taskId = ''): void {
    if (!sessionId || !chunk) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const messages = [...current.messages]
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const item = messages[index]
        if (item.kind === 'tool' && item.status === 'running' && liveMessageBelongsToTask(item, resolvedTaskId)) {
          const streamed = `${item.summary || ''}${chunk}`
          messages[index] = {
            ...item,
            summary: streamed,
            body: compactText(streamed, 180) || item.body,
            taskId: resolvedTaskId || item.taskId,
          }
          return {
            ...current,
            taskId: resolvedTaskId || current.taskId,
            messages,
          }
        }
      }
      return current
    })
  }

  function completeLiveTool(sessionId: string, tool: string, observationText: string, explicitTurn?: number, taskId = ''): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const completedTurn = explicitTurn && explicitTurn > 0 ? explicitTurn : current.turn ?? undefined
      const messages = [...current.messages]
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const item = messages[index]
        if (item.kind === 'tool' && item.status === 'running' && liveMessageBelongsToTask(item, resolvedTaskId)) {
          const details = Array.isArray(item.details) ? [...item.details] : []
          details.push(`Observation:\n${observationText || 'No observation'}`)
          const summary = compactText(observationText || 'done', 48)
          messages[index] = {
            ...item,
            turn: item.turn ?? completedTurn,
            title: `Turn ${item.turn ?? completedTurn ?? 0} - ok ${tool} - ${summary}`,
            body: compactText(observationText || item.body || 'No observation', 180),
            details,
            meta: 'done',
            status: 'done',
            taskId: resolvedTaskId || item.taskId,
          }
          return {
            ...current,
            taskId: resolvedTaskId || current.taskId,
            messages,
            turn: completedTurn ? Math.max(current.turn ?? 0, completedTurn) : current.turn,
          }
        }
      }
      const toolMessage: ViewMessage = {
        id: createMessageId(`tool-${sessionId}`),
        kind: 'tool',
        role: 'assistant',
        title: `Turn ${completedTurn ?? 0} - ok ${tool} - ${compactText(observationText || 'done', 48)}`,
        body: compactText(observationText, 180) || 'No observation',
        details: observationText ? [`Observation:\n${observationText}`] : [],
        meta: 'done',
        status: 'done',
        turn: completedTurn,
        taskId: resolvedTaskId || undefined,
      }
      messages.push(toolMessage)
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        messages,
        turn: completedTurn ? Math.max(current.turn ?? 0, completedTurn) : current.turn,
      }
    })
  }

  function applyLiveSubagentEvent(
    sessionId: string,
    subIndex: number,
    subTask: string,
    kind: string,
    payload: { tool?: string; message?: string; ok?: boolean; error?: string; observation?: string },
    taskId = '',
  ): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const messageId = `subagent-${sessionId}-${resolvedTaskId || 'live'}-${subIndex}`
      const label = `子任务 ${subIndex + 1}`
      const taskPreview = compactText(subTask, 80)

      let title = `${label} - 运行中`
      let status = 'running'
      let meta = 'running'
      let bodyLine = ''
      if (kind === 'started') {
        bodyLine = taskPreview
      } else if (kind === 'progress') {
        bodyLine = compactText(payload.message || '', 160)
      } else if (kind === 'tool_started') {
        bodyLine = `调用 ${payload.tool || 'tool'} ...`
      } else if (kind === 'tool_completed') {
        bodyLine = `${payload.tool || 'tool'} 完成`
      } else if (kind === 'done') {
        status = payload.ok ? 'done' : 'error'
        meta = payload.ok ? 'done' : 'error'
        title = payload.ok ? `${label} - 完成` : `${label} - 失败`
        bodyLine = payload.ok ? '已完成' : compactText(payload.error || '失败', 160)
      }

      const detailLine = (() => {
        if (kind === 'progress' && payload.message) return compactText(payload.message, 200)
        if (kind === 'tool_started' && payload.tool) return `运行 ${payload.tool}`
        if (kind === 'tool_completed' && payload.tool) return `完成 ${payload.tool}`
        if (kind === 'done' && !payload.ok && payload.error) return `Error:\n${payload.error}`
        return ''
      })()

      const messages = [...current.messages]
      const existingIndex = messages.findIndex((item) => item.id === messageId)
      if (existingIndex >= 0) {
        const prev = messages[existingIndex]
        // done 是终态，不被后续滞后事件覆盖
        if ((prev.status === 'done' || prev.status === 'error') && kind !== 'done') {
          return current
        }
        const details = Array.isArray(prev.details) ? [...prev.details] : []
        if (detailLine) {
          details.push(detailLine)
        }
        messages[existingIndex] = {
          ...prev,
          title: status === 'running' ? `${label} - ${taskPreview}` : title,
          body: bodyLine || prev.body,
          details,
          status,
          meta,
          taskId: resolvedTaskId || prev.taskId,
        }
        return { ...current, messages, taskId: resolvedTaskId || current.taskId }
      }

      const subMessage: ViewMessage = {
        id: messageId,
        kind: 'tool',
        role: 'assistant',
        title: status === 'running' ? `${label} - ${taskPreview}` : title,
        body: bodyLine || taskPreview,
        details: detailLine ? [detailLine] : [],
        meta,
        status,
        taskId: resolvedTaskId || undefined,
      }
      messages.push(subMessage)
      return { ...current, messages, taskId: resolvedTaskId || current.taskId }
    })
  }

  function appendLiveDiff(sessionId: string, path: string, diffText: string, explicitTurn?: number, taskId = ''): void {
    if (!sessionId) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const diffTurn = explicitTurn && explicitTurn > 0 ? explicitTurn : current.turn ?? undefined
      const messages = [...current.messages]
      const change = buildFileChange(path, diffText)
      const existingIndex = messages.findIndex((message) => (
        message.kind === 'diff' && liveMessageBelongsToTask(message, resolvedTaskId)
      ))
      if (existingIndex >= 0) {
        const existing = messages[existingIndex]
        const existingChanges = Array.isArray(existing.fileChanges) ? [...existing.fileChanges] : []
        const changeIndex = existingChanges.findIndex((item) => fileChangeKey(item.path) === fileChangeKey(change.path))
        if (changeIndex >= 0) {
          existingChanges[changeIndex] = change
        } else {
          existingChanges.push(change)
        }
        messages[existingIndex] = {
          ...existing,
          title: formatFileChangeSummary(existingChanges),
          body: formatFileChangeBody(existingChanges),
          details: existingChanges.map((item) => item.diff).filter(Boolean),
          path: existingChanges.length === 1 ? existingChanges[0].path : undefined,
          fileChanges: existingChanges,
          turn: existing.turn ?? diffTurn,
          taskId: resolvedTaskId || existing.taskId,
        }
        return {
          ...current,
          taskId: resolvedTaskId || current.taskId,
          messages,
          turn: diffTurn ? Math.max(current.turn ?? 0, diffTurn) : current.turn,
        }
      }
      const changes = [change]
      const diffMessage: ViewMessage = {
        id: createMessageId(`diff-${sessionId}`),
        kind: 'diff',
        role: 'assistant',
        title: formatFileChangeSummary(changes),
        body: formatFileChangeBody(changes),
        meta: 'diff',
        path: change.path,
        details: change.diff ? [change.diff] : [],
        fileChanges: changes,
        turn: diffTurn,
        taskId: resolvedTaskId || undefined,
        status: current.started || current.taskId ? 'running' : undefined,
      }
      messages.push(diffMessage)
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        messages,
        turn: diffTurn ? Math.max(current.turn ?? 0, diffTurn) : current.turn,
      }
    })
  }

  function removeLiveDiff(sessionId: string, path: string, taskId = ''): void {
    if (!sessionId || !path) {
      return
    }
    updateLiveSession(sessionId, (current) => {
      if (!liveTaskEventApplies(current, taskId)) {
        return current
      }
      const resolvedTaskId = liveTaskIdForEvent(current, taskId)
      const messages = [...current.messages]
      const existingIndex = messages.findIndex((message) => (
        message.kind === 'diff' && liveMessageBelongsToTask(message, resolvedTaskId)
      ))
      if (existingIndex < 0) {
        return current
      }
      const existing = messages[existingIndex]
      const existingChanges = Array.isArray(existing.fileChanges) ? existing.fileChanges : []
      const nextChanges = existingChanges.filter((change) => fileChangeKey(change.path) !== fileChangeKey(path))
      if (nextChanges.length === existingChanges.length) {
        return current
      }
      if (nextChanges.length === 0) {
        messages.splice(existingIndex, 1)
      } else {
        messages[existingIndex] = {
          ...existing,
          title: formatFileChangeSummary(nextChanges),
          body: formatFileChangeBody(nextChanges),
          details: nextChanges.map((item) => item.diff).filter(Boolean),
          path: nextChanges.length === 1 ? nextChanges[0].path : undefined,
          fileChanges: nextChanges,
          taskId: resolvedTaskId || existing.taskId,
        }
      }
      return {
        ...current,
        taskId: resolvedTaskId || current.taskId,
        messages,
      }
    })
  }

  function liveSessionHasFileChanges(sessionId: string, taskId = ''): boolean {
    const live = liveSessionsRef.current[sessionId]
    if (!live) {
      return false
    }
    const resolvedTaskId = normalizeLiveTaskId(taskId) || normalizeLiveTaskId(live.taskId)
    return live.messages.some((message) => (
      message.kind === 'diff' && (!resolvedTaskId || liveMessageBelongsToTask(message, resolvedTaskId))
    ))
  }

  function settleLiveFileChanges(messages: ViewMessage[], taskId = ''): ViewMessage[] {
    return messages.map((message) => {
      if (message.kind !== 'diff' || !liveMessageBelongsToTask(message, taskId)) {
        return message
      }
      return {
        ...message,
        status: 'done',
        meta: 'diff',
      }
    })
  }

  function stripTurnProcessMessages(messages: ViewMessage[]): ViewMessage[] {
    return messages.filter((message) => message.meta !== 'pending_user' && !isTurnProcessMessage(message))
  }

  function settleRunningLiveTools(
    messages: ViewMessage[],
    status: 'done' | 'error',
    finalText: string,
  ): ViewMessage[] {
    let changed = false
    const label = status === 'error' ? 'error' : 'done'
    const summary = compactText(finalText, 180)
    const detailLabel = status === 'error' ? 'Final error' : 'Final'
    const settled = messages.map((message) => {
      if (message.kind !== 'tool' || message.status !== 'running') {
        return message
      }
      changed = true
      const details = Array.isArray(message.details) ? [...message.details] : []
      if (summary && !details.some((detail) => detail.includes(summary))) {
        details.push(`${detailLabel}:\n${finalText}`)
      }
      return {
        ...message,
        title: settleRunningTitle(message.title, label),
        body: summary || message.body,
        details,
        meta: status,
        status,
      }
    })
    return changed ? settled : messages
  }

  function settleRunningTitle(title: string, label: string): string {
    const settled = title.replace(/\brunning\b/i, label).replace(/\s+\.\.\.$/, '')
    return settled === title ? `${title} - ${label}` : settled
  }

  async function handleSend(text: string): Promise<void> {
    const task = text.trim()
    const sessionId = activeSessionId
    if (busy) {
      // 任务运行中：有输入即作为引导发送（兼容旧的 /btw 前缀，可省略）。
      const stripped = parseBtwCommand(task)
      const guidance = stripped === null ? task : stripped
      await handleGuideTask(guidance)
      return
    }
    if (!sessionId) {
      setErrorText('No active session')
      return
    }
    if (!task && attachedFiles.length === 0) {
      setErrorText('Enter a task or attach files')
      return
    }
    setErrorText('')
    setNoticeText('')
    setGrowthNotice(null)
    setStatusText('sending')
    if (pendingRequest?.sessionId === sessionId) {
      setPendingRequest(null)
    }
    const userBody = composeDisplayTask(task, attachedFiles)
    const nextTurn = getConversationTurn(sessionId)
    updateLiveSession(sessionId, () => ({
      messages: [
        ...liveFileChangeAnchorMessages(liveSessionsRef.current[sessionId]?.messages || []),
        {
          id: createMessageId('user'),
          kind: 'user',
          role: 'user',
          title: 'User',
          body: userBody,
          turn: nextTurn,
          taskId: PENDING_TASK_ID,
        },
      ],
      streamBuffer: '',
      turn: 0,
      taskId: PENDING_TASK_ID,
      started: true,
    }))

    const response = await window.chrysalis.runTask(task, sessionId)
    if (!response.ok) {
      const error = response.error || 'Failed to start task'
      setErrorText(error)
      setStatusText('error')
      appendLiveFinal(sessionId, error, 'error', '运行失败', 'error', PENDING_TASK_ID)
      const snapshotResponse = await window.chrysalis.snapshot()
      if (snapshotResponse.ok && snapshotResponse.data) {
        applySnapshot(snapshotResponse.data, { preserveLiveSessionId: sessionId, keepError: true })
      }
      return
    }

    updateLiveSession(sessionId, (current) => ({
      ...current,
      taskId: response.data?.task_id || current.taskId,
      messages: response.data?.task_id
        ? tagPendingLiveMessages(current.messages, response.data.task_id)
        : current.messages,
    }))
    setDraftText('')
    setAttachments([])
    setComposerResetKey((value) => value + 1)
    setStatusText('running')
  }

  async function handleGuideTask(guidance: string): Promise<void> {
    const sessionId = activeSessionId
    if (!sessionId) {
      setErrorText('No active session')
      return
    }
    if (!guidance) {
      setErrorText('Enter guidance for the running task')
      return
    }
    setErrorText('')
    setNoticeText('')
    setStatusText('guiding')
    const currentTaskId = activeLiveSession.taskId || activeSession?.task_id || ''
    updateLiveSession(sessionId, (current) => ({
      ...current,
      messages: [
        ...current.messages,
        {
          id: createMessageId('guidance'),
          kind: 'user',
          role: 'user',
          title: 'Guidance',
          body: guidance,
          turn: getConversationTurn(sessionId),
          taskId: currentTaskId || current.taskId,
          meta: 'guidance',
        },
      ],
      started: true,
    }))

    const response = await window.chrysalis.guideTask(sessionId, guidance)
    if (!response.ok) {
      const error = response.error || 'Failed to guide task'
      setErrorText(error)
      setStatusText('error')
      return
    }
    setDraftText('')
    setComposerResetKey((value) => value + 1)
    setNoticeText('Guidance added to the running task')
    setStatusText('running')
  }

  async function handlePendingReply(reply: string): Promise<void> {
    const request = activePendingRequest
    const responseText = reply.trim()
    if (!request || !responseText) {
      return
    }
    setErrorText('')
    setNoticeText('')
    setGrowthNotice(null)
    setStatusText('sending')
    setPendingRequest(null)
    clearLivePendingMessages(request.sessionId)
    setDraftText('')
    setComposerResetKey((value) => value + 1)
    const response = await window.chrysalis.resolvePendingUserAction(request.sessionId, responseText)
    if (!response.ok) {
      const error = response.error || 'Failed to continue task'
      setErrorText(error)
      setStatusText('error')
      setPendingRequest(request)
      setDraftText(responseText)
      const snapshotResponse = await window.chrysalis.snapshot()
      if (snapshotResponse.ok && snapshotResponse.data) {
        applySnapshot(snapshotResponse.data, { preserveLiveSessionId: request.sessionId, keepError: true })
      }
      return
    }
    updateLiveSession(request.sessionId, (current) => ({
      ...current,
      taskId: response.data?.task_id || current.taskId,
      started: true,
      streamBuffer: '',
      messages: response.data?.task_id
        ? current.messages
          .filter((message) => message.meta !== 'pending_user')
          .map((message) => (
            isLiveFileChangeAnchorMessage(message, liveDiffTaskIds(current.messages))
              ? message
              : tagLiveMessageTask(message, response.data?.task_id || '')
          ))
        : current.messages.filter((message) => message.meta !== 'pending_user'),
    }))
    setStatusText('running')
  }

  async function handleComposerSubmit(text: string): Promise<void> {
    if (activePendingRequest) {
      await handlePendingReply(text)
      return
    }
    await handleSend(text)
  }

  async function handleNewSession(): Promise<void> {
    const response = await window.chrysalis.newSession()
    applySnapshotResponse(response, '新建会话失败')
    setPage('chat')
    setPaletteOpen(false)
  }

  async function handleLoadSession(sessionId: string): Promise<void> {
    if (!sessionId) {
      return
    }
    const response = await window.chrysalis.loadSession(sessionId)
    applySnapshotResponse(response, 'Failed to load session')
    setPage('chat')
    setPaletteOpen(false)
  }

  async function handleDeleteSession(sessionId: string): Promise<void> {
    if (!sessionId) {
      return
    }
    const target = allSessions.find((session) => session.id === sessionId)
    if (target?.busy) {
      setErrorText('This session is running and cannot be deleted')
      return
    }
    const response = await window.chrysalis.deleteSession(sessionId)
    applySnapshotResponse(response, 'Failed to delete session')
  }

  async function handleCancel(): Promise<void> {
    const response = await window.chrysalis.cancelTask(activeSessionId)
    if (!response.ok) {
      setErrorText(response.error || 'Failed to cancel task')
      return
    }
    setNoticeText('Cancelling current task')
  }

  async function handleResume(): Promise<void> {
    const sessionId = activeSessionId
    if (!sessionId) {
      return
    }
    const response = await window.chrysalis.resumeTask(sessionId)
    if (!response.ok) {
      setErrorText(response.error || 'Failed to resume task')
      return
    }
    setResumableSessionId((current) => (current === sessionId ? '' : current))
    setErrorText('')
    setNoticeText('Resuming from checkpoint')
  }

  async function openSettingsPage(): Promise<void> {
    setNoticeText('')
    setPaletteOpen(false)
    const response = await window.chrysalis.loadSettingsText()
    if (response.ok && response.data?.text) {
      try {
        const data = JSON.parse(response.data.text) as SettingsSnapshot
        setSettingsForm(createSettingsForm(data))
        setSettingsDirty(false)
      } catch {
        setSettingsForm(createSettingsForm(snapshot?.settings))
        setSettingsDirty(false)
      }
    } else {
      setSettingsForm(createSettingsForm(snapshot?.settings))
      setSettingsDirty(false)
    }
    setPage('settings')
  }

  async function saveSettingsPage(): Promise<void> {
    const response = await window.chrysalis.saveSettingsText(JSON.stringify(settingsPayload(settingsForm)))
    applySnapshotResponse(response, '保存设置失败')
    if (response.ok) {
      setSettingsDirty(false)
      setPage('chat')
    }
  }

  async function resetSettingsPage(): Promise<void> {
    const response = await window.chrysalis.resetSettings()
    applySnapshotResponse(response, '重置设置失败')
    setSettingsDirty(false)
    if (response.ok) {
      setSettingsForm(createSettingsForm(response.data?.settings))
    }
  }

  async function handlePermissionLevelChange(level: PermissionLevel): Promise<void> {
    const nextLevel = normalizePermissionLevel(level)
    if (nextLevel === permissionLevel) {
      return
    }
    setErrorText('')
    const response = await window.chrysalis.setPermissionLevel(nextLevel)
    applySnapshotResponse(response, 'Failed to update permission level')
    if (response.ok) {
      setPermissionMenuOpen(false)
      setSettingsForm((current) => ({ ...current, permissionLevel: nextLevel }))
      setNoticeText(`Permission set to ${permissionLevelLabel(nextLevel)}`)
    }
  }

  function validateCronForm(): boolean {
    if (!cronForm.noAgent && !cronForm.prompt.trim()) {
      setErrorText('智能体定时任务需要填写 Prompt')
      return false
    }
    if (cronForm.noAgent && !cronForm.script.trim()) {
      setErrorText('仅运行脚本时需要填写脚本路径')
      return false
    }
    return true
  }

  function resetCronForm(): void {
    setCronEditingJobId('')
    setCronForm(createCronForm())
  }

  function handleEditCronJob(job: CronJob): void {
    if (job.state?.running) {
      setErrorText('运行中的定时任务暂时不能编辑')
      return
    }
    setErrorText('')
    setCronSelectedJobId(job.id)
    setCronEditingJobId(job.id)
    setCronForm(createCronFormFromJob(job))
  }

  async function handleCreateCronJob(): Promise<void> {
    if (!validateCronForm()) {
      return
    }
    const existingJobIds = new Set((snapshotRef.current?.cron?.jobs || []).map((job) => job.id))
    const response = await window.chrysalis.createCronJob(buildCronSpec(cronForm))
    applySnapshotResponse(response, '创建定时任务失败')
    if (response.ok) {
      const jobs = response.data?.cron?.jobs || []
      const requestedId = cronForm.id.trim()
      const createdJob = jobs.find((job) => !existingJobIds.has(job.id))
        || jobs.find((job) => requestedId && job.id === requestedId)
        || null
      if (createdJob) {
        setCronSelectedJobId(createdJob.id)
      }
      setNoticeText(cronJobNotice('定时任务已创建', createdJob))
      resetCronForm()
    }
  }

  async function handleUpdateCronJob(): Promise<void> {
    const jobId = cronEditingJobId
    if (!jobId || !validateCronForm()) {
      return
    }
    const response = await window.chrysalis.updateCronJob(jobId, buildCronSpec(cronForm))
    applySnapshotResponse(response, '更新定时任务失败')
    if (response.ok) {
      const updatedJob = (response.data?.cron?.jobs || []).find((job) => job.id === jobId) || null
      setNoticeText(cronJobNotice('定时任务已更新', updatedJob))
      setCronSelectedJobId(jobId)
      resetCronForm()
    }
  }

  async function handleTickCron(): Promise<void> {
    const response = await window.chrysalis.tickCron()
    applySnapshotResponse(response, '定时任务检查失败')
    if (response.ok) {
      setNoticeText('定时任务已检查')
    }
  }

  async function handleStartCronDaemon(): Promise<void> {
    const intervalSeconds = Math.max(1, parseIntOrFallback(cronDaemonInterval, 60))
    setCronDaemonInterval(String(intervalSeconds))
    const response = await window.chrysalis.startCronDaemon(intervalSeconds)
    applySnapshotResponse(response, '启动定时任务守护进程失败')
    if (response.ok) {
      setNoticeText('定时任务守护进程已启动')
    }
  }

  async function handleStopCronDaemon(): Promise<void> {
    const response = await window.chrysalis.stopCronDaemon()
    applySnapshotResponse(response, '停止定时任务守护进程失败')
    if (response.ok) {
      setNoticeText('定时任务守护进程已停止')
    }
  }

  async function handleRunCronJob(jobId: string): Promise<void> {
    if (!jobId) {
      return
    }
    const response = await window.chrysalis.runCronJob(jobId)
    applySnapshotResponse(response, '运行定时任务失败')
    if (response.ok) {
      setNoticeText('定时任务已加入运行队列')
    }
  }

  async function handleToggleCronJob(job: CronJob): Promise<void> {
    const jobId = job.id
    if (!jobId) {
      return
    }
    const response = job.enabled === false
      ? await window.chrysalis.resumeCronJob(jobId)
      : await window.chrysalis.pauseCronJob(jobId)
    applySnapshotResponse(response, job.enabled === false ? '启用定时任务失败' : '停用定时任务失败')
    if (response.ok) {
      setNoticeText(job.enabled === false ? '定时任务已启用' : '定时任务已停用')
    }
  }

  async function handleRemoveCronJob(jobId: string): Promise<void> {
    if (!jobId || !window.confirm('删除这个定时任务？')) {
      return
    }
    const response = await window.chrysalis.removeCronJob(jobId)
    applySnapshotResponse(response, '删除定时任务失败')
    if (response.ok) {
      setNoticeText('定时任务已删除')
    }
  }

  function applyGatewayResponse(response: RuntimeResponse<RuntimeSnapshot>, fallbackError: string): boolean {
    if (response.data) {
      applySnapshot(response.data)
    }
    if (!response.ok) {
      setErrorText(response.error || fallbackError)
      return false
    }
    setErrorText('')
    return true
  }

  function gatewayPlatformLabel(platformId: string): string {
    return gatewayPlatforms.find((platform) => platform.id === platformId)?.label || platformId
  }

  async function handleGatewayStart(platformId: string): Promise<void> {
    const sharedGroups = platformId === 'qq_personal'
    const response = await window.chrysalis.gatewayStart(platformId, sharedGroups)
    const ok = applyGatewayResponse(response, '启动网关失败')
    if (ok) {
      setNoticeText(`${gatewayPlatformLabel(platformId)} 网关已启动${sharedGroups ? '（群共享会话）' : ''}`)
      void handleGatewayLogs(platformId)
    }
  }

  async function handleGatewayStop(platformId: string): Promise<void> {
    const response = await window.chrysalis.gatewayStop(platformId)
    const ok = applyGatewayResponse(response, '停止网关失败')
    if (ok) {
      setNoticeText(`${gatewayPlatformLabel(platformId)} 网关已停止`)
      void handleGatewayLogs(platformId)
    }
  }

  async function handleGatewayRefresh(): Promise<void> {
    const response = await window.chrysalis.gatewayRefresh()
    const ok = applyGatewayResponse(response, '刷新连接状态失败')
    if (ok) {
      setNoticeText('连接状态已刷新')
    }
  }

  async function handleGatewayLogs(platformId: string): Promise<void> {
    setGatewayLog((current) => ({
      platformId,
      loading: true,
      text: current.platformId === platformId ? current.text : '',
      path: current.platformId === platformId ? current.path : '',
    }))
    const response = await window.chrysalis.gatewayLogs(platformId)
    if (!response.ok || !response.data) {
      setGatewayLog((current) => ({ ...current, platformId, loading: false }))
      setErrorText(response.error || '读取网关日志失败')
      return
    }
    const data = response.data as GatewayLogResponse
    setGatewayLog({
      platformId,
      loading: false,
      text: data.log || '',
      path: data.log_file || '',
    })
  }

  async function handleGatewayCopy(platform: GatewayPlatformSnapshot): Promise<void> {
    const text = gatewayCopyText(platform)
    if (!text) {
      setNoticeText('当前没有可复制的错误信息')
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      setNoticeText('错误信息已复制')
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : '复制失败')
    }
  }

  async function handleAttachFiles(paths?: string[]): Promise<void> {
    if (activeSessionBusy) {
      return
    }
    const selected = paths || (await window.chrysalis.openFiles())
    if (!selected.length) {
      return
    }
    let nextSnapshot: RuntimeSnapshot | null = null
    for (const path of selected) {
      const response = await window.chrysalis.addAttachment(path)
      if (response.ok && response.data) {
        nextSnapshot = response.data
      } else if (!response.ok) {
        setErrorText(response.error || '附件添加失败')
      }
    }
    if (nextSnapshot) {
      applySnapshot(nextSnapshot)
    }
  }

  async function handleRemoveAttachment(index: number): Promise<void> {
    const response = await window.chrysalis.removeAttachment(index)
    applySnapshotResponse(response, '移除附件失败')
  }

  async function handleClearAttachments(): Promise<void> {
    const response = await window.chrysalis.clearAttachments()
    applySnapshotResponse(response, '清空附件失败')
  }

  async function handleToggleSessionPinned(sessionId: string): Promise<void> {
    if (!sessionId) {
      return
    }
    const response = await window.chrysalis.toggleSessionPinned(sessionId)
    applySnapshotResponse(response, '切换置顶失败')
  }

  async function handleRenameSession(sessionId: string, title: string): Promise<void> {
    if (!sessionId || !title.trim()) {
      setErrorText('Session title cannot be empty')
      return
    }
    try {
      const response = await window.chrysalis.renameSession(sessionId, title.trim())
      applySnapshotResponse(response, 'Rename failed')
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Rename failed')
    }
  }

  async function handleEvent(event: RuntimeEvent): Promise<void> {
    if (event.event === 'runtime_ready') {
      const data = event.snapshot as RuntimeSnapshot | undefined
      if (data) {
        applySnapshot(data)
      }
      setReady(true)
      return
    }

    if (event.event === 'trace') {
      appendTraceEvent(String(event.session_id || ''), event.trace, String(event.task_id || ''))
      return
    }

    if (event.event === 'runtime_stderr') {
      const text = compactText(stripAnsi(String(event.text || '')), 220)
      if (text) {
        setNoticeText(text)
      }
      return
    }

    if (event.event === 'runtime_disconnected') {
      setErrorText('Runtime disconnected')
      setStatusText('disconnected')
      dropAllLiveTasks()
      return
    }

    if (event.event === 'status') {
      const sessionId = String(event.session_id || '')
      const activeSessionId = snapshotRef.current?.active_session_id || ''
      if (sessionId && sessionId !== activeSessionId) {
        return
      }
      setStatusText(String(event.status || ''))
      return
    }

    if (event.event === 'working') {
      const sessionId = String(event.session_id || '')
      const activeSessionId = snapshotRef.current?.active_session_id || ''
      if (sessionId && sessionId !== activeSessionId) {
        return
      }
      setWorking((event.snapshot as WorkingSnapshot) || emptyWorking)
      return
    }

    const eventSessionId = String(event.session_id || '')
    const eventTaskId = String(event.task_id || '')

    if (event.event === 'thinking') {
      const content = String(event.content || '')
      if (content && eventSessionId) {
        appendLiveStream(eventSessionId, content, eventTaskId)
      }
      return
    }

    if (event.event === 'stream') {
      const content = String(event.content || '')
      if (content && eventSessionId) {
        appendLiveStream(eventSessionId, content, eventTaskId)
      }
      return
    }

    if (event.event === 'guidance') {
      const content = String(event.content || '')
      if (content && eventSessionId) {
        appendLiveGuidance(eventSessionId, content, eventTaskId)
      }
      return
    }

    if (event.event === 'tool_started') {
      if (event.snapshot) {
        applySnapshot(event.snapshot as RuntimeSnapshot, {
          preserveLiveSessionId: eventSessionId,
          keepError: Boolean(eventSessionId && liveSessionsRef.current[eventSessionId]?.messages.length),
        })
      }
      if (eventSessionId) {
        beginLiveTool(eventSessionId, String(event.tool || 'tool'), stringifyValue(event.args), Number(event.turn || 0), eventTaskId)
      }
      return
    }

    if (event.event === 'tool_stream') {
      if (eventSessionId) {
        appendLiveToolStream(eventSessionId, String(event.tool || 'tool'), String(event.content || ''), eventTaskId)
      }
      return
    }

    if (event.event === 'tool_completed') {
      if (eventSessionId) {
        completeLiveTool(eventSessionId, String(event.tool || 'tool'), stringifyValue(event.observation), Number(event.turn || 0), eventTaskId)
      }
      return
    }

    if (event.event === 'subagent') {
      if (eventSessionId) {
        applyLiveSubagentEvent(
          eventSessionId,
          Number(event.sub_index || 0),
          String(event.task || ''),
          String(event.kind || ''),
          {
            tool: event.tool ? String(event.tool) : undefined,
            message: event.message ? String(event.message) : undefined,
            ok: typeof event.ok === 'boolean' ? event.ok : undefined,
            error: event.error ? String(event.error) : undefined,
            observation: event.observation ? stringifyValue(event.observation) : undefined,
          },
          eventTaskId,
        )
      }
      return
    }

    if (event.event === 'file_diff') {
      if (eventSessionId) {
        if (event.clear) {
          removeLiveDiff(eventSessionId, String(event.path || 'unknown'), eventTaskId)
        } else {
          appendLiveDiff(eventSessionId, String(event.path || 'unknown'), String(event.diff || ''), Number(event.turn || 0), eventTaskId)
        }
      }
      return
    }

    if (event.event === 'task_started') {
      const sessionId = String(event.session_id || '')
      const taskId = String(event.task_id || '')
      const currentActiveSessionId = snapshotRef.current?.active_session_id || ''
      if (event.snapshot) {
        applySnapshot(event.snapshot as RuntimeSnapshot, { preserveLiveSessionId: sessionId })
      }
      if (sessionId) {
        updateLiveSession(sessionId, (current) => ({
          ...current,
          messages: seedMessagesForLiveTaskStart(current, taskId),
          streamBuffer: '',
          taskId: taskId || current.taskId,
          started: true,
          pausedForUser: false,
          turn: 0,
        }))
        if (sessionId === currentActiveSessionId) {
          setStatusText(String(event.status || 'running'))
        }
        setResumableSessionId((current) => (current === sessionId ? '' : current))
      }
      return
    }

    if (event.event === 'task_resumable') {
      const sessionId = String(event.session_id || '')
      if (sessionId) {
        setResumableSessionId(sessionId)
      }
      return
    }

    if (event.event === 'task_done') {
      const sessionId = String(event.session_id || '')
      const result = event.result && typeof event.result === 'object'
        ? event.result as Record<string, unknown>
        : {}
      const needsUser = Boolean(result.need_user)
      const ok = result.ok !== false && !result.error
      const finalText = stringifyValue(result.final || result.question || result.error || result.reason || result.message || '')
      const finalKind = (!ok && !needsUser) ? 'error' : 'assistant'
      const data = event.snapshot as RuntimeSnapshot | undefined
      const historyHasVisibleTurn = data ? snapshotContainsLiveTurn(sessionId, data, ok ? finalText : '') : false
      const hasLiveFileChanges = liveSessionHasFileChanges(sessionId, eventTaskId)
      const shouldKeepLiveResult = Boolean(sessionId && finalText && !needsUser && (!ok || !historyHasVisibleTurn))
      const growth = createGrowthNotice(result.review_summary, eventTaskId)
      if (sessionId) {
        if (needsUser) {
          settleLiveTaskForPending(sessionId, String(event.task_id || ''), finalText)
        } else if (shouldKeepLiveResult) {
          appendLiveFinal(
            sessionId,
            finalText,
            finalKind,
            finalKind === 'error' ? 'Error' : 'Assistant',
            finalKind === 'error' ? 'error' : 'done',
            String(event.task_id || ''),
          )
        } else if (historyHasVisibleTurn) {
          if (hasLiveFileChanges) {
            dropLiveTask(sessionId, String(event.task_id || ''))
          } else {
            clearLiveSession(sessionId)
          }
        } else {
          dropLiveTask(sessionId, String(event.task_id || ''))
        }
        if (!ok && !needsUser && finalText) {
          setErrorText(finalText)
        } else {
          setErrorText('')
        }
      }
      if (data) {
        applySnapshot(data, {
          preserveLiveSessionId: needsUser || shouldKeepLiveResult || hasLiveFileChanges ? sessionId : '',
          keepError: shouldKeepLiveResult && finalKind === 'error',
        })
      } else if (sessionId && sessionId === (snapshotRef.current?.active_session_id || '')) {
        void refreshSnapshot()
      }
      if (growth && sessionId === (snapshotRef.current?.active_session_id || sessionId)) {
        setGrowthNotice(growth)
        setReviewFilter('pending')
        setSelectedReviewId(growth.itemIds[0] || '')
        if (ok && !needsUser) {
          setNoticeText(growth.title)
        }
      }
      return
    }

    if (event.event === 'session_changed') {
      const data = event.snapshot as RuntimeSnapshot | undefined
      if (data) {
        applySnapshot(data, { preserveLiveSessionId: String(event.session_id || '') })
      } else {
        void refreshSnapshot()
      }
      return
    }

    if (
      event.event === 'attachments_changed' ||
      event.event === 'workspace_changed' ||
      event.event === 'settings_changed' ||
      event.event === 'cron_changed' ||
      event.event === 'review_changed' ||
      event.event === 'gateway_changed'
    ) {
      const data = event.snapshot as RuntimeSnapshot | undefined
      if (data) {
        const sessionId = data.active_session_id || ''
        applySnapshot(data, {
          preserveLiveSessionId: liveSessionShouldPersist(sessionId) ? sessionId : '',
        })
      }
    }
  }

  function applySnapshotResponse<T>(response: RuntimeResponse<T>, fallbackError: string): void {
    if (!response.ok || !response.data) {
      setErrorText(response.error || fallbackError)
      return
    }
    const data = response.data as unknown as RuntimeSnapshot
    applySnapshot(data)
  }

  function applySnapshot(data: RuntimeSnapshot, options: { preserveLiveSessionId?: string; keepError?: boolean } = {}): void {
    const previousSessionId = snapshotRef.current?.active_session_id || ''
    const activeSnapshotSessionId = data.active_session_id || ''
    const preserveLiveSessionId = options.preserveLiveSessionId
      || (data.busy && activeSnapshotSessionId && liveSessionShouldPersist(activeSnapshotSessionId) ? activeSnapshotSessionId : '')
    const nextPending = createPendingRequest(data.active_session_id || '', data.pending_user_action)
    const preservePendingSession = Boolean(nextPending && nextPending.sessionId === data.active_session_id)
    snapshotRef.current = data
    setSnapshot(data)
    const historySignature = runtimeHistorySignature(data.active_session_id || '', data.history || [])
    if (
      historyCacheRef.current.sessionId !== data.active_session_id ||
      historyCacheRef.current.signature !== historySignature
    ) {
      historyCacheRef.current = {
        sessionId: data.active_session_id || '',
        signature: historySignature,
        messages: normalizeHistory(data.history || []),
      }
    }
    setHistoryMessages(historyCacheRef.current.messages)
    applyTraceSnapshot(data.active_session_id || '', data.trace || [])
    setWorking(data.working || emptyWorking)
    setAttachments(data.attachments || [])
    setPendingRequest(nextPending)
    setResumableSessionId(data.resumable_session ? (data.active_session_id || '') : '')
    setStatusText(data.busy ? 'running' : 'ready')
    if (!options.keepError) {
      setErrorText('')
    }
    setReady(true)
    reconcileLiveTasksWithSnapshot(data)
    if (data.busy && preserveLiveSessionId === data.active_session_id) {
      const activeSummary = (data.sessions || []).find((session) => session.id === data.active_session_id)
      updateLiveSession(data.active_session_id, (current) => ({
        ...current,
        taskId: activeSummary?.task_id || current.taskId || '',
        started: current.started || Boolean(activeSummary?.task_id),
        turn: current.turn ?? 0,
      }))
    } else if (!preservePendingSession && (!preserveLiveSessionId || preserveLiveSessionId !== data.active_session_id)) {
      clearLiveSession(data.active_session_id)
    }
    mergeGatewayActivitiesIntoLiveSessions(data.gateway?.activities || [])
    if (previousSessionId !== data.active_session_id) {
      setDraftText(data.draft_text || '')
      setComposerResetKey((value) => value + 1)
    }
  }

  function dropStreamingAssistant(messages: ViewMessage[]): ViewMessage[] {
    const next = [...messages]
    for (let index = next.length - 1; index >= 0; index -= 1) {
      const item = next[index]
      if (item.kind === 'assistant' && item.streaming) {
        next.splice(index, 1)
        break
      }
    }
    return next
  }

  function openPalette(): void {
    setPaletteText(searchText)
    setPaletteOpen(true)
    window.setTimeout(() => paletteInputRef.current?.focus(), 0)
  }

  function closePalette(): void {
    setPaletteOpen(false)
    void window.chrysalis.setSessionFilter(searchText)
  }

  function updateSearchText(value: string): void {
    setSearchText(value)
    void window.chrysalis.setSessionFilter(value)
  }

  function updatePaletteText(value: string): void {
    setPaletteText(value)
    void window.chrysalis.setSessionFilter(value)
  }

  function startRenameCurrentSession(): void {
    if (!activeSession) {
      return
    }
    setRenameState({
      open: true,
      sessionId: activeSession.id,
      title: activeSession.title || '',
    })
    window.setTimeout(() => renameInputRef.current?.focus(), 0)
  }

  function closeRenameDialog(): void {
    setRenameState({ open: false, sessionId: '', title: '' })
  }

  function updateRenameTitle(title: string): void {
    setRenameState((current) => ({ ...current, title }))
    if (title.trim()) {
      setErrorText('')
    }
  }

  function submitRename(): void {
    const title = renameState.title.trim()
    if (!title) {
      setErrorText('Session title cannot be empty')
      return
    }
    void handleRenameSession(renameState.sessionId, title)
    closeRenameDialog()
  }

  function openChatPage(): void {
    setPaletteOpen(false)
    setPage('chat')
  }

  function openCronPage(): void {
    setPaletteOpen(false)
    setPage('cron')
    setCronDaemonInterval(String(snapshotRef.current?.cron?.daemon?.interval_seconds ?? 60))
    const jobs = snapshotRef.current?.cron?.jobs || []
    if (jobs.length > 0) {
      setCronSelectedJobId((current) => current || jobs[0].id)
    }
  }

  function openReviewPage(itemId = ''): void {
    setPaletteOpen(false)
    setPage('reviews')
    if (itemId) {
      setSelectedReviewId(itemId)
    }
  }

  function openReviewCandidate(itemIds: string[]): void {
    setReviewFilter('pending')
    setSelectedReviewId(itemIds[0] || '')
    setGrowthNotice(null)
    openReviewPage(itemIds[0] || '')
  }

  function openGatewayPage(): void {
    setPaletteOpen(false)
    setPage('gateway')
    void handleGatewayRefresh()
  }

  function currentReviewPatch(): ReviewPatch {
    return {
      title: reviewDraft.title,
      target: reviewDraft.target,
      description: reviewDraft.description,
      content: reviewDraft.content,
    }
  }

  async function handleReviewUpdate(): Promise<void> {
    const item = selectedReviewItem
    if (!item) {
      return
    }
    setReviewSaving('save')
    const response = await window.chrysalis.reviewUpdate(item.id, currentReviewPatch())
    applySnapshotResponse(response, 'Failed to save review item')
    if (response.ok) {
      setNoticeText('审核项已保存')
    }
    setReviewSaving('')
  }

  async function handleReviewApprove(): Promise<void> {
    const item = selectedReviewItem
    if (!item) {
      return
    }
    setReviewSaving('approve')
    const response = await window.chrysalis.reviewApprove(item.id, currentReviewPatch())
    applySnapshotResponse(response, 'Failed to approve review item')
    if (response.ok) {
      setNoticeText(item.kind === 'skill' || currentReviewPatch().target === 'sop' ? '技能笔记已批准' : '记忆已批准')
      setReviewFilter('pending')
    }
    setReviewSaving('')
  }

  async function handleReviewDiscard(): Promise<void> {
    const item = selectedReviewItem
    if (!item) {
      return
    }
    setReviewSaving('discard')
    const response = await window.chrysalis.reviewDiscard(item.id)
    applySnapshotResponse(response, 'Failed to discard review item')
    if (response.ok) {
      setNoticeText(item.kind === 'skill' ? '技能笔记文件已删除' : '记忆候选已丢弃')
      setReviewFilter('pending')
    }
    setReviewSaving('')
  }

  function handleDropEvent(event: React.DragEvent<HTMLElement>): void {
    event.preventDefault()
    event.stopPropagation()
    setDragDepth(0)
    if (busy) {
      return
    }
    const paths = extractDropPaths(event)
    if (paths.length > 0) {
      void handleAttachFiles(paths)
    }
  }

  function handleDragEnter(event: React.DragEvent<HTMLElement>): void {
    if (busy) {
      return
    }
    const paths = extractDropPaths(event)
    if (paths.length === 0) {
      return
    }
    event.preventDefault()
    setDragDepth((value) => value + 1)
  }

  function handleDragOver(event: React.DragEvent<HTMLElement>): void {
    if (busy) {
      return
    }
    const paths = extractDropPaths(event)
    if (paths.length === 0) {
      return
    }
    event.preventDefault()
  }

  function handleDragLeave(event: React.DragEvent<HTMLElement>): void {
    if (busy) {
      return
    }
    event.preventDefault()
    setDragDepth((value) => Math.max(0, value - 1))
  }

  function handleWorkspaceResizeStart(edge: ResizeEdge, event: ReactPointerEvent<HTMLDivElement>): void {
    const rect = workspaceRef.current?.getBoundingClientRect()
    if (!rect) {
      return
    }
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    workspaceResizeRef.current = {
      edge,
      startX: event.clientX,
      sidebarWidth: workspaceLayout.sidebarWidth,
      inspectorWidth: workspaceLayout.inspectorWidth,
      containerWidth: rect.width,
    }
    setResizingEdge(edge)
  }

  function handleWorkspaceResizeMove(event: ReactPointerEvent<HTMLDivElement>): void {
    const drag = workspaceResizeRef.current
    if (!drag) {
      return
    }
    event.preventDefault()
    const delta = event.clientX - drag.startX
    const inspectorVisible = drag.containerWidth > 1120
    if (drag.edge === 'left') {
      const rightWidth = inspectorVisible ? drag.inspectorWidth : 0
      const handleSpace = inspectorVisible ? WORKSPACE_RESIZER_WIDTH * 2 : WORKSPACE_RESIZER_WIDTH
      const maxSidebar = Math.min(
        WORKSPACE_RESIZE_LIMITS.sidebarMax,
        drag.containerWidth - rightWidth - WORKSPACE_RESIZE_LIMITS.centerMin - handleSpace,
      )
      const sidebarWidth = clampNumber(
        drag.sidebarWidth + delta,
        WORKSPACE_RESIZE_LIMITS.sidebarMin,
        maxSidebar,
      )
      setWorkspaceLayout((current) => ({ ...current, sidebarWidth }))
      return
    }

    const maxInspector = Math.min(
      WORKSPACE_RESIZE_LIMITS.inspectorMax,
      drag.containerWidth - drag.sidebarWidth - WORKSPACE_RESIZE_LIMITS.centerMin - WORKSPACE_RESIZER_WIDTH * 2,
    )
    const inspectorWidth = clampNumber(
      drag.inspectorWidth - delta,
      WORKSPACE_RESIZE_LIMITS.inspectorMin,
      maxInspector,
    )
    setWorkspaceLayout((current) => ({ ...current, inspectorWidth }))
  }

  function handleWorkspaceResizeEnd(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!workspaceResizeRef.current) {
      return
    }
    workspaceResizeRef.current = null
    setResizingEdge(null)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function resetWorkspaceResize(edge: ResizeEdge): void {
    setWorkspaceLayout((current) => edge === 'left'
      ? { ...current, sidebarWidth: DEFAULT_WORKSPACE_LAYOUT.sidebarWidth }
      : { ...current, inspectorWidth: DEFAULT_WORKSPACE_LAYOUT.inspectorWidth })
  }

  const widePage = page !== 'chat'

  const cronPage = (
            <div className="settings-page cron-page">
              <div className="settings-head cron-head">
                <div className="page-title-stack">
                  <div className="page-kicker"><Clock3 size={14} /> 定时任务</div>
                  <div className="panel-title">定时任务</div>
                  <div className="panel-subtitle">{cronDaemonLabel(cronSnapshot.daemon)} · {cronJobs.length} 个任务</div>
                </div>
                <div className="panel-spacer" />
                <label className="cron-interval" title="后台轮询间隔，单位秒">
                  <span>轮询间隔</span>
                  <input
                    value={cronDaemonInterval}
                    inputMode="numeric"
                    onChange={(event) => setCronDaemonInterval(event.currentTarget.value)}
                  />
                </label>
                <button className="icon-button" type="button" onClick={() => void handleTickCron()} title="立即检查" aria-label="立即检查">
                  <RefreshCw size={15} />
                </button>
                {cronSnapshot.daemon?.running ? (
                  <button className="icon-button danger" type="button" onClick={() => void handleStopCronDaemon()} title="停止守护进程" aria-label="停止守护进程">
                    <Square size={15} />
                  </button>
                ) : (
                  <button className="icon-button primary" type="button" onClick={() => void handleStartCronDaemon()} title="启动守护进程" aria-label="启动守护进程">
                    <Play size={15} />
                  </button>
                )}
                <button className="icon-button" type="button" onClick={openChatPage} title="返回对话" aria-label="返回对话">
                  <ArrowLeft size={15} />
                </button>
              </div>

              <div className="settings-body cron-body">
                <SettingsSection title={cronEditingJobId ? '编辑任务' : '创建任务'}>
                  <div className="grid-two">
                    <SettingsField
                      label="任务 ID"
                      placeholder="daily-report"
                      value={cronForm.id}
                      disabled={Boolean(cronEditingJobId)}
                      onChange={(value) => setCronForm((current) => ({ ...current, id: value }))}
                    />
                    <SettingsField
                      label="任务名称"
                      placeholder="例如：每日摘要"
                      value={cronForm.name}
                      onChange={(value) => setCronForm((current) => ({ ...current, name: value }))}
                    />
                  </div>

                  <div className="switch-row">
                    <div className="switch-copy">
                      <div className="field-label">仅运行脚本</div>
                      <div className="field-hint">不向智能体发送 Prompt，只执行本地脚本。</div>
                    </div>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={cronForm.noAgent}
                        onChange={(event) => {
                          const noAgent = event.currentTarget.checked
                          setCronForm((current) => ({ ...current, noAgent }))
                        }}
                      />
                      <span />
                    </label>
                  </div>

                  {cronForm.noAgent ? (
                    <div className="grid-two">
                      <SettingsField
                        label="脚本路径"
                        placeholder="scripts/check.py"
                        value={cronForm.script}
                        onChange={(value) => setCronForm((current) => ({ ...current, script: value }))}
                      />
                      <SettingsField
                        label="工作目录"
                        placeholder="可选"
                        value={cronForm.workdir}
                        onChange={(value) => setCronForm((current) => ({ ...current, workdir: value }))}
                      />
                    </div>
                  ) : (
                    <>
                      <SettingsField
                        label="智能体 Prompt"
                        placeholder="描述希望智能体定时完成的任务"
                        value={cronForm.prompt}
                        multiline
                        onChange={(value) => setCronForm((current) => ({ ...current, prompt: value }))}
                      />
                      <div className="grid-two">
                        <SettingsField
                          label="附加脚本"
                          placeholder="可选"
                          value={cronForm.script}
                          onChange={(value) => setCronForm((current) => ({ ...current, script: value }))}
                        />
                        <SettingsField
                          label="工作目录"
                          placeholder="可选"
                          value={cronForm.workdir}
                          onChange={(value) => setCronForm((current) => ({ ...current, workdir: value }))}
                        />
                      </div>
                    </>
                  )}
                </SettingsSection>

                <SettingsSection title="时间计划">
                  <div className="grid-three">
                    <SettingsField
                      label="计划类型"
                      placeholder="once"
                      value={cronForm.scheduleType}
                      options={[
                        { value: 'once', label: '一次' },
                        { value: 'periodic', label: '周期' },
                      ]}
                      onChange={(value) => setCronForm((current) => ({ ...current, scheduleType: value === 'periodic' ? 'periodic' : 'once' }))}
                    />
                    {cronForm.scheduleType === 'once' ? (
                      <SettingsField
                        label="执行时间"
                        placeholder="YYYY-MM-DDTHH:mm"
                        value={cronForm.runAt}
                        inputType="datetime-local"
                        onChange={(value) => setCronForm((current) => ({ ...current, runAt: value }))}
                      />
                    ) : (
                      <SettingsField
                        label="周期"
                        placeholder="daily"
                        value={cronForm.period}
                        options={[
                          { value: 'daily', label: '每天' },
                          { value: 'weekly', label: '每周' },
                          { value: 'monthly', label: '每月' },
                          { value: 'yearly', label: '每年' },
                          { value: 'interval', label: '固定间隔' },
                        ]}
                        onChange={(value) => setCronForm((current) => ({ ...current, period: value }))}
                      />
                    )}
                    {cronForm.scheduleType === 'periodic' ? (
                      <SettingsField
                        label="开始时间"
                        placeholder="YYYY-MM-DDTHH:mm"
                        value={cronForm.startAt}
                        inputType="datetime-local"
                        onChange={(value) => setCronForm((current) => ({ ...current, startAt: value }))}
                      />
                    ) : (
                      <div />
                    )}
                  </div>

                  {cronForm.scheduleType === 'periodic' && cronForm.period !== 'interval' ? (
                    <div className="grid-three">
                      <SettingsField
                        label="时间"
                        placeholder="HH:mm"
                        value={cronForm.time}
                        inputType="time"
                        onChange={(value) => setCronForm((current) => ({ ...current, time: value }))}
                      />
                      {cronForm.period === 'weekly' ? (
                        <SettingsField
                          label="星期"
                          placeholder="1-7，周一=1"
                          value={cronForm.weekday}
                          onChange={(value) => setCronForm((current) => ({ ...current, weekday: value }))}
                        />
                      ) : null}
                      {cronForm.period === 'monthly' || cronForm.period === 'yearly' ? (
                        <SettingsField
                          label="日期"
                          placeholder="1-31"
                          value={cronForm.day}
                          inputType="number"
                          onChange={(value) => setCronForm((current) => ({ ...current, day: value }))}
                        />
                      ) : null}
                      {cronForm.period === 'yearly' ? (
                        <SettingsField
                          label="月份"
                          placeholder="1-12"
                          value={cronForm.month}
                          inputType="number"
                          onChange={(value) => setCronForm((current) => ({ ...current, month: value }))}
                        />
                      ) : null}
                    </div>
                  ) : null}

                  {cronForm.scheduleType === 'periodic' && cronForm.period === 'interval' ? (
                    <div className="grid-two">
                      <SettingsField
                        label="间隔数量"
                        placeholder="1"
                        value={cronForm.intervalCount}
                        inputType="number"
                        onChange={(value) => setCronForm((current) => ({ ...current, intervalCount: value }))}
                      />
                      <SettingsField
                        label="间隔单位"
                        placeholder="minutes"
                        value={cronForm.intervalUnit}
                        options={[
                          { value: 'minutes', label: '分钟' },
                          { value: 'hours', label: '小时' },
                          { value: 'days', label: '天' },
                          { value: 'weeks', label: '周' },
                          { value: 'months', label: '月' },
                          { value: 'years', label: '年' },
                        ]}
                        onChange={(value) =>
                          setCronForm((current) => ({
                            ...current,
                            intervalUnit: value as CronFormState['intervalUnit'],
                          }))
                        }
                      />
                    </div>
                  ) : null}
                </SettingsSection>

                <SettingsSection title="执行选项">
                  <div className="grid-three">
                    <SettingsField
                      label="重复次数上限"
                      placeholder="留空表示不限"
                      value={cronForm.repeatTimes}
                      inputType="number"
                      onChange={(value) => setCronForm((current) => ({ ...current, repeatTimes: value }))}
                    />
                    <SettingsField
                      label="最大延迟分钟"
                      placeholder="例如：360"
                      value={cronForm.maxDelayMinutes}
                      inputType="number"
                      onChange={(value) => setCronForm((current) => ({ ...current, maxDelayMinutes: value }))}
                    />
                    <SettingsField
                      label="引用任务上下文"
                      placeholder="job-a, job-b"
                      value={cronForm.contextFrom}
                      onChange={(value) => setCronForm((current) => ({ ...current, contextFrom: value }))}
                    />
                  </div>
                  <div className="settings-actions">
                    <button
                      className="icon-button"
                      type="button"
                      onClick={resetCronForm}
                      title={cronEditingJobId ? '取消编辑' : '重置表单'}
                      aria-label={cronEditingJobId ? '取消编辑' : '重置表单'}
                    >
                      {cronEditingJobId ? <X size={15} /> : <RotateCcw size={15} />}
                    </button>
                    <button
                      className="icon-button primary"
                      type="button"
                      onClick={() => void (cronEditingJobId ? handleUpdateCronJob() : handleCreateCronJob())}
                      title={cronEditingJobId ? '保存编辑' : '创建任务'}
                      aria-label={cronEditingJobId ? '保存编辑' : '创建任务'}
                    >
                      {cronEditingJobId ? <Save size={15} /> : <Plus size={15} />}
                    </button>
                  </div>
                </SettingsSection>

                <SettingsSection title="任务列表">
                  <div className="cron-list">
                    {cronJobs.map((job) => (
                      <CronJobRow
                        key={job.id}
                        job={job}
                        active={job.id === cronSelectedJobId}
                        onSelect={() => setCronSelectedJobId(job.id)}
                        onEdit={() => handleEditCronJob(job)}
                        onRun={() => void handleRunCronJob(job.id)}
                        onToggle={() => void handleToggleCronJob(job)}
                        onRemove={() => void handleRemoveCronJob(job.id)}
                      />
                    ))}
                    {cronJobs.length === 0 ? <div className="empty-inline">暂无定时任务</div> : null}
                  </div>
                </SettingsSection>
              </div>
            </div>
  )

  return (
    <div
      className="app-shell"
      onDrop={handleDropEvent}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <Bot size={16} />
          </div>
          <div className="brand-copy">
            <div className="brand-title">Chrysalis</div>
            <div className="brand-subtitle">v0.1.0 · Agent Runtime</div>
          </div>
        </div>

        <div className="topbar-status">
          <span className={`status-pill ${busy ? 'busy' : 'idle'}`}>{statusText}</span>
          <span className="status-pill subtle">{activeSession?.model || snapshot?.model || 'No model'}</span>
          <span className="status-pill subtle">{ready ? `${totalSessions} sessions` : 'Starting'}</span>
        </div>

        <div className="topbar-left-actions">
          <button
            className={`icon-button ${page === 'reviews' ? 'active' : ''}`}
            type="button"
            onClick={() => {
              if (page === 'reviews') {
                openChatPage()
              } else {
                openReviewPage()
              }
            }}
            title="审核台"
            aria-label="审核台"
          >
            <Shield size={16} />
            {Number(reviewStats.pending || 0) > 0 ? (
              <span className="topbar-badge">{formatCompactCount(Number(reviewStats.pending || 0))}</span>
            ) : null}
          </button>
          <button
            className={`icon-button ${page === 'cron' ? 'active' : ''}`}
            type="button"
            onClick={() => {
              if (page === 'cron') {
                openChatPage()
              } else {
                openCronPage()
              }
            }}
            title="定时任务"
            aria-label="定时任务"
          >
            <Clock3 size={16} />
          </button>
          <button
            className={`icon-button ${page === 'gateway' ? 'active' : ''}`}
            type="button"
            onClick={() => {
              if (page === 'gateway') {
                openChatPage()
              } else {
                openGatewayPage()
              }
            }}
            title="连接中心"
            aria-label="连接中心"
          >
            <Wrench size={16} />
          </button>
          <button
            className={`icon-button ${page === 'settings' ? 'active' : ''}`}
            type="button"
            onClick={() => {
              if (page === 'settings') {
                openChatPage()
              } else {
                void openSettingsPage()
              }
            }}
            title="设置"
            aria-label="设置"
          >
            <Settings size={16} />
          </button>
        </div>

        <div className="topbar-window-actions">
          <button
            className="icon-button"
            type="button"
            onClick={() => void window.chrysalis.minimizeWindow()}
            title="最小化"
            aria-label="最小化"
          >
            <Minus size={16} />
          </button>
          <button
            className="icon-button"
            type="button"
            onClick={() => void window.chrysalis.toggleWindowMaximize()}
            title="最大化"
            aria-label="最大化"
          >
            <Square size={15} />
          </button>
          <button
            className="icon-button danger"
            type="button"
            onClick={() => void window.chrysalis.closeWindow()}
            title="关闭"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>
      </header>

      <div
        ref={workspaceRef}
        className={`workspace ${widePage ? 'reviews-layout' : ''} ${page === 'gateway' ? 'gateway-layout' : ''} ${resizingEdge ? 'resizing' : ''}`}
        style={workspaceStyle}
      >
        <aside className="sidebar">
          <div className="sidebar-brand">
            <div className="brand-mark">
              <Bot size={18} />
            </div>
            <div className="brand-copy">
              <div className="brand-title">Chrysalis</div>
              <div className="brand-subtitle">v0.1.0 - Agent Runtime</div>
            </div>
          </div>

          <div className="sidebar-section-head">
            <div>
              <div className="nav-label">近期会话</div>
              <div className="panel-count">{totalSessions}</div>
            </div>
            <button className="icon-button primary" type="button" onClick={() => void handleNewSession()} title="新建会话" aria-label="新建会话">
              <Plus size={14} />
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={() => void refreshSnapshot()}
              title="刷新会话"
              aria-label="刷新会话"
            >
              <RefreshCw size={14} />
            </button>
          </div>

          <div className="sidebar-body">
            <label className="search-field">
              <Search size={14} />
              <input
                value={searchText}
                placeholder="Search sessions..."
                onChange={(event) => updateSearchText(event.currentTarget.value)}
              />
            </label>

            <div className="session-list">
              {sessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  active={session.id === snapshot?.active_session_id}
                  busy={Boolean(session.busy)}
                  onClick={() => void handleLoadSession(session.id)}
                />
              ))}
              {sessions.length === 0 ? <div className="empty-inline">No sessions</div> : null}
            </div>

            <div className="action-row">
              <button className="icon-button" type="button" onClick={() => void handleToggleSessionPinned(activeSession?.id || '')} disabled={!activeSession || busy} title="Pin" aria-label="Pin">
                {activeSession?.pinned ? <PinOff size={14} /> : <Pin size={14} />}
              </button>
              <button className="icon-button" type="button" onClick={startRenameCurrentSession} disabled={!activeSession || busy} title="Rename session" aria-label="Rename session">
                <Pencil size={14} />
              </button>
              <button className="icon-button danger" type="button" onClick={() => void handleDeleteSession(activeSession?.id || '')} disabled={!activeSession || busy} title="Delete" aria-label="Delete">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        </aside>

        <div
          className={`workspace-resizer left ${resizingEdge === 'left' ? 'active' : ''}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sessions panel"
          title="Resize sessions panel"
          onPointerDown={(event) => handleWorkspaceResizeStart('left', event)}
          onPointerMove={handleWorkspaceResizeMove}
          onPointerUp={handleWorkspaceResizeEnd}
          onPointerCancel={handleWorkspaceResizeEnd}
          onDoubleClick={() => resetWorkspaceResize('left')}
        />

        <main className="center">
          {page === 'chat' ? (
            <>
              <div className="panel-head center-head">
                <div>
                  <div className="panel-title">Chat</div>
                  <div className="panel-subtitle">{snapshot?.workspace_root || 'No workspace'}</div>
                </div>
                <div className="panel-spacer" />
                <div className="notice-text">{noticeText}</div>
              </div>

              <div
                ref={messageScrollRef}
                className="message-list"
                onScroll={(event) => {
                  const node = event.currentTarget
                  const nearBottom = node.scrollHeight - (node.scrollTop + node.clientHeight) < 48
                  setStickyToBottom(nearBottom)
                }}
              >
                {chatMessagesWithCache.length === 0 ? (
                  <div className="empty-state">
                    <div className="empty-title">Ready when you are</div>
                    <div className="empty-body">Start with a task or attach files.</div>
                  </div>
                ) : (
                  chatMessagesWithCache.map((message) => (
                    <LogRow
                      key={message.id}
                      message={message}
                    />
                  ))
                )}
              </div>

              {growthNotice ? (
                <div className="growth-notice" role="status">
                  <div className="growth-notice-icon">
                    <Shield size={15} />
                  </div>
                  <div className="growth-notice-main">
                    <div className="growth-notice-title">{growthNotice.title}</div>
                    <div className="growth-notice-summary">
                      {growthNotice.summary || `${growthNotice.count} 个候选待审核`}
                    </div>
                  </div>
                  <div className="growth-notice-actions">
                    <button
                      className="pending-button primary growth-notice-open"
                      type="button"
                      onClick={() => openReviewCandidate(growthNotice.itemIds)}
                    >
                      <Shield size={13} />
                      <span>去审核</span>
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => setGrowthNotice(null)}
                      title="关闭"
                      aria-label="关闭"
                    >
                      <X size={14} />
                    </button>
                  </div>
                </div>
              ) : null}

              <div className="composer-shell">
                {attachments.length > 0 ? (
                  <div className="attachment-strip">
                    {attachments.map((attachment, index) => (
                      <AttachmentChip key={attachment.path} attachment={attachment} onRemove={() => void handleRemoveAttachment(index)} />
                    ))}
                  </div>
                ) : null}

                {activePendingRequest ? (
                  <PendingActionPanel
                    request={activePendingRequest}
                    choices={pendingChoices}
                    disabled={busy}
                    onReply={(reply) => void handlePendingReply(reply)}
                  />
                ) : null}

                <ProseMirrorComposer
                  busy={busy}
                  clearSignal={composerResetKey}
                  value={draftText}
                  placeholder={busy ? '输入引导内容，留空点击按钮可终止任务...' : 'Ask Chrysalis to do something...'}
                  onChange={(text) => {
                    setDraftText(text)
                  }}
                  onSubmit={(text) => void handleComposerSubmit(text)}
                />

                <div className="composer-footer">
                  <div className="footer-status">
                    <button className="composer-tool-button" type="button" onClick={() => void handleAttachFiles()} disabled={busy} title="Attach files" aria-label="Attach files">
                      <Plus size={16} />
                    </button>
                    <div
                      className={`composer-permission ${permissionLevel}`}
                      onBlur={(event) => {
                        const nextTarget = event.relatedTarget
                        if (!(nextTarget instanceof Node) || !event.currentTarget.contains(nextTarget)) {
                          setPermissionMenuOpen(false)
                        }
                      }}
                    >
                      <button
                        className="composer-permission-trigger"
                        type="button"
                        disabled={busy}
                        title="Permission level"
                        aria-label="Permission level"
                        aria-haspopup="menu"
                        aria-expanded={permissionMenuOpen}
                        onClick={() => setPermissionMenuOpen((open) => !open)}
                      >
                        <Shield className={`permission-icon ${permissionLevel}`} size={13} />
                        <span>{permissionLevelLabel(permissionLevel)}</span>
                        <ChevronDown size={13} />
                      </button>
                      {permissionMenuOpen && !busy ? (
                        <div className="composer-permission-menu" role="menu">
                          {PERMISSION_LEVEL_OPTIONS.map((option) => (
                            <button
                              key={option.value}
                              className={`composer-permission-option ${option.value} ${option.value === permissionLevel ? 'active' : ''}`}
                              type="button"
                              role="menuitemradio"
                              aria-checked={option.value === permissionLevel}
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => void handlePermissionLevelChange(option.value)}
                            >
                              <Shield className={`permission-icon ${option.value}`} size={13} />
                              <span>{option.label}</span>
                              {option.value === permissionLevel ? <Check size={13} /> : null}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    {errorText ? <span className="footer-error">{errorText}</span> : null}
                  </div>
                  <div className="footer-actions">
                    <span className="composer-model-chip">{snapshot?.model || 'my codex'}</span>
                    <ContextRing context={contextUsage} />
                    {!busy && resumableSessionId && resumableSessionId === activeSessionId ? (
                      <button
                        className="composer-resume-button"
                        type="button"
                        onClick={() => void handleResume()}
                        title="从断点继续上次被中断的任务"
                        aria-label="Resume task from checkpoint"
                      >
                        <RefreshCw size={14} />
                        <span>继续</span>
                      </button>
                    ) : null}
                    <button
                      className={`composer-run-button ${busy ? 'busy' : ''} ${busy && !draftText.trim() ? 'danger' : ''}`}
                      type="button"
                      onClick={() => {
                        if (busy && !draftText.trim()) {
                          void handleCancel()
                        } else {
                          void handleComposerSubmit(draftText)
                        }
                      }}
                      disabled={busy ? false : (activePendingRequest ? !draftText.trim() : !draftText.trim() && attachments.length === 0)}
                      title={busy ? (draftText.trim() ? 'Guide task' : 'Stop task') : 'Send'}
                      aria-label={busy ? (draftText.trim() ? 'Guide task' : 'Stop task') : 'Send'}
                    >
                      {busy && !draftText.trim() ? <Square size={15} /> : <Send size={15} />}
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => void handleAttachFiles()}
                      disabled={busy}
                      title="附加文件"
                      aria-label="附加文件"
                    >
                      <Paperclip size={15} />
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => void handleClearAttachments()}
                      disabled={busy || attachments.length === 0}
                      title="清空附件"
                      aria-label="清空附件"
                    >
                      <RotateCcw size={15} />
                    </button>
                  </div>
                </div>
              </div>
            </>
          ) : page === 'cron' ? (
            cronPage
          ) : page === 'reviews' ? (
            <ReviewWorkbench
              snapshot={reviewSnapshot}
              items={filteredReviewItems}
              allItems={reviewItems}
              stats={reviewStats}
              filter={reviewFilter}
              selectedItem={selectedReviewItem}
              draft={reviewDraft}
              saving={reviewSaving}
              notice={noticeText}
              error={errorText}
              onBack={openChatPage}
              onRefresh={() => void refreshSnapshot()}
              onFilterChange={setReviewFilter}
              onSelect={(itemId) => setSelectedReviewId(itemId)}
              onDraftChange={(patch) => setReviewDraft((current) => ({ ...current, ...patch }))}
              onSave={() => void handleReviewUpdate()}
              onApprove={() => void handleReviewApprove()}
              onDiscard={() => void handleReviewDiscard()}
            />
          ) : page === 'gateway' ? (
            <GatewayCenter
              snapshot={gatewaySnapshot}
              platforms={gatewayPlatforms}
              runningCount={gatewayRunningCount}
              configuredCount={gatewayConfiguredCount}
              failedCount={gatewayFailedCount}
              log={gatewayLog}
              notice={noticeText}
              error={errorText}
              onBack={openChatPage}
              onRefresh={() => void handleGatewayRefresh()}
              onStart={(platformId) => void handleGatewayStart(platformId)}
              onStop={(platformId) => void handleGatewayStop(platformId)}
              onLogs={(platformId) => void handleGatewayLogs(platformId)}
              onCopy={(platform) => void handleGatewayCopy(platform)}
            />
          ) : page === 'settings' ? (
            <div className="settings-page model-page">
              <div className="settings-head model-head">
                <div className="page-title-stack">
                  <div className="page-kicker"><Settings size={14} /> 模型配置</div>
                  <div className="panel-title">模型配置</div>
                  <div className="panel-subtitle">配置模型连接和运行参数</div>
                </div>
                <div className="panel-spacer" />
                <span className={`config-status ${settingsForm.enabled ? 'enabled' : ''}`}>
                  {settingsForm.enabled ? '桌面端覆盖已启用' : '使用默认配置'}
                </span>
                <button className="icon-button" type="button" onClick={openChatPage} title="返回对话" aria-label="返回对话">
                  <ArrowLeft size={15} />
                </button>
              </div>

              <div className="settings-body">
                <SettingsSection title="配置档案">
                  <div className="switch-row">
                    <div className="switch-copy">
                      <div className="field-label">启用桌面端覆盖</div>
                      <div className="field-hint">覆盖默认模型配置。</div>
                    </div>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={settingsForm.enabled}
                        onChange={(event) => {
                          const enabled = event.currentTarget.checked
                          setSettingsForm((current) => ({ ...current, enabled }))
                          setSettingsDirty(true)
                        }}
                      />
                      <span />
                    </label>
                  </div>

                  <div className="grid-two">
                    <SettingsField
                      label="配置名称"
                      placeholder="桌面端配置"
                      value={settingsForm.name}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, name: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="服务商"
                      placeholder="openai / anthropic"
                      value={settingsForm.provider}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, provider: value }))
                        setSettingsDirty(true)
                      }}
                    />
                  </div>
                </SettingsSection>

                <SettingsSection title="权限设置">
                  <div className="grid-two">
                    <SettingsField
                      label="权限级别"
                      placeholder="balanced"
                      value={settingsForm.permissionLevel}
                      options={PERMISSION_LEVEL_OPTIONS}
                      onChange={(value) => {
                        setSettingsForm((current) => ({
                          ...current,
                          permissionLevel: normalizePermissionLevel(value),
                        }))
                        setSettingsDirty(true)
                      }}
                    />
                    <div />
                  </div>
                </SettingsSection>

                <SettingsSection title="连接信息">
                  <div className="grid-two">
                    <SettingsField
                      label="API Key"
                      placeholder="sk-..."
                      value={settingsForm.apiKey}
                      secret
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, apiKey: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="Base URL"
                      placeholder="https://api.example.com"
                      value={settingsForm.baseUrl}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, baseUrl: value }))
                        setSettingsDirty(true)
                      }}
                    />
                  </div>

                  <div className="grid-three">
                    <SettingsField
                      label="模型"
                      placeholder="gpt-4.1 / claude..."
                      value={settingsForm.model}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, model: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="Wire API"
                      placeholder="chat"
                      value={settingsForm.wireApi}
                      options={WIRE_API_OPTIONS}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, wireApi: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="代理"
                      placeholder="http://127.0.0.1:7890"
                      value={settingsForm.proxy}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, proxy: value }))
                        setSettingsDirty(true)
                      }}
                    />
                  </div>
                </SettingsSection>

                <SettingsSection title="运行参数">
                  <div className="grid-three">
                    <SettingsField
                      label="上下文窗口"
                      placeholder="28000"
                      value={settingsForm.contextWindow}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, contextWindow: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="温度"
                      placeholder="0.2"
                      value={settingsForm.temperature}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, temperature: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="最大输出 Token"
                      placeholder="4096"
                      value={settingsForm.maxTokens}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, maxTokens: value }))
                        setSettingsDirty(true)
                      }}
                    />
                  </div>

                  <div className="grid-three">
                    <SettingsField
                      label="最大重试次数"
                      placeholder="4"
                      value={settingsForm.maxRetries}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, maxRetries: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="超时秒数"
                      placeholder="60"
                      value={settingsForm.timeout}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, timeout: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <SettingsField
                      label="Thinking 模式"
                      placeholder="disabled"
                      value={settingsForm.thinking}
                      options={THINKING_OPTIONS}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, thinking: value }))
                        setSettingsDirty(true)
                      }}
                    />
                  </div>

                  <div className="grid-two">
                    <SettingsField
                      label="Thinking 预算"
                      placeholder="0"
                      value={settingsForm.thinkingBudget}
                      onChange={(value) => {
                        setSettingsForm((current) => ({ ...current, thinkingBudget: value }))
                        setSettingsDirty(true)
                      }}
                    />
                    <div />
                  </div>

                  <SettingsField
                    label="系统提示词"
                    placeholder="输入系统提示词"
                    value={settingsForm.systemPrompt}
                    multiline
                    onChange={(value) => {
                      setSettingsForm((current) => ({ ...current, systemPrompt: value }))
                      setSettingsDirty(true)
                    }}
                  />
                </SettingsSection>

                <div className="settings-actions">
                  <button className="icon-button" type="button" onClick={() => void resetSettingsPage()} disabled={busy} title="重置设置" aria-label="重置设置">
                    <RotateCcw size={15} />
                  </button>
                  <button className="icon-button primary" type="button" onClick={() => void saveSettingsPage()} disabled={busy} title="保存设置" aria-label="保存设置">
                    <Save size={15} />
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </main>

        {page === 'chat' ? (
          <div
            className={`workspace-resizer right ${resizingEdge === 'right' ? 'active' : ''}`}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize context panel"
            title="Resize context panel"
            onPointerDown={(event) => handleWorkspaceResizeStart('right', event)}
            onPointerMove={handleWorkspaceResizeMove}
            onPointerUp={handleWorkspaceResizeEnd}
            onPointerCancel={handleWorkspaceResizeEnd}
            onDoubleClick={() => resetWorkspaceResize('right')}
          />
        ) : null}

        {page === 'chat' ? (
        <aside className="inspector">
          <div className="panel-head inspector-head">
            <div className="inspector-head-copy">
              <div className="panel-title">{inspectorMode === 'trace' ? 'Trace 时间线' : '上下文窗口'}</div>
              <div className="panel-subtitle">{inspectorSubtitle}</div>
            </div>
            <div className="inspector-segmented" role="tablist" aria-label="Inspector mode">
              <button
                className={inspectorMode === 'context' ? 'active' : ''}
                type="button"
                onClick={() => setInspectorMode('context')}
                title="上下文"
                aria-label="上下文"
              >
                <FileText size={13} />
                <span>Context</span>
              </button>
              <button
                className={inspectorMode === 'trace' ? 'active' : ''}
                type="button"
                onClick={() => setInspectorMode('trace')}
                title="Trace"
                aria-label="Trace"
              >
                <Clock3 size={13} />
                <span>Trace</span>
              </button>
            </div>
          </div>

          {inspectorMode === 'trace' ? (
            <TracePanel
              groups={traceTaskGroups}
              selectedTaskId={effectiveTraceTaskId}
              nodes={activeTrace}
              summary={activeTraceSummary}
              replayIndex={replayIndex}
              replayNode={replayNode}
              live={traceLive}
              playing={tracePlaying}
              onTaskSelect={selectTraceTask}
              onLive={setTraceLiveMode}
              onPlayToggle={toggleTracePlayback}
              onSeek={seekTrace}
            />
          ) : (
            <div className="inspector-content context-content">
            <section className="inspector-section">
              <div className="section-title">上下文窗口</div>
              <div className="context-meter" aria-label={`Context ${contextPercent}%`}>
                <div className="context-meter-fill" style={{ width: `${contextPercent}%` }} />
              </div>
              <div className="section-body mono-line">{contextPercent}% - {contextTokenLabel}</div>
            </section>

            <section className="inspector-section context-budget-section">
              <div className="section-title">Context budget</div>
              {contextSections.length > 0 ? (
                <div className="context-budget-list">
                  {contextSections.map((section, index) => {
                    const allocated = toTraceNumber(section.allocated_chars || section.budget_chars)
                    const used = toTraceNumber(section.used_chars)
                    const fill = allocated > 0 ? Math.round((used / allocated) * 100) : 0
                    const fillWidth = used > 0 ? Math.max(2, Math.min(100, fill)) : 0
                    const recallItems = contextSectionRecallItems(section)
                    return (
                      <div key={`${section.name}-${section.source || index}-${section.used_chars}`} className={`context-budget-row ${section.kind || ''}`}>
                        <div className="context-budget-row-head">
                          <span className="context-budget-name">{section.label || section.name}</span>
                          <span className={`context-budget-badge ${section.stable ? 'stable' : 'runtime'}`}>
                            {section.stable ? 'stable' : 'runtime'}
                          </span>
                        </div>
                        <div className="context-budget-meter" aria-label={contextSectionBudgetLine(section)}>
                          <div className="context-budget-fill" style={{ width: `${fillWidth}%` }} />
                        </div>
                        <div className="context-budget-meta">{contextSectionBudgetLine(section)}</div>
                        <div className="context-budget-reason">{contextSectionReason(section) || 'included by context policy'}</div>
                        {recallItems.length > 0 ? (
                          <div className="context-recall-list">
                            {recallItems.map((item) => (
                              <div key={`${section.name}-${item}`} className="context-recall-item">{item}</div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              ) : (
                <div className="empty-inline">No context budget detail yet</div>
              )}
            </section>

            <section className="inspector-section">
              <div className="section-title">工作记忆</div>
              <div className="memory-pills">
                {(memoryTags.length > 0 ? memoryTags : ['session', 'workspace', 'runtime']).map((tag, index) => (
                  <span key={`${tag}-${index}`} className={`pill ${index < 3 ? 'active' : ''}`}>{tag}</span>
                ))}
              </div>
              <div className="memory-summary">
                <div className="memory-goal">{todoState.goal || todoState.active_todo_title || '当前任务已载入'}</div>
                <div className="memory-meta">
                  {String(todoState.pending_count ?? 0)} open - {String(todoState.completed_count ?? 0)} done
                </div>
              </div>
            </section>

            <section className="inspector-section">
              <div className="section-title">
                <span>TODO</span>
              </div>
              {todoVisible ? (
                <div className="plan-shell">
                  <div className="plan-head">
                    <div className="plan-goal">{todoState.goal || 'Not set'}</div>
                    <div className={`plan-status ${todoItemStatusClass(todoState.pending_count ? 'active' : 'done')}`}>
                      {todoState.pending_count ? 'active' : 'done'}
                    </div>
                  </div>

                  <div className="metrics-grid plan-metrics">
                    <Metric label="Open" value={String(todoState.pending_count ?? 0)} />
                    <Metric label="Done" value={String(todoState.completed_count ?? 0)} />
                    <Metric label="Rounds" value={String(todoState.rounds_since_todo ?? 0)} />
                    <Metric label="Interval" value={String(todoState.todo_reminder_interval ?? 0)} />
                  </div>

                  <div className="plan-columns">
                    <TodoList
                      title="Tasks"
                      emptyLabel="暂无 TODO"
                      items={todoState.todos || []}
                      activeId={todoState.active_todo_id || ''}
                    />
                  </div>
                </div>
              ) : (
                <div className="empty-inline">暂无 TODO</div>
              )}
            </section>

            <section className="inspector-section">
              <div className="section-title">Token 统计</div>
              <div className="section-body runtime-lines">
                <div>输入: {formatCompactCount(contextTokens)}</div>
                <div>窗口: {contextWindow ? formatCompactCount(contextWindow) : '-'}</div>
                <div>消息: {visibleMessageCount}</div>
                <div>轮次: {conversationTurns}</div>
                <div>缓存命中: {formatCacheHitRate(sessionCacheUsage)}</div>
                <div>缓存读取: {formatCompactCount(sessionCacheUsage.readTokens)}</div>
                {sessionCacheUsage.writeTokens > 0 ? (
                  <div>缓存写入: {formatCompactCount(sessionCacheUsage.writeTokens)}</div>
                ) : null}
              </div>
            </section>

          </div>
          )}
        </aside>
        ) : null}
      </div>

      {dragDepth > 0 && !busy ? (
        <div className="drop-overlay">
          <div className="drop-card">
            <Paperclip size={20} />
            <div>Drop files to attach</div>
          </div>
        </div>
      ) : null}

      {paletteOpen ? (
        <div className="modal-layer" onMouseDown={(event) => event.target === event.currentTarget && closePalette()}>
          <div className="modal-shell palette-shell">
            <div className="modal-head">
              <div className="modal-title">
                <Command size={15} />
                <span>命令面板</span>
              </div>
              <button className="icon-button" type="button" onClick={closePalette} title="关闭" aria-label="关闭">
                <X size={14} />
              </button>
            </div>
            <label className="search-field palette-field">
              <Search size={14} />
              <input
                ref={paletteInputRef}
                value={paletteText}
                placeholder="Command or session search..."
                onChange={(event) => updatePaletteText(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    const nextTask = paletteText.trim()
                    if (nextTask.length > 0) {
                      setDraftText(nextTask)
                      setComposerResetKey((value) => value + 1)
                      setPage('chat')
                    }
                    closePalette()
                  }
                }}
              />
            </label>
            <div className="palette-actions">
              <button
                className="icon-button primary"
                type="button"
                onClick={() => {
                  const nextTask = paletteText.trim()
                  if (nextTask.length > 0) {
                    setDraftText(nextTask)
                    setComposerResetKey((value) => value + 1)
                    setPage('chat')
                  }
                  closePalette()
                }}
              >
                <Plus size={14} />
              </button>
              <button className="icon-button" type="button" onClick={() => void handleNewSession()}>
                <MessageSquare size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {renameState.open ? (
        <div className="modal-layer" onMouseDown={(event) => event.target === event.currentTarget && closeRenameDialog()}>
          <div className="modal-shell rename-shell">
            <div className="modal-head">
              <div className="modal-title">
                <Pencil size={15} />
                <span>Rename session</span>
              </div>
              <button className="icon-button" type="button" onClick={closeRenameDialog} title="Close" aria-label="Close">
                <X size={14} />
              </button>
            </div>
            <label className="search-field rename-field">
              <Pencil size={14} />
              <input
                ref={renameInputRef}
                value={renameState.title}
                placeholder="Session title"
                onChange={(event) => updateRenameTitle(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    submitRename()
                  }
                }}
              />
            </label>
            <div className="palette-actions">
              <button
                className="icon-button primary"
                type="button"
                onClick={submitRename}
                disabled={!renameState.title.trim()}
              >
                <Save size={14} />
              </button>
              <button className="icon-button" type="button" onClick={closeRenameDialog}>
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function GatewayCenter({
  snapshot,
  platforms,
  runningCount,
  configuredCount,
  failedCount,
  log,
  notice,
  error,
  onBack,
  onRefresh,
  onStart,
  onStop,
  onLogs,
  onCopy,
}: {
  snapshot: GatewaySnapshot
  platforms: GatewayPlatformSnapshot[]
  runningCount: number
  configuredCount: number
  failedCount: number
  log: GatewayLogState
  notice: string
  error: string
  onBack: () => void
  onRefresh: () => void
  onStart: (platformId: string) => void
  onStop: (platformId: string) => void
  onLogs: (platformId: string) => void
  onCopy: (platform: GatewayPlatformSnapshot) => void
}) {
  const selectedPlatform = platforms.find((platform) => platform.id === log.platformId) || platforms[0] || null
  const logTitle = selectedPlatform ? `${selectedPlatform.label} 日志` : '网关日志'
  return (
    <div className="gateway-page">
      <div className="settings-head gateway-head">
        <div className="page-title-stack">
          <div className="page-kicker"><Wrench size={14} /> 连接中心</div>
          <div className="panel-title">连接中心</div>
          <div className="panel-subtitle">
            {runningCount} 运行中 · {configuredCount} 已配置 · {failedCount} 失败
            {snapshot.updated_at ? ` · ${formatTimestamp(snapshot.updated_at)}` : ''}
          </div>
        </div>
        <div className="panel-spacer" />
        {notice ? <div className="notice-text">{notice}</div> : null}
        <button className="icon-button" type="button" onClick={onRefresh} title="刷新状态" aria-label="刷新状态">
          <RefreshCw size={15} />
        </button>
        <button className="icon-button" type="button" onClick={onBack} title="返回对话" aria-label="返回对话">
          <ArrowLeft size={15} />
        </button>
      </div>

      <div className="gateway-body">
        <section className="gateway-summary" aria-label="Gateway summary">
          <Metric label="运行中" value={String(runningCount)} />
          <Metric label="已配置" value={String(configuredCount)} />
          <Metric label="失败" value={String(failedCount)} />
        </section>

        <section className="gateway-grid" aria-label="Gateway platforms">
          {platforms.length > 0 ? platforms.map((platform) => (
            <GatewayPlatformCard
              key={platform.id}
              platform={platform}
              active={platform.id === selectedPlatform?.id}
              onStart={() => onStart(platform.id)}
              onStop={() => onStop(platform.id)}
              onLogs={() => onLogs(platform.id)}
              onCopy={() => onCopy(platform)}
            />
          )) : (
            <div className="empty-inline">暂无网关状态</div>
          )}
        </section>

        <section className="gateway-log-panel">
          <div className="gateway-log-head">
            <div>
              <div className="gateway-log-title">{logTitle}</div>
              <div className="gateway-log-path">{log.path || '暂无日志文件'}</div>
            </div>
            {selectedPlatform ? (
              <button className="icon-button" type="button" onClick={() => onLogs(selectedPlatform.id)} title="刷新日志" aria-label="刷新日志">
                {log.loading ? <RefreshCw size={14} className="spin" /> : <Terminal size={14} />}
              </button>
            ) : null}
          </div>
          {error ? <div className="gateway-error">{error}</div> : null}
          <pre className="gateway-log-body">
            {log.loading ? 'Loading logs...' : log.text || '暂无日志'}
          </pre>
        </section>
      </div>
    </div>
  )
}

function GatewayPlatformCard({
  platform,
  active,
  onStart,
  onStop,
  onLogs,
  onCopy,
}: {
  platform: GatewayPlatformSnapshot
  active: boolean
  onStart: () => void
  onStop: () => void
  onLogs: () => void
  onCopy: () => void
}) {
  const statusClass = gatewayStatusClass(platform.status)
  const detail = gatewayStatusDetail(platform)
  const required = platform.required_config || []
  const missingDeps = platform.missing_dependencies || []
  const canCopy = Boolean(gatewayCopyText(platform))
  const canStart = Boolean(platform.configured) && missingDeps.length === 0
  return (
    <article className={`gateway-card ${statusClass} ${active ? 'active' : ''}`}>
      <div className="gateway-card-main">
        <div className="gateway-card-head">
          <div>
            <div className="gateway-card-title">{platform.label}</div>
            <div className="gateway-card-subtitle">{platform.launch_platform || platform.id}</div>
          </div>
          <span className={`gateway-status ${statusClass}`}>{gatewayStatusLabel(platform.status)}</span>
        </div>
        {detail ? <div className="gateway-card-detail">{detail}</div> : null}
        {required.length > 0 ? (
          <div className="gateway-chip-row">
            {required.map((key) => <span key={`${platform.id}-${key}`} className="gateway-chip">{key}</span>)}
          </div>
        ) : null}
        {missingDeps.length > 0 ? (
          <div className="gateway-deps">
            <AlertTriangle size={13} />
            <span>{platform.install_hint || `Missing: ${missingDeps.join(', ')}`}</span>
          </div>
        ) : null}
      </div>
      <div className="gateway-card-actions">
        {platform.running ? (
          <button className="icon-button danger" type="button" onClick={onStop} title="停止网关" aria-label="停止网关">
            <Square size={14} />
          </button>
        ) : (
          <button className="icon-button primary" type="button" onClick={onStart} disabled={!canStart} title="启动网关" aria-label="启动网关">
            <Play size={14} />
          </button>
        )}
        <button className="icon-button" type="button" onClick={onLogs} title="查看日志" aria-label="查看日志">
          <Terminal size={14} />
        </button>
        <button className="icon-button" type="button" onClick={onCopy} disabled={!canCopy} title="复制错误" aria-label="复制错误">
          <Copy size={14} />
        </button>
      </div>
    </article>
  )
}

function ReviewWorkbench({
  snapshot,
  items,
  allItems,
  stats,
  filter,
  selectedItem,
  draft,
  saving,
  notice,
  error,
  onBack,
  onRefresh,
  onFilterChange,
  onSelect,
  onDraftChange,
  onSave,
  onApprove,
  onDiscard,
}: {
  snapshot: ReviewSnapshot
  items: ReviewItem[]
  allItems: ReviewItem[]
  stats: NonNullable<ReviewSnapshot['stats']>
  filter: ReviewFilter
  selectedItem: ReviewItem | null
  draft: ReviewEditState
  saving: string
  notice: string
  error: string
  onBack: () => void
  onRefresh: () => void
  onFilterChange: (filter: ReviewFilter) => void
  onSelect: (itemId: string) => void
  onDraftChange: (patch: Partial<ReviewEditState>) => void
  onSave: () => void
  onApprove: () => void
  onDiscard: () => void
}) {
  const pending = Number(stats.pending || 0)
  const canEdit = Boolean(selectedItem && !saving)
  const evidence = selectedItem?.evidence || []
  const validation = selectedItem?.validation || {}
  const validationIssues = Array.isArray(validation.issues)
    ? validation.issues.map((item) => String(item)).filter(Boolean)
    : []
  const contentLabel = selectedItem?.kind === 'skill' ? 'SKILL.md' : '记忆内容'
  const detailTitle = selectedItem ? reviewItemTitle(selectedItem) : '暂无审核项'
  const summary = selectedItem ? normalizedReviewSummary(selectedItem) : null
  const summaryCards = selectedItem ? reviewSummaryCards(selectedItem) : []

  return (
    <div className="reviews-page">
      <div className="settings-head reviews-head">
        <div className="page-title-stack">
          <div className="page-kicker"><Shield size={14} /> 技能笔记和记忆审核台</div>
          <div className="panel-title">技能笔记和记忆审核台</div>
          <div className="panel-subtitle">{pending} 待审核 · {Number(stats.skills || 0)} 技能笔记 · {Number(stats.memories || 0)} 记忆</div>
        </div>
        <div className="panel-spacer" />
        {notice ? <span className="review-head-note">{notice}</span> : null}
        <button className="icon-button" type="button" onClick={onRefresh} title="刷新" aria-label="刷新">
          <RefreshCw size={15} />
        </button>
        <button className="icon-button" type="button" onClick={onBack} title="返回对话" aria-label="返回对话">
          <ArrowLeft size={15} />
        </button>
      </div>

      <div className="reviews-body">
        <div className="review-metrics" aria-label="Review summary">
          <ReviewMetric label="待审核" value={stats.pending} tone="pending" />
          <ReviewMetric label="已批准" value={stats.approved} tone="approved" />
          <ReviewMetric label="已丢弃" value={stats.discarded} tone="discarded" />
          <ReviewMetric label="技能笔记草稿" value={stats.draft_skills} tone="skill" />
        </div>

        <div className="review-workbench">
          <section className="review-list-panel">
            <div className="review-list-head">
              <div>
                <div className="section-title">审核队列</div>
                <div className="review-list-count">{items.length} / {allItems.length}</div>
              </div>
              <div className="review-filter-tabs" role="tablist" aria-label="Review filter">
                {REVIEW_FILTERS.map((item) => (
                  <button
                    key={item.value}
                    className={filter === item.value ? 'active' : ''}
                    type="button"
                    aria-pressed={filter === item.value}
                    onClick={() => onFilterChange(item.value)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="review-list">
              {items.map((item) => (
                <ReviewRow
                  key={item.id}
                  item={item}
                  active={selectedItem?.id === item.id}
                  onClick={() => onSelect(item.id)}
                />
              ))}
              {items.length === 0 ? (
                <div className="review-empty">
                  <CheckCircle2 size={18} />
                  <span>{filter === 'pending' ? '没有待审核项' : '没有匹配的审核项'}</span>
                </div>
              ) : null}
            </div>
          </section>

          <section className="review-detail-panel">
            {selectedItem ? (
              <>
                <div className="review-detail-head">
                  <div className="review-detail-title-wrap">
                    <div className="review-detail-kicker">
                      <ReviewKindMark item={selectedItem} />
                      <ReviewStatusBadge status={selectedItem.status} />
                      <span>{reviewTargetLabel(selectedItem)}</span>
                    </div>
                    <div className="review-detail-title">{detailTitle}</div>
                  </div>
                  <div className="review-actions">
                    <button className="icon-button" type="button" onClick={onSave} disabled={!canEdit} title="保存编辑" aria-label="保存编辑">
                      {saving === 'save' ? <RefreshCw size={15} className="spin" /> : <Save size={15} />}
                    </button>
                    <button className="icon-button primary" type="button" onClick={onApprove} disabled={!canEdit} title="批准" aria-label="批准">
                      {saving === 'approve' ? <RefreshCw size={15} className="spin" /> : <Check size={15} />}
                    </button>
                    <button className="icon-button danger" type="button" onClick={onDiscard} disabled={!canEdit} title="丢弃" aria-label="丢弃">
                      {saving === 'discard' ? <RefreshCw size={15} className="spin" /> : <Trash2 size={15} />}
                    </button>
                  </div>
                </div>

                {error ? <div className="review-error"><AlertTriangle size={14} /> {error}</div> : null}

                <div className="review-summary-grid">
                  {summaryCards.map((card) => (
                    <section key={card.label} className={`review-summary-card ${card.tone}`}>
                      <div className="review-summary-card-title">{card.label}</div>
                      <div className="review-summary-card-body">{card.value}</div>
                    </section>
                  ))}
                </div>

                <div className="review-editor-grid">
                  <label className="review-field">
                    <span>标题</span>
                    <input
                      value={draft.title}
                      disabled={!canEdit}
                      onChange={(event) => onDraftChange({ title: event.currentTarget.value })}
                    />
                  </label>

                  <ReviewTargetEditor
                    item={selectedItem}
                    value={draft.target}
                    disabled={!canEdit}
                    onChange={(target) => onDraftChange({ target })}
                  />
                </div>

                {selectedItem.kind === 'skill' ? (
                  <label className="review-field">
                    <span>描述</span>
                    <input
                      value={draft.description}
                      disabled={!canEdit}
                      onChange={(event) => onDraftChange({ description: event.currentTarget.value })}
                    />
                  </label>
                ) : null}

                <label className="review-field review-content-field">
                  <span>{contentLabel}</span>
                  <textarea
                    value={draft.content}
                    disabled={!canEdit}
                    onChange={(event) => onDraftChange({ content: event.currentTarget.value })}
                  />
                </label>

                <div className="review-detail-grid">
                  <section className="review-info-section">
                    <div className="section-title">审核建议</div>
                    <div className="review-reason">{summary?.next_action || selectedItem.reason || '无记录'}</div>
                    {summary?.tools && summary.tools.length > 0 ? (
                      <div className="review-quality-issues">
                        {summary.tools.map((tool) => <span key={`${selectedItem.id}-${tool}`}>{tool}</span>)}
                      </div>
                    ) : null}
                  </section>

                  <section className="review-info-section">
                    <div className="section-title">证据</div>
                    <div className="review-evidence-list">
                      {evidence.map((item, index) => (
                        <div key={`${selectedItem.id}-evidence-${index}`} className="review-evidence-item">{item}</div>
                      ))}
                      {evidence.length === 0 ? <div className="empty-inline">无证据片段</div> : null}
                    </div>
                  </section>
                </div>

                <div className="review-detail-grid">
                  <section className="review-info-section">
                    <div className="section-title">来源</div>
                    <ReviewMetaRows item={selectedItem} />
                  </section>

                  <section className="review-info-section">
                    <div className="section-title">质量</div>
                    {selectedItem.kind === 'skill' ? (
                      <div className="review-quality">
                        <div className={`review-quality-status ${String(validation.status || '').toLowerCase()}`}>
                          {String(validation.status || 'unchecked')}
                          {typeof validation.score === 'number' ? ` · ${validation.score}` : ''}
                        </div>
                        {validationIssues.length > 0 ? (
                          <div className="review-quality-issues">
                            {validationIssues.map((issue) => <span key={issue}>{issue}</span>)}
                          </div>
                        ) : <div className="empty-inline">无校验问题</div>}
                      </div>
                    ) : (
                      <div className="review-quality">
                        <div className="review-quality-status passed">memory candidate</div>
                        <div className="review-quality-issues">
                          <span>{reviewTargetLabel(selectedItem)}</span>
                        </div>
                      </div>
                    )}
                  </section>
                </div>
              </>
            ) : (
              <div className="review-detail-empty">
                <Shield size={24} />
                <div>没有可查看的审核项</div>
              </div>
            )}
          </section>
        </div>

        {snapshot.errors && snapshot.errors.length > 0 ? (
          <div className="review-errors">
            {snapshot.errors.map((item) => <span key={item}>{item}</span>)}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ReviewMetric({ label, value, tone }: { label: string; value?: number; tone: string }) {
  return (
    <div className={`review-metric ${tone}`}>
      <div className="review-metric-value">{formatCompactCount(Number(value || 0))}</div>
      <div className="review-metric-label">{label}</div>
    </div>
  )
}

function ReviewRow({ item, active, onClick }: { item: ReviewItem; active: boolean; onClick: () => void }) {
  return (
    <button className={`review-row ${active ? 'active' : ''} ${item.status}`} type="button" onClick={onClick}>
      <div className="review-row-kind">
        <ReviewKindMark item={item} />
      </div>
      <div className="review-row-main">
        <div className="review-row-title">{reviewItemTitle(item)}</div>
        <div className="review-row-summary">{reviewItemSummary(item)}</div>
        <div className="review-row-meta">
          <span>{reviewTargetLabel(item)}</span>
          <span>{formatReviewTime(item.updated_at || item.created_at)}</span>
        </div>
      </div>
      <ReviewStatusBadge status={item.status} />
    </button>
  )
}

function ReviewKindMark({ item }: { item: ReviewItem }) {
  return (
    <span className={`review-kind-mark ${item.kind}`}>
      {item.kind === 'skill' ? <Wrench size={13} /> : <FileText size={13} />}
      <span>{item.kind === 'skill' ? '技能笔记' : '记忆'}</span>
    </span>
  )
}

function ReviewStatusBadge({ status }: { status: ReviewStatus | string }) {
  return <span className={`review-status-badge ${status}`}>{reviewStatusLabel(status)}</span>
}

function ReviewTargetEditor({
  item,
  value,
  disabled,
  onChange,
}: {
  item: ReviewItem
  value: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  if (item.kind === 'memory') {
    return (
      <div className="review-field">
        <span>目标</span>
        <div className="review-target-tabs">
          {MEMORY_TARGET_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={value === option.value ? 'active' : ''}
              type="button"
              disabled={disabled}
              onClick={() => onChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <label className="review-field">
      <span>类别</span>
      <input
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </label>
  )
}

function ReviewMetaRows({ item }: { item: ReviewItem }) {
  const rows = [
    ['ID', item.raw_id || item.id],
    ['创建', formatReviewTime(item.created_at)],
    ['更新', formatReviewTime(item.updated_at)],
    ['会话', item.session_id || '-'],
    ['路径', item.path || '-'],
  ]
  if (item.source_task) {
    rows.push(['任务', item.source_task])
  }
  return (
    <div className="review-meta-rows">
      {rows.map(([label, value]) => (
        <div key={label} className="review-meta-row">
          <span>{label}</span>
          <strong>{value || '-'}</strong>
        </div>
      ))}
    </div>
  )
}











function SessionRow({
  session,
  active,
  busy,
  onClick,
}: {
  session: SessionSummary
  active: boolean
  busy: boolean
  onClick: () => void
}) {
  return (
    <button className={`session-row ${active ? 'active' : ''}`} type="button" onClick={onClick}>
      <div className="session-row-main">
        <div className="session-title-line">
          <span className="session-title">{session.title || 'Untitled'}</span>
          <span className="session-title-flags">
            {session.pinned ? <Pin size={12} className="pinned-icon" /> : null}
            {busy ? <RefreshCw size={12} className="spin busy-icon" /> : null}
          </span>
          <span className="session-age">{formatSessionAge(session.updated_at)}</span>
        </div>
        <div className="session-meta">
          <span>{session.model || 'No model'}</span>
          <span>{session.turns} turns</span>
        </div>
      </div>
    </button>
  )
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: AttachmentSummary
  onRemove: () => void
}) {
  return (
    <div className="attachment-chip">
      <span className="attachment-kind">{attachment.kind}</span>
      <span className="attachment-name">{attachment.name}</span>
      <button className="chip-close" type="button" onClick={onRemove} title="Remove" aria-label="Remove">
        <X size={11} />
      </button>
    </div>
  )
}

function ContextRing({ context }: { context?: RuntimeSnapshot['context'] | null }) {
  const ratio = Math.max(0, Math.min(1, Number(context?.ratio || 0)))
  const percent = Math.round(ratio * 100)
  const softRatio = Number(context?.soft_ratio ?? 0.7)
  const hardRatio = Number(context?.hard_ratio ?? 0.9)
  const level = ratio >= hardRatio ? 'high' : ratio >= softRatio ? 'warn' : 'ok'
  const tokens = Number(context?.tokens_estimate || 0)
  const windowSize = Number(context?.context_window || 0)
  const title = `Context ${percent}% - ~${formatCompactCount(tokens)}/${formatCompactCount(windowSize)} tokens`
  const style = { '--context-ratio': `${percent}%` } as CSSProperties

  return (
    <div className={`context-ring ${level}`} style={style} title={title} aria-label={title}>
      <span>{percent}</span>
    </div>
  )
}

function TracePanel({
  groups,
  selectedTaskId,
  nodes,
  summary,
  replayIndex,
  replayNode,
  live,
  playing,
  onTaskSelect,
  onLive,
  onPlayToggle,
  onSeek,
}: {
  groups: TraceTaskGroup[]
  selectedTaskId: string
  nodes: TraceEventNode[]
  summary: TraceSummary
  replayIndex: number
  replayNode: TraceEventNode | null
  live: boolean
  playing: boolean
  onTaskSelect: (taskId: string) => void
  onLive: () => void
  onPlayToggle: () => void
  onSeek: (index: number) => void
}) {
  const rangeMax = Math.max(0, nodes.length - 1)
  const rangeValue = Math.max(0, replayIndex)
  const cursorLabel = nodes.length > 0 && replayIndex >= 0 ? `${replayIndex + 1}/${nodes.length}` : '0/0'
  const details = traceDetails(replayNode)
  const modeLabel = live ? 'Live' : playing ? 'Replay' : 'Paused'

  return (
    <div className="inspector-content trace-content">
      <section className="trace-task-switcher" aria-label="Trace tasks">
        <div className="trace-section-head">
          <div className="section-title">Tasks</div>
          <span>{formatCompactCount(groups.length)}</span>
        </div>
        <div className="trace-task-list">
          {groups.length > 0 ? [...groups].reverse().map((group) => (
            <button
              key={group.id}
              className={`trace-task-chip ${group.status} ${group.id === selectedTaskId ? 'active' : ''}`}
              type="button"
              onClick={() => onTaskSelect(group.id)}
              title={group.subtitle}
            >
              <span className="trace-task-title">{group.label}</span>
              <span className="trace-task-meta">{group.subtitle}</span>
            </button>
          )) : (
            <div className="empty-inline">No trace tasks</div>
          )}
        </div>
      </section>

      <section className="trace-focus">
        <div className={`trace-focus-icon ${replayNode ? traceKindClass(replayNode) : ''}`}>
          {traceIcon(replayNode)}
        </div>
        <div className="trace-focus-copy">
          <div className="trace-focus-kicker">
            <span>{modeLabel}</span>
            <span>{cursorLabel}</span>
            {replayNode?.timestamp ? <span>{formatTraceTime(replayNode.timestamp)}</span> : null}
          </div>
          <div className="trace-focus-title">{traceTitle(replayNode)}</div>
          <div className="trace-focus-subtitle">{traceSubtitle(replayNode)}</div>
        </div>
      </section>

      <section className="trace-controls" aria-label="Trace replay">
        <button className={`trace-live-button ${live ? 'active' : ''}`} type="button" onClick={onLive}>
          <span className="trace-live-dot" />
          <span>Live</span>
        </button>
        <button
          className="icon-button trace-play-button"
          type="button"
          onClick={onPlayToggle}
          disabled={nodes.length === 0}
          title={playing ? '暂停回放' : '播放回放'}
          aria-label={playing ? '暂停回放' : '播放回放'}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <input
          className="trace-range"
          type="range"
          min={0}
          max={rangeMax}
          value={rangeValue}
          disabled={nodes.length <= 1}
          onChange={(event) => onSeek(Number(event.currentTarget.value))}
          aria-label="Trace cursor"
        />
        <span className="trace-cursor">{cursorLabel}</span>
      </section>

      <section className="trace-summary-grid" aria-label="Trace summary">
        <div className="trace-stat model">
          <div className="trace-stat-value">{formatCompactCount(summary.modelCalls)}</div>
          <div className="trace-stat-label">Calls</div>
        </div>
        <div className="trace-stat tool">
          <div className="trace-stat-value">{formatCompactCount(summary.tools)}</div>
          <div className="trace-stat-label">Tools</div>
        </div>
        <div className="trace-stat permission">
          <div className="trace-stat-value">{formatCompactCount(summary.permissions)}</div>
          <div className="trace-stat-label">Perms</div>
        </div>
        <div className="trace-stat context">
          <div className="trace-stat-value">{formatCompactCount(summary.tokens)}</div>
          <div className="trace-stat-label">Tokens</div>
        </div>
        <div className="trace-stat money">
          <div className="trace-stat-value">{formatCost(summary.cost)}</div>
          <div className="trace-stat-label">Cost</div>
        </div>
        <div className="trace-stat time">
          <div className="trace-stat-value">{formatMs(summary.elapsedMs)}</div>
          <div className="trace-stat-label">Time</div>
        </div>
      </section>

      <section className="inspector-section trace-detail-section">
        <div className="section-title">当前节点</div>
        {details.length > 0 ? (
          <div className="trace-detail-list">
            {details.map((detail) => (
              <div key={`${detail.label}-${detail.value}`} className="trace-detail-row">
                <span>{detail.label}</span>
                <pre>{detail.value}</pre>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-inline">暂无 Trace 事件</div>
        )}
      </section>

      <section className="inspector-section trace-timeline-section">
        <div className="trace-section-head">
          <div className="section-title">事件</div>
          <span>{formatCompactCount(nodes.length)}</span>
        </div>
        <div className="trace-event-list">
          {nodes.length > 0 ? nodes.map((node, index) => (
            <button
              key={node.id}
              className={`trace-event-row ${traceKindClass(node)} ${index === replayIndex ? 'active' : ''} ${!live && index > replayIndex ? 'future' : ''}`}
              type="button"
              onClick={() => onSeek(index)}
              title={traceSubtitle(node)}
            >
              <span className="trace-event-rail">
                <span className="trace-event-dot">{traceIcon(node)}</span>
              </span>
              <span className="trace-event-main">
                <span className="trace-event-title">{traceTitle(node)}</span>
                <span className="trace-event-subtitle">{traceSubtitle(node)}</span>
              </span>
              <span className="trace-event-time">{formatTraceTime(node.timestamp)}</span>
            </button>
          )) : (
            <div className="empty-inline">暂无 Trace 事件</div>
          )}
        </div>
      </section>
    </div>
  )
}

function PendingActionPanel({
  request,
  choices,
  disabled,
  onReply,
}: {
  request: PendingRequestState
  choices: PendingChoice[]
  disabled: boolean
  onReply: (reply: string) => void
}) {
  const fallbackReplies: PendingChoice[] = [
    { label: 'Continue', value: '继续', description: 'Tell Chrysalis the requested action is complete.' },
    { label: 'Skip', value: '跳过', description: 'Ask Chrysalis to choose another path.' },
  ]
  const quickReplies = request.kind === 'permission'
    ? choices
    : choices.length > 0
      ? choices
      : fallbackReplies
  const [selectedReplyIndex, setSelectedReplyIndex] = useState(0)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const selectedIndex = quickReplies.length > 0
    ? Math.min(selectedReplyIndex, quickReplies.length - 1)
    : -1

  useEffect(() => {
    setSelectedReplyIndex(0)
    window.setTimeout(() => panelRef.current?.focus(), 0)
  }, [request.id])

  function submitChoice(choice: PendingChoice | undefined): void {
    if (!choice || disabled) {
      return
    }
    onReply(choice.value || choice.label)
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    if (quickReplies.length === 0 || disabled) {
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      event.preventDefault()
      setSelectedReplyIndex((index) => (index + 1) % quickReplies.length)
      return
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      event.preventDefault()
      setSelectedReplyIndex((index) => (index - 1 + quickReplies.length) % quickReplies.length)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      submitChoice(quickReplies[selectedIndex])
    }
  }

  return (
    <div
      ref={panelRef}
      className={`pending-panel ${request.kind}`}
      tabIndex={disabled || quickReplies.length === 0 ? -1 : 0}
      onKeyDown={handleKeyDown}
    >
      <div className="pending-copy">
        <div className="pending-title">{request.title}</div>
        <div className="pending-question">{request.question || request.summary || 'Waiting for your response.'}</div>
        {request.summary && request.summary !== request.question ? <div className="pending-summary">{request.summary}</div> : null}
      </div>
      <div className="pending-choice-list" role="listbox" aria-label={request.title}>
        {quickReplies.map((choice, index) => (
          <button
            key={`${choice.value || choice.label}-${index}`}
            className={`pending-choice-row ${choice.value === 'deny' || choice.value === '跳过' ? 'danger' : ''} ${index === selectedIndex ? 'selected' : ''}`}
            type="button"
            disabled={disabled}
            title={choice.description || choice.label}
            role="option"
            aria-selected={index === selectedIndex}
            onFocus={() => setSelectedReplyIndex(index)}
            onClick={() => submitChoice(choice)}
          >
            <span className="pending-choice-cursor">{index === selectedIndex ? '>' : ' '}</span>
            <span className="pending-choice-index">{index + 1}.</span>
            <span className="pending-choice-copy">
              <span className="pending-choice-label">{choice.label}</span>
              {choice.description ? <span className="pending-choice-description">{choice.description}</span> : null}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function LogRow({
  message,
}: {
  message: ViewMessage
}) {
  const rowClass = `log-row ${message.kind}${message.streaming ? ' streaming' : ''}`
  const details = Array.isArray(message.details) ? message.details.filter(Boolean) : []
  const status = statusLabel(message.status)
  const cacheLabel = formatTurnCacheLabel(message.cache)
  const bodyClass = message.kind === 'user' || message.kind === 'assistant'
    ? 'log-body prose'
    : 'log-body mono'
  const fileChanges = message.kind === 'diff'
    ? Array.isArray(message.fileChanges) && message.fileChanges.length > 0
      ? message.fileChanges
      : message.path
        ? [buildFileChange(message.path, details[0] || message.body)]
        : []
    : []
  const fileTotals = fileChangeTotals(fileChanges)

  return (
    <article className={rowClass}>
      {message.kind === 'tool' ? (
        <details className="turn-shell" open={message.status === 'running'}>
          <summary className="turn-summary">
            <span className="turn-title">{message.title}</span>
            {cacheLabel ? (
              <span className="turn-cache" title={formatCacheTitle(message.cache)}>
                {cacheLabel}
              </span>
            ) : null}
            {status ? <span className={`turn-status ${message.status || ''}`}>{status}</span> : null}
          </summary>
          <div className="turn-body">
            <pre className="turn-preview">{message.body}</pre>
            {details.length > 0 ? (
              <div className="turn-details">
                {details.map((line, index) => (
                  <pre key={`${message.id}-${index}`}>{line}</pre>
                ))}
              </div>
            ) : null}
          </div>
        </details>
      ) : message.kind === 'diff' ? (
        <details className="file-change-shell" open={message.status === 'running'}>
          <summary className="file-change-summary">
            <span className="file-change-title">
              <FileText size={14} />
              <span>{message.title || formatFileChangeSummary(fileChanges)}</span>
            </span>
            <span className="file-change-stats" title={`${fileTotals.added} additions, ${fileTotals.removed} deletions`}>
              <span className="diff-added">+{fileTotals.added}</span>
              <span className="diff-removed">-{fileTotals.removed}</span>
            </span>
            {status ? <span className={`turn-status ${message.status || ''}`}>{status}</span> : null}
          </summary>
          <div className="file-change-list">
            {fileChanges.map((change) => (
              <details className="file-change-row" key={`${message.id}-${change.path}`} open={fileChanges.length === 1}>
                <summary className="file-change-row-summary">
                  <span className="file-change-path">
                    <FileText size={13} />
                    <span className="file-change-name">{change.name}</span>
                    <span className="file-change-full-path">{change.path}</span>
                  </span>
                  <span className="file-change-row-actions">
                    <span className="file-change-stats">
                      <span className="diff-added">+{change.added || 0}</span>
                      <span className="diff-removed">-{change.removed || 0}</span>
                    </span>
                  </span>
                </summary>
                <pre className="file-change-diff">
                  {change.diff.split(/\r?\n/).map((line, index) => {
                    const lineClass = line.startsWith('+++') || line.startsWith('---')
                      ? 'diff-meta'
                      : line.startsWith('+')
                        ? 'diff-added-line'
                        : line.startsWith('-')
                          ? 'diff-removed-line'
                          : line.startsWith('@@')
                            ? 'diff-hunk-line'
                            : ''
                    return (
                      <span key={`${change.path}-${index}`} className={lineClass}>
                        {line || ' '}
                        {'\n'}
                      </span>
                    )
                  })}
                </pre>
              </details>
            ))}
            {fileChanges.length === 0 ? <div className="empty-inline">没有可显示的文件差异</div> : null}
          </div>
        </details>
      ) : (
        <>
          <div className="log-head">
            <span className="log-icon">{kindIcon(message)}</span>
            <span className="log-title">{message.title}</span>
            {status ? <span className={`log-status ${message.status || ''}`}>{status}</span> : null}
          </div>
          {message.path ? (
            <div className="message-path-row">
              <span>{message.path}</span>
            </div>
          ) : null}
          {message.kind === 'assistant' ? (
            <div className={`markdown-body ${message.streaming ? 'streaming' : ''}`}>
              {renderMarkdown(message.body)}
              {message.streaming ? <span className="stream-caret">|</span> : null}
            </div>
          ) : (
            <pre className={bodyClass}>{message.body}{message.streaming ? '|' : ''}</pre>
          )}
          {details.length > 0 ? (
            <div className="log-details">
              {details.map((line, index) => (
                <pre key={`${message.id}-${index}`}>{line}</pre>
              ))}
            </div>
          ) : null}
        </>
      )}
    </article>
  )
}





function renderMarkdown(value: string): ReactNode[] {
  const lines = stripSummaryMarkup(value).split('\n')
  const nodes: ReactNode[] = []
  let index = 0
  let key = 0

  const isBlockStart = (line: string): boolean =>
    /^#{1,6}\s+/.test(line) ||
    /^```/.test(line) ||
    /^>\s?/.test(line) ||
    /^[-*+]\s+/.test(line) ||
    /^\d+\.\s+/.test(line)

  while (index < lines.length) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed) {
      index += 1
      continue
    }

    if (/^```/.test(trimmed)) {
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        codeLines.push(lines[index])
        index += 1
      }
      if (index < lines.length) {
        index += 1
      }
      nodes.push(
        <pre className="md-code" key={`md-${key++}`}>
          <code>{codeLines.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed)
    if (heading) {
      const level = Math.min(heading[1].length, 6)
      const Tag = `h${level}` as keyof JSX.IntrinsicElements
      nodes.push(<Tag key={`md-${key++}`}>{renderInlineMarkdown(heading[2], `h-${key}`)}</Tag>)
      index += 1
      continue
    }

    if (/^[-*+]\s+/.test(trimmed)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^[-*+]\s+/.test(lines[index].trim())) {
        const item = lines[index].trim().replace(/^[-*+]\s+/, '')
        items.push(<li key={`li-${key}-${items.length}`}>{renderInlineMarkdown(item, `li-${key}-${items.length}`)}</li>)
        index += 1
      }
      nodes.push(<ul key={`md-${key++}`}>{items}</ul>)
      continue
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: ReactNode[] = []
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
        const item = lines[index].trim().replace(/^\d+\.\s+/, '')
        items.push(<li key={`oli-${key}-${items.length}`}>{renderInlineMarkdown(item, `oli-${key}-${items.length}`)}</li>)
        index += 1
      }
      nodes.push(<ol key={`md-${key++}`}>{items}</ol>)
      continue
    }

    if (/^>\s?/.test(trimmed)) {
      const quoteLines: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index].trim())) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ''))
        index += 1
      }
      nodes.push(<blockquote key={`md-${key++}`}>{renderInlineMarkdown(quoteLines.join('\n'), `q-${key}`)}</blockquote>)
      continue
    }

    const paragraph: string[] = []
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index].trim())) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    nodes.push(<p key={`md-${key++}`}>{renderInlineMarkdown(paragraph.join('\n'), `p-${key}`)}</p>)
  }

  return nodes.length > 0 ? nodes : [<p key="md-empty" />]
}

function renderInlineMarkdown(value: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g
  let cursor = 0
  let index = 0
  for (const match of value.matchAll(pattern)) {
    if (match.index === undefined) {
      continue
    }
    if (match.index > cursor) {
      nodes.push(value.slice(cursor, match.index))
    }
    const token = match[0]
    if (token.startsWith('`')) {
      nodes.push(<code key={`${keyPrefix}-code-${index++}`}>{token.slice(1, -1)}</code>)
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={`${keyPrefix}-strong-${index++}`}>{token.slice(2, -2)}</strong>)
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (link) {
        nodes.push(
          <a key={`${keyPrefix}-link-${index++}`} href={link[2]} target="_blank" rel="noreferrer">
            {link[1]}
          </a>,
        )
      }
    }
    cursor = match.index + token.length
  }
  if (cursor < value.length) {
    nodes.push(value.slice(cursor))
  }
  return nodes
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  )
}

function TodoList({
  title,
  items,
  activeId = '',
  emptyLabel,
}: {
  title: string
  items: WorkingTodoItem[]
  activeId?: string
  emptyLabel: string
}) {
  return (
    <div className="plan-list">
      <div className="plan-list-title">{title}</div>
      <div className="plan-list-body">
        {items.map((item) => (
          <TodoRow key={item.id} item={item} active={todoItemActive(item, activeId)} />
        ))}
        {items.length === 0 ? <div className="empty-inline">{emptyLabel}</div> : null}
      </div>
    </div>
  )
}

function TodoRow({ item, active }: { item: WorkingTodoItem; active: boolean }) {
  const status = item.status || 'pending'
  const detail = item.note || status
  return (
    <div className={`plan-row ${todoItemStatusClass(status)} ${active ? 'active' : ''}`}>
      <div className="plan-row-main">
        <div className="plan-row-title">{item.title || item.id}</div>
        <div className="plan-row-detail">{detail}</div>
      </div>
      <div className={`plan-row-status ${todoItemStatusClass(status)}`}>{status}</div>
    </div>
  )
}

function CronJobRow({
  job,
  active,
  onSelect,
  onEdit,
  onRun,
  onToggle,
  onRemove,
}: {
  job: CronJob
  active: boolean
  onSelect: () => void
  onEdit: () => void
  onRun: () => void
  onToggle: () => void
  onRemove: () => void
}) {
  const state = job.state || {}
  const enabled = job.enabled !== false
  const running = Boolean(state.running)
  const scheduleText = job.schedule_display || cronScheduleLabel(job.schedule)
  const configPath = String(job.path || '').trim()
  return (
    <div className={`cron-job-row ${active ? 'active' : ''}`}>
      <button className="cron-job-main" type="button" onClick={onSelect}>
        <div className="cron-job-title-line">
          <span className="cron-job-title">{job.name || job.id}</span>
          {running ? <RefreshCw size={12} className="spin cron-running-icon" /> : null}
          {!enabled ? <span className="cron-job-tag muted">已停用</span> : null}
        </div>
        <div className="cron-job-meta">
          <span>{cronJobStatus(job)}</span>
          <span>{job.id}</span>
          <span>{scheduleText}</span>
          <span>下次 {state.next_run_at ? formatTimestamp(state.next_run_at) : '-'}</span>
          <span>上次 {cronLastStatusLabel(state.last_status)}</span>
        </div>
        {configPath ? (
          <div className="cron-job-path" title={configPath}>
            <FileText size={12} />
            <span>{configPath}</span>
          </div>
        ) : null}
      </button>
      <div className="cron-job-actions">
        <button className="icon-button" type="button" onClick={onEdit} disabled={running} title="编辑" aria-label="编辑">
          <Pencil size={14} />
        </button>
        <button className="icon-button" type="button" onClick={onRun} disabled={running} title="手动运行" aria-label="手动运行">
          <Play size={14} />
        </button>
        <button className="icon-button" type="button" onClick={onToggle} title={enabled ? '停用' : '启用'} aria-label={enabled ? '停用' : '启用'}>
          {enabled ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button className="icon-button danger" type="button" onClick={onRemove} disabled={running} title="删除" aria-label="删除">
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  )
}

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <div className="settings-section-title">{title}</div>
      <div className="settings-section-body">{children}</div>
    </section>
  )
}

function SettingsField({
  label,
  placeholder,
  value,
  onChange,
  multiline = false,
  secret = false,
  inputType = 'text',
  options,
  disabled = false,
}: {
  label: string
  placeholder: string
  value: string
  onChange: (value: string) => void
  multiline?: boolean
  secret?: boolean
  inputType?: string
  options?: { label: string; value: string }[]
  disabled?: boolean
}) {
  return (
    <label className={`settings-field ${multiline ? 'multiline' : ''}`}>
      <span className="field-label">{label}</span>
      {options ? (
        <select value={value} disabled={disabled} onChange={(event) => onChange(event.currentTarget.value)}>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : multiline ? (
        <textarea
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
      ) : (
        <input
          type={secret ? 'password' : inputType}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
      )}
    </label>
  )
}













function runtimeHistorySignature(sessionId: string, history: RuntimeSnapshot['history']): string {
  const last = history.length > 0 ? history[history.length - 1] : null
  if (!last) {
    return `${sessionId}:0`
  }
  const blocks = Array.isArray(last.blocks) ? last.blocks : []
  const lastText = readCanonicalText(last)
  const lastMarker = [
    last.role || '',
    blocks.length,
    lastText.length,
    lastText.slice(0, 48),
    lastText.slice(-48),
  ].join('|')
  return `${sessionId}:${history.length}:${lastMarker}`
}
