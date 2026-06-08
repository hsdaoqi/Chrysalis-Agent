export interface RuntimeResponse<T = unknown> {
  type: 'response'
  request_id: string
  ok: boolean
  data?: T
  error?: string
}

export interface RuntimeEvent {
  type: 'event'
  event: string
  [key: string]: unknown
}

export interface TraceEventNode {
  id: string
  sequence?: number
  timestamp?: string
  kind: string
  session_id?: string
  task_id?: string
  turn?: number
  model?: string
  model_id?: string
  protocol?: string
  call_id?: string
  tool?: string
  status?: string
  action?: string
  ok?: boolean
  need_user?: boolean
  cancelled?: boolean
  elapsed_ms?: number
  cost?: number
  usage?: Record<string, unknown>
  context?: ContextUsage & { blocks?: Record<string, number>; tools?: number }
  budget?: ContextAssembly
  args?: Record<string, unknown>
  observation?: Record<string, unknown>
  request?: Record<string, unknown>
  snapshot?: WorkingSnapshot
  final_preview?: string
  task_preview?: string
  content_preview?: string
  review_summary?: TaskReviewSummary
  tool_calls?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export interface SessionSummary {
  id: string
  title: string
  updated_at: string
  model: string
  turns: number
  pinned: boolean
  busy?: boolean
  task_id?: string
}

export interface WorkingPlanItem {
  id: string
  title: string
  status: string
  note?: string
  evidence?: string
}

export interface WorkingPlanSnapshot {
  goal?: string
  status?: string
  summary?: string
  blocker?: string
  rounds_since_plan?: number
  plan_reminder_interval?: number
  total_steps?: number
  pending_steps?: number
  completed_steps?: number
  total_acceptance_criteria?: number
  pending_acceptance_criteria?: number
  satisfied_acceptance_criteria?: number
  steps?: WorkingPlanItem[]
  acceptance_criteria?: WorkingPlanItem[]
  evidence?: string[]
  active_step_id?: string
  active_step_title?: string
}

export interface WorkingTodoItem {
  id: string
  title: string
  status: string
  note?: string
}

export interface WorkingTodoSnapshot {
  goal?: string
  rounds_since_todo?: number
  todo_reminder_interval?: number
  total_count?: number
  pending_count?: number
  completed_count?: number
  todos?: WorkingTodoItem[]
  active_todo_id?: string
  active_todo_title?: string
}

export interface WorkingSnapshot {
  todo?: WorkingTodoSnapshot
  plan?: WorkingPlanSnapshot
  key_info?: string
  related_sop?: string
  long_term_update_requested?: string
  todo_goal?: string
  todos?: WorkingTodoItem[]
  rounds_since_todo?: number
  todo_reminder_interval?: number
  total_count?: number
  pending_count?: number
  completed_count?: number
  active_todo_id?: string
  active_todo_title?: string
  plan_goal?: string
  plan_status?: string
  plan_summary?: string
  plan_blocker?: string
  plan_steps?: WorkingPlanItem[]
  plan_acceptance_criteria?: WorkingPlanItem[]
  plan_evidence?: string[]
  rounds_since_plan?: number
  plan_reminder_interval?: number
  plan_total_steps?: number
  plan_pending_steps?: number
  plan_completed_steps?: number
  plan_total_acceptance_criteria?: number
  plan_pending_acceptance_criteria?: number
  plan_satisfied_acceptance_criteria?: number
  plan_active_step_id?: string
  plan_active_step_title?: string
}

export interface AttachmentSummary {
  path: string
  name: string
  kind: string
  summary: string
}

export interface WorkspaceEntry {
  path: string
  name: string
  depth: number
  is_dir: boolean
  has_children: boolean
  expanded: boolean
  selected: boolean
  kind: string
  summary: string
}

export interface WorkspacePreview {
  path: string
  name: string
  kind: string
  summary: string
  content: string
  image_url: string
  can_preview: boolean
}

export interface WorkspaceSnapshot {
  root: string
  entries: WorkspaceEntry[]
  changes: WorkspaceEntry[]
  preview: WorkspacePreview
}

export interface LlmSettings {
  name?: string
  provider?: string
  api_key?: string
  base_url?: string
  model?: string
  wire_api?: string
  context_window?: number
  temperature?: number
  max_tokens?: number | null
  max_retries?: number
  timeout?: number
  proxy?: string
  thinking?: string
  thinking_budget?: number | null
}

export type PermissionLevel = 'locked' | 'balanced' | 'full'

export type ReviewKind = 'memory' | 'skill'
export type ReviewStatus = 'pending' | 'approved' | 'discarded'

export interface ReviewSummary {
  why?: string
  save_as?: string
  reuse?: string
  risk?: string
  quality?: string
  next_action?: string
  tools?: string[]
}

export interface TaskReviewCandidateSummary {
  id?: string
  kind?: ReviewKind | string
  title?: string
  target?: string
  label?: string
}

export interface TaskReviewSummary {
  has_candidates?: boolean
  count?: number
  headline?: string
  reason?: string
  items?: TaskReviewCandidateSummary[]
}

export interface ReviewItem {
  id: string
  raw_id?: string
  kind: ReviewKind
  status: ReviewStatus
  target?: string
  title?: string
  description?: string
  content?: string
  body?: string
  reason?: string
  evidence?: string[]
  decision?: Record<string, unknown>
  review_summary?: ReviewSummary
  why?: string
  save_as?: string
  reuse?: string
  risk?: string
  source_task?: string
  final_preview?: string
  session_id?: string
  created_at?: string
  updated_at?: string
  approved_at?: string | null
  discarded_at?: string | null
  path?: string
  artifact?: Record<string, unknown>
  stats?: Record<string, unknown>
  validation?: Record<string, unknown>
  skill_status?: string
  category?: string
  tags?: string[]
  tools?: string[]
}

export interface ReviewStats {
  total?: number
  pending?: number
  approved?: number
  discarded?: number
  memories?: number
  skills?: number
  active_skills?: number
  draft_skills?: number
  archived_skills?: number
  stale_skills?: number
}

export interface ReviewSnapshot {
  items?: ReviewItem[]
  stats?: ReviewStats
  errors?: string[]
  updated_at?: string
}

export interface ReviewPatch {
  title?: string
  description?: string
  content?: string
  target?: string
}

export interface SettingsSnapshot {
  enabled: boolean
  llm: LlmSettings
  system_prompt: string
  permission_level?: PermissionLevel
}

export interface CronSchedule {
  type?: 'once' | 'periodic'
  run_at?: string | null
  period?: string | null
  time?: string | null
  weekday?: number | string | null
  day?: number | null
  month?: number | null
  start_at?: string | null
  every_minutes?: number | null
  every_hours?: number | null
  every_days?: number | null
  every?: number | null
  n?: number | null
  interval_unit?: string | null
  interval_count?: number | null
}

export interface CronJobState {
  next_run_at?: string | null
  last_run_at?: string | null
  last_status?: string | null
  last_error?: string | null
  last_output?: string | null
  running?: boolean
  started_at?: string | null
  paused_at?: string | null
}

export interface CronJobRepeat {
  times?: number | null
  completed?: number | null
}

export interface CronJob {
  id: string
  name?: string | null
  enabled?: boolean
  schedule?: CronSchedule
  schedule_display?: string | null
  prompt?: string | null
  script?: string | null
  no_agent?: boolean
  context_from?: string[] | null
  workdir?: string | null
  deliver?: string | null
  max_delay_minutes?: number | null
  repeat?: CronJobRepeat | null
  created_at?: string | null
  state?: CronJobState | null
  path?: string | null
}

export interface CronDaemonSnapshot {
  running?: boolean
  interval_seconds?: number
  started_at?: string | null
  last_tick_at?: string | null
  last_count?: number
  last_error?: string | null
}

export interface CronSnapshot {
  daemon?: CronDaemonSnapshot
  jobs?: CronJob[]
}

export type GatewayStatus = 'not_configured' | 'configured' | 'running' | 'failed'

export interface GatewayPlatformSnapshot {
  id: string
  label: string
  status: GatewayStatus
  configured?: boolean
  running?: boolean
  pid?: number | null
  started_at?: string | null
  return_code?: number | null
  last_error?: string
  configuration_error?: string
  config_summary?: string
  required_config?: string[]
  launch_platform?: string
  missing_dependencies?: string[]
  install_hint?: string
  command?: string
  log_file?: string
}

export interface GatewayActivityEvent {
  id?: string
  kind?: string
  timestamp?: string
  task_id?: string
  session_id?: string
  task_preview?: string
  status?: string
  turn?: number
  tool?: string
  args?: unknown
  observation?: unknown
  thought?: string
  result?: unknown
  final?: string
  [key: string]: unknown
}

export interface GatewayActivity {
  task_id?: string
  session_id?: string
  session_key?: string
  platform?: string
  source?: Record<string, unknown>
  status?: string
  status_text?: string
  active?: boolean
  task_preview?: string
  model?: string
  stream?: string
  stream_kind?: string
  turn?: number
  events?: GatewayActivityEvent[]
  trace?: TraceEventNode[]
  working?: WorkingSnapshot
  started_at?: string
  updated_at?: string
  finished_at?: string
  cancel_requested?: boolean
  [key: string]: unknown
}

export interface GatewayActivitySnapshot {
  version?: number
  updated_at?: string
  activities?: GatewayActivity[]
}

export interface GatewaySnapshot {
  platforms?: GatewayPlatformSnapshot[]
  activities?: GatewayActivity[]
  updated_at?: string
}

export interface GatewayLogResponse {
  platform: string
  log_file?: string
  log?: string
}

export interface CanonicalBlock {
  type?: string
  text?: unknown
  name?: string
  id?: string
  arguments?: unknown
  content?: unknown
  tool_use_id?: string
  is_error?: boolean
}

export interface CanonicalMessage {
  role?: string
  content?: unknown
  blocks?: CanonicalBlock[]
  meta?: unknown
}

export interface RuntimeSnapshot {
  active_session_id: string
  busy: boolean
  model: string
  workspace_root: string
  sessions: SessionSummary[]
  history: CanonicalMessage[]
  trace?: TraceEventNode[]
  working: WorkingSnapshot
  context?: ContextUsage
  draft_text: string
  attachments: AttachmentSummary[]
  settings: SettingsSnapshot
  workspace: WorkspaceSnapshot
  session_filter: string
  pending_user_action?: Record<string, unknown> | null
  resumable_session?: boolean
  cron?: CronSnapshot
  reviews?: ReviewSnapshot
  gateway?: GatewaySnapshot
}

export interface ContextUsage {
  chars?: number
  tokens_estimate?: number
  budget_chars?: number
  context_window?: number
  ratio?: number
  soft_ratio?: number
  hard_ratio?: number
  messages?: number
  assembly?: ContextAssembly
}

export interface ContextAssemblySectionItem {
  name?: string
  id?: string
  source?: string
  reason?: string
  matched?: string[]
  reasons?: string[]
  score?: number
}

export interface ContextAssemblySection {
  name: string
  label?: string
  kind?: string
  stable?: boolean
  source?: string
  reason?: string
  budget_chars?: number
  allocated_chars?: number
  requested_chars?: number
  used_chars?: number
  truncated?: boolean
  items?: ContextAssemblySectionItem[]
}

export interface ContextAssembly {
  total_chars?: number
  used_chars?: number
  remaining_chars?: number
  section_count?: number
  sections?: ContextAssemblySection[]
}

export interface CacheUsageSummary {
  readTokens: number
  writeTokens: number
  promptTokens: number
  inputTokens: number
  totalTokens: number
  calls: number
  hitRate: number
}

export interface FileChangeSummary {
  path: string
  name: string
  diff: string
  added: number
  removed: number
}

export interface ViewMessage {
  id: string
  kind: 'user' | 'assistant' | 'tool' | 'status' | 'thinking' | 'info' | 'error' | 'diff' | 'usage' | 'warning' | 'system'
  role: string
  title: string
  body: string
  turn?: number
  taskId?: string
  meta?: string
  summary?: string
  details?: string[]
  status?: string
  streaming?: boolean
  path?: string
  cache?: CacheUsageSummary
  fileChanges?: FileChangeSummary[]
}

export interface ChrysalisApi {
  snapshot: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  refreshSessions: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  newSession: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  loadSession: (sessionId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  deleteSession: (sessionId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  runTask: (task: string, sessionId?: string) => Promise<RuntimeResponse<{ started: boolean; task_id: string; session_id: string }>>
  resumeTask: (sessionId?: string) => Promise<RuntimeResponse<{ started: boolean; task_id: string; session_id: string; resumed: boolean }>>
  guideTask: (sessionId: string, guidance: string) => Promise<RuntimeResponse<{ guided: boolean; task_id: string; session_id: string }>>
  resolvePendingUserAction: (sessionId: string, reply: string) => Promise<RuntimeResponse<{ started: boolean; task_id: string; session_id: string }>>
  cancelTask: (sessionId?: string) => Promise<RuntimeResponse<{ cancelled: boolean }>>
  renameSession: (sessionId: string, title: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  toggleSessionPinned: (sessionId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  setSessionFilter: (query: string) => Promise<RuntimeResponse>
  saveDraft: (text: string) => Promise<RuntimeResponse<{ saved: boolean; draft_text: string }>>
  addAttachment: (pathOrUrl: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  removeAttachment: (row: number) => Promise<RuntimeResponse<RuntimeSnapshot>>
  clearAttachments: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  openFiles: () => Promise<string[]>
  refreshWorkspace: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  selectWorkspacePath: (workspacePath: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  attachWorkspacePath: (workspacePath: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  loadSettingsText: () => Promise<RuntimeResponse<{ text: string }>>
  saveSettingsText: (raw: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  resetSettings: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  setPermissionLevel: (level: PermissionLevel) => Promise<RuntimeResponse<RuntimeSnapshot>>
  reviewUpdate: (itemId: string, patch: ReviewPatch) => Promise<RuntimeResponse<RuntimeSnapshot>>
  reviewApprove: (itemId: string, patch: ReviewPatch) => Promise<RuntimeResponse<RuntimeSnapshot>>
  reviewDiscard: (itemId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  gatewayStart: (platform: string, sharedGroups?: boolean) => Promise<RuntimeResponse<RuntimeSnapshot>>
  gatewayStop: (platform: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  gatewayLogs: (platform: string) => Promise<RuntimeResponse<GatewayLogResponse>>
  gatewayRefresh: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  createCronJob: (spec: CronJobCreateSpec) => Promise<RuntimeResponse<RuntimeSnapshot>>
  updateCronJob: (jobId: string, spec: CronJobCreateSpec) => Promise<RuntimeResponse<RuntimeSnapshot>>
  pauseCronJob: (jobId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  resumeCronJob: (jobId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  removeCronJob: (jobId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  runCronJob: (jobId: string) => Promise<RuntimeResponse<RuntimeSnapshot>>
  tickCron: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  startCronDaemon: (intervalSeconds?: number) => Promise<RuntimeResponse<RuntimeSnapshot>>
  stopCronDaemon: () => Promise<RuntimeResponse<RuntimeSnapshot>>
  minimizeWindow: () => Promise<void>
  toggleWindowMaximize: () => Promise<boolean>
  closeWindow: () => Promise<void>
  onEvent: (handler: (event: RuntimeEvent) => void) => () => void
}

export interface CronJobCreateSpec {
  id?: string
  name?: string
  schedule: CronSchedule
  prompt?: string
  script?: string
  no_agent?: boolean
  context_from?: string[] | string
  workdir?: string
  deliver?: string
  repeat_times?: number | null
  max_delay_minutes?: number | null
}

declare global {
  interface Window {
    chrysalis: ChrysalisApi
  }
}

export {}
