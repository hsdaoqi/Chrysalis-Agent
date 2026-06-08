import { app, BrowserWindow, dialog, ipcMain, Menu, type OpenDialogOptions } from 'electron'
import path from 'node:path'
import { RuntimeBridge, type RuntimeResponse, type RuntimeWireEvent } from './runtimeBridge'

let mainWindow: BrowserWindow | null = null
let runtime: RuntimeBridge | null = null

function createRuntime(): RuntimeBridge {
  const repoRoot = path.resolve(__dirname, '..', '..', '..')
  const bridge = new RuntimeBridge({
    isPackaged: app.isPackaged,
    repoRoot,
    resourcesPath: process.resourcesPath,
    userDataPath: app.getPath('userData'),
  })

  bridge.on('event', (payload: RuntimeWireEvent) => {
    mainWindow?.webContents.send('chrysalis:event', payload)
  })

  return bridge
}

function createWindow(): void {
  Menu.setApplicationMenu(null)

  mainWindow = new BrowserWindow({
    width: 1500,
    height: 920,
    minWidth: 1100,
    minHeight: 720,
    title: 'Chrysalis Desktop',
    backgroundColor: '#0b0d10',
    frame: false,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  })

  const devServerUrl = process.env.VITE_DEV_SERVER_URL
  if (devServerUrl) {
    mainWindow.loadURL(devServerUrl)
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function installIpcHandlers(): void {
  const safeRequest = async (
    type: string,
    payload?: Record<string, unknown>,
  ): Promise<RuntimeResponse> => {
    try {
      return await runtime!.request(type, payload)
    } catch (error) {
      return {
        type: 'response',
        request_id: '',
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      }
    }
  }

  ipcMain.handle('chrysalis:snapshot', () => safeRequest('snapshot'))
  ipcMain.handle('chrysalis:refreshSessions', () => safeRequest('refresh_sessions'))
  ipcMain.handle('chrysalis:newSession', () => safeRequest('new_session'))
  ipcMain.handle('chrysalis:cancelTask', (_event, sessionId?: string) =>
    safeRequest('cancel_task', { session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:loadSession', (_event, sessionId: string) =>
    safeRequest('load_session', { session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:deleteSession', (_event, sessionId: string) =>
    safeRequest('delete_session', { session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:runTask', (_event, task: string, sessionId?: string) =>
    safeRequest('run_task', { task, session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:resumeTask', (_event, sessionId?: string) =>
    safeRequest('resume_task', { session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:guideTask', (_event, sessionId: string, guidance: string) =>
    safeRequest('guide_task', { session_id: sessionId, guidance }),
  )
  ipcMain.handle('chrysalis:resolvePendingUserAction', (_event, sessionId: string, reply: string) =>
    safeRequest('resolve_pending_user_action', { session_id: sessionId, reply }),
  )
  ipcMain.handle('chrysalis:renameSession', (_event, sessionId: string, title: string) =>
    safeRequest('rename_session', { session_id: sessionId, title }),
  )
  ipcMain.handle('chrysalis:toggleSessionPinned', (_event, sessionId: string) =>
    safeRequest('toggle_session_pinned', { session_id: sessionId }),
  )
  ipcMain.handle('chrysalis:setSessionFilter', (_event, query: string) =>
    safeRequest('set_session_filter', { query }),
  )
  ipcMain.handle('chrysalis:saveDraft', (_event, text: string) =>
    safeRequest('save_draft', { text }),
  )
  ipcMain.handle('chrysalis:addAttachment', (_event, pathOrUrl: string) =>
    safeRequest('add_attachment', { path: pathOrUrl }),
  )
  ipcMain.handle('chrysalis:removeAttachment', (_event, row: number) =>
    safeRequest('remove_attachment', { row }),
  )
  ipcMain.handle('chrysalis:clearAttachments', () => safeRequest('clear_attachments'))
  ipcMain.handle('chrysalis:refreshWorkspace', () => safeRequest('refresh_workspace'))
  ipcMain.handle('chrysalis:selectWorkspacePath', (_event, workspacePath: string) =>
    safeRequest('select_workspace_path', { path: workspacePath }),
  )
  ipcMain.handle('chrysalis:attachWorkspacePath', (_event, workspacePath: string) =>
    safeRequest('attach_workspace_path', { path: workspacePath }),
  )
  ipcMain.handle('chrysalis:loadSettingsText', () => safeRequest('load_settings_text'))
  ipcMain.handle('chrysalis:saveSettingsText', (_event, raw: string) =>
    safeRequest('save_settings_text', { raw }),
  )
  ipcMain.handle('chrysalis:resetSettings', () => safeRequest('reset_settings'))
  ipcMain.handle('chrysalis:setPermissionLevel', (_event, level: string) =>
    safeRequest('set_permission_level', { level }),
  )
  ipcMain.handle('chrysalis:reviewUpdate', (_event, itemId: string, patch: Record<string, unknown> = {}) =>
    safeRequest('review_update', { id: itemId, ...patch }),
  )
  ipcMain.handle('chrysalis:reviewApprove', (_event, itemId: string, patch: Record<string, unknown> = {}) =>
    safeRequest('review_approve', { id: itemId, ...patch }),
  )
  ipcMain.handle('chrysalis:reviewDiscard', (_event, itemId: string) =>
    safeRequest('review_discard', { id: itemId }),
  )
  ipcMain.handle('chrysalis:gatewayStart', (_event, platform: string, sharedGroups?: boolean) =>
    safeRequest('gateway_start', { platform, shared_groups: sharedGroups }),
  )
  ipcMain.handle('chrysalis:gatewayStop', (_event, platform: string) =>
    safeRequest('gateway_stop', { platform }),
  )
  ipcMain.handle('chrysalis:gatewayLogs', (_event, platform: string) =>
    safeRequest('gateway_logs', { platform }),
  )
  ipcMain.handle('chrysalis:gatewayRefresh', () => safeRequest('gateway_refresh'))
  ipcMain.handle('chrysalis:createCronJob', (_event, spec: Record<string, unknown>) =>
    safeRequest('cron_create', { spec }),
  )
  ipcMain.handle('chrysalis:updateCronJob', (_event, jobId: string, spec: Record<string, unknown>) =>
    safeRequest('cron_update', { job_id: jobId, spec }),
  )
  ipcMain.handle('chrysalis:pauseCronJob', (_event, jobId: string) =>
    safeRequest('cron_pause', { job_id: jobId }),
  )
  ipcMain.handle('chrysalis:resumeCronJob', (_event, jobId: string) =>
    safeRequest('cron_resume', { job_id: jobId }),
  )
  ipcMain.handle('chrysalis:removeCronJob', (_event, jobId: string) =>
    safeRequest('cron_remove', { job_id: jobId }),
  )
  ipcMain.handle('chrysalis:runCronJob', (_event, jobId: string) =>
    safeRequest('cron_run', { job_id: jobId }),
  )
  ipcMain.handle('chrysalis:tickCron', () => safeRequest('cron_tick'))
  ipcMain.handle('chrysalis:startCronDaemon', (_event, intervalSeconds?: number) =>
    safeRequest('cron_daemon_start', { interval_seconds: intervalSeconds }),
  )
  ipcMain.handle('chrysalis:stopCronDaemon', () => safeRequest('cron_daemon_stop'))

  ipcMain.handle('chrysalis:openFiles', async () => {
    const options: OpenDialogOptions = {
      title: 'Attach files',
      properties: ['openFile', 'multiSelections'],
    }
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options)
    return result.canceled ? [] : result.filePaths
  })

  ipcMain.handle('chrysalis:window:minimize', () => {
    mainWindow?.minimize()
  })
  ipcMain.handle('chrysalis:window:toggleMaximize', () => {
    if (!mainWindow) {
      return false
    }
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize()
    } else {
      mainWindow.maximize()
    }
    return mainWindow.isMaximized()
  })
  ipcMain.handle('chrysalis:window:close', () => {
    mainWindow?.close()
  })
}

app.whenReady().then(() => {
  runtime = createRuntime()
  installIpcHandlers()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('before-quit', () => {
  runtime?.shutdown()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
