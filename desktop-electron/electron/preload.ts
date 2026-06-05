import { contextBridge, ipcRenderer } from 'electron'

type RuntimeResponse = {
  type: 'response'
  request_id: string
  ok: boolean
  data?: unknown
  error?: string
}

type RuntimeEvent = {
  type: 'event'
  event: string
  [key: string]: unknown
}

contextBridge.exposeInMainWorld('chrysalis', {
  snapshot: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:snapshot'),
  refreshSessions: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:refreshSessions'),
  newSession: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:newSession'),
  loadSession: (sessionId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:loadSession', sessionId),
  deleteSession: (sessionId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:deleteSession', sessionId),
  runTask: (task: string, sessionId?: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:runTask', task, sessionId),
  resolvePendingUserAction: (sessionId: string, reply: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:resolvePendingUserAction', sessionId, reply),
  cancelTask: (sessionId?: string): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:cancelTask', sessionId),
  renameSession: (sessionId: string, title: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:renameSession', sessionId, title),
  toggleSessionPinned: (sessionId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:toggleSessionPinned', sessionId),
  setSessionFilter: (query: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:setSessionFilter', query),
  saveDraft: (text: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:saveDraft', text),
  addAttachment: (pathOrUrl: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:addAttachment', pathOrUrl),
  removeAttachment: (row: number): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:removeAttachment', row),
  clearAttachments: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:clearAttachments'),
  openFiles: (): Promise<string[]> => ipcRenderer.invoke('chrysalis:openFiles'),
  refreshWorkspace: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:refreshWorkspace'),
  selectWorkspacePath: (workspacePath: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:selectWorkspacePath', workspacePath),
  attachWorkspacePath: (workspacePath: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:attachWorkspacePath', workspacePath),
  loadSettingsText: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:loadSettingsText'),
  saveSettingsText: (raw: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:saveSettingsText', raw),
  resetSettings: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:resetSettings'),
  setPermissionLevel: (level: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:setPermissionLevel', level),
  reviewUpdate: (itemId: string, patch: Record<string, unknown>): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:reviewUpdate', itemId, patch),
  reviewApprove: (itemId: string, patch: Record<string, unknown>): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:reviewApprove', itemId, patch),
  reviewDiscard: (itemId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:reviewDiscard', itemId),
  gatewayStart: (platform: string, sharedGroups?: boolean): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:gatewayStart', platform, sharedGroups),
  gatewayStop: (platform: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:gatewayStop', platform),
  gatewayLogs: (platform: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:gatewayLogs', platform),
  gatewayRefresh: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:gatewayRefresh'),
  createCronJob: (spec: Record<string, unknown>): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:createCronJob', spec),
  updateCronJob: (jobId: string, spec: Record<string, unknown>): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:updateCronJob', jobId, spec),
  pauseCronJob: (jobId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:pauseCronJob', jobId),
  resumeCronJob: (jobId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:resumeCronJob', jobId),
  removeCronJob: (jobId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:removeCronJob', jobId),
  runCronJob: (jobId: string): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:runCronJob', jobId),
  tickCron: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:tickCron'),
  startCronDaemon: (intervalSeconds?: number): Promise<RuntimeResponse> =>
    ipcRenderer.invoke('chrysalis:startCronDaemon', intervalSeconds),
  stopCronDaemon: (): Promise<RuntimeResponse> => ipcRenderer.invoke('chrysalis:stopCronDaemon'),
  minimizeWindow: (): Promise<void> => ipcRenderer.invoke('chrysalis:window:minimize'),
  toggleWindowMaximize: (): Promise<boolean> => ipcRenderer.invoke('chrysalis:window:toggleMaximize'),
  closeWindow: (): Promise<void> => ipcRenderer.invoke('chrysalis:window:close'),
  onEvent: (handler: (event: RuntimeEvent) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: RuntimeEvent) => handler(payload)
    ipcRenderer.on('chrysalis:event', listener)
    return () => ipcRenderer.removeListener('chrysalis:event', listener)
  },
})
