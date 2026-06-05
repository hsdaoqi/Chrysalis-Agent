import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import path from 'node:path'
import readline from 'node:readline'

type PendingRequest = {
  resolve: (value: RuntimeResponse) => void
  reject: (error: Error) => void
  timer: NodeJS.Timeout
}

export type RuntimeResponse = {
  type: 'response'
  request_id: string
  ok: boolean
  data?: unknown
  error?: string
}

export type RuntimeWireEvent = {
  type: 'event'
  event: string
  [key: string]: unknown
}

type RuntimeBridgeOptions = {
  isPackaged: boolean
  repoRoot: string
  resourcesPath: string
  userDataPath: string
}

export class RuntimeBridge extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null
  private pending = new Map<string, PendingRequest>()

  constructor(private readonly options: RuntimeBridgeOptions) {
    super()
  }

  request(type: string, payload: Record<string, unknown> = {}): Promise<RuntimeResponse> {
    this.start()

    if (!this.child || !this.child.stdin.writable) {
      return Promise.resolve({
        type: 'response',
        request_id: '',
        ok: false,
        error: 'Runtime process is not writable.',
      })
    }

    const requestId = randomUUID()
    const message = JSON.stringify({
      ...payload,
      type,
      request_id: requestId,
    })

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new Error(`Runtime request timed out: ${type}`))
      }, 30_000)

      this.pending.set(requestId, { resolve, reject, timer })
      this.child?.stdin.write(`${message}\n`, 'utf8', (error) => {
        if (!error) {
          return
        }
        clearTimeout(timer)
        this.pending.delete(requestId)
        reject(error)
      })
    })
  }

  start(): void {
    if (this.child && !this.child.killed) {
      return
    }

    const runtime = this.resolveRuntime()
    const child = spawn(runtime.command, runtime.args, {
      cwd: runtime.cwd,
      env: runtime.env,
      stdio: 'pipe',
      windowsHide: true,
    })

    this.child = child

    readline.createInterface({ input: child.stdout }).on('line', (line) => {
      this.handleLine(line)
    })

    child.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8').trim()
      if (!text) {
        return
      }
      console.error(`[chrysalis-runtime] ${text}`)
      this.emit('event', {
        type: 'event',
        event: 'runtime_stderr',
        text,
      } satisfies RuntimeWireEvent)
    })

    child.on('exit', (code, signal) => {
      this.child = null
      this.rejectAll(new Error(`Runtime exited with code=${code ?? 'null'} signal=${signal ?? 'null'}`))
      this.emit('event', {
        type: 'event',
        event: 'runtime_disconnected',
        code,
        signal,
      } satisfies RuntimeWireEvent)
    })
  }

  shutdown(): void {
    const child = this.child
    this.child = null
    this.rejectAll(new Error('Runtime bridge is shutting down.'))
    if (!child || child.killed) {
      return
    }
    child.kill()
  }

  private resolveRuntime(): {
    command: string
    args: string[]
    cwd: string
    env: NodeJS.ProcessEnv
  } {
    if (this.options.isPackaged) {
      const runtimeName = process.platform === 'win32' ? 'chrysalis-runtime.exe' : 'chrysalis-runtime'
      const command = path.join(this.options.resourcesPath, 'runtime', runtimeName)
      if (!fs.existsSync(command)) {
        throw new Error(`Bundled Chrysalis runtime not found: ${command}`)
      }

      const projectRoot = path.join(this.options.userDataPath, 'runtime-root')
      fs.mkdirSync(projectRoot, { recursive: true })

      return {
        command,
        args: [],
        cwd: projectRoot,
        env: {
          ...process.env,
          CHRYSALIS_PROJECT_ROOT: projectRoot,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUNBUFFERED: '1',
          PYTHONUTF8: '1',
        },
      }
    }

    const command = process.env.CHRYSALIS_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
    return {
      command,
      args: ['-m', 'chrysalis.electron_runtime'],
      cwd: this.options.repoRoot,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
      },
    }
  }

  private handleLine(line: string): void {
    const trimmed = line.trim()
    if (!trimmed) {
      return
    }

    let payload: unknown
    try {
      payload = JSON.parse(trimmed)
    } catch {
      console.warn(`[chrysalis-runtime] ${trimmed}`)
      return
    }

    if (isRuntimeResponse(payload)) {
      const pending = this.pending.get(payload.request_id)
      if (!pending) {
        return
      }
      clearTimeout(pending.timer)
      this.pending.delete(payload.request_id)
      pending.resolve(payload)
      return
    }

    if (isRuntimeEvent(payload)) {
      this.emit('event', payload)
    }
  }

  private rejectAll(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
  }
}

function isRuntimeResponse(value: unknown): value is RuntimeResponse {
  return Boolean(
    value &&
      typeof value === 'object' &&
      (value as RuntimeResponse).type === 'response' &&
      typeof (value as RuntimeResponse).request_id === 'string',
  )
}

function isRuntimeEvent(value: unknown): value is RuntimeWireEvent {
  return Boolean(
    value &&
      typeof value === 'object' &&
      (value as RuntimeWireEvent).type === 'event' &&
      typeof (value as RuntimeWireEvent).event === 'string',
  )
}
