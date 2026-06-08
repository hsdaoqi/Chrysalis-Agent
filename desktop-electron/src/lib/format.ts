import type { FileChangeSummary } from '../types'

export function formatTimestamp(value: string): string {
  if (!value) {
    return ''
  }
  if (value.includes('T')) {
    return value.replace('T', ' ').slice(0, 16)
  }
  return value
}
export function formatSessionAge(value: string): string {
  if (!value) {
    return ''
  }
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const timestamp = new Date(normalized).getTime()
  if (!Number.isFinite(timestamp)) {
    return formatTimestamp(value)
  }
  const elapsedMs = Math.max(0, Date.now() - timestamp)
  const minutes = Math.floor(elapsedMs / 60_000)
  if (minutes < 1) {
    return '刚刚'
  }
  if (minutes < 60) {
    return `${minutes}分`
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours}时`
  }
  const days = Math.floor(hours / 24)
  if (days < 30) {
    return `${days}天`
  }
  const months = Math.floor(days / 30)
  if (months < 12) {
    return `${months}月`
  }
  return `${Math.floor(months / 12)}年`
}
export function formatBytes(size: string | number): string {
  const value = typeof size === 'number' ? size : Number(size) || 0
  const units = ['B', 'KB', 'MB', 'GB']
  let current = Math.max(0, value)
  for (let index = 0; index < units.length; index += 1) {
    if (current < 1024 || index === units.length - 1) {
      return index === 0 ? `${Math.trunc(current)} ${units[index]}` : `${current.toFixed(1)} ${units[index]}`
    }
    current /= 1024
  }
  return `${Math.trunc(value)} B`
}
export function formatCompactCount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0'
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}m`
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`
  }
  return String(Math.round(value))
}
export function stringifyValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (value == null) {
    return ''
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
export function compactText(value: unknown, limit: number): string {
  const text = stringifyValue(value)
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\s+/g, ' ')
    .trim()
  if (text.length <= limit) {
    return text
  }
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`
}
export function fileNameFromPath(path: string): string {
  const cleanPath = String(path || '').trim()
  if (!cleanPath) {
    return 'unknown'
  }
  const parts = cleanPath.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] || cleanPath
}
export function fileChangeKey(path: string): string {
  return String(path || '').replace(/\\/g, '/').trim().toLowerCase()
}
export function diffStats(diffText: string): { added: number; removed: number } {
  return String(diffText || '').split(/\r?\n/).reduce((total, line) => {
    if (line.startsWith('+++') || line.startsWith('---')) {
      return total
    }
    if (line.startsWith('+')) {
      return { ...total, added: total.added + 1 }
    }
    if (line.startsWith('-')) {
      return { ...total, removed: total.removed + 1 }
    }
    return total
  }, { added: 0, removed: 0 })
}
export function buildFileChange(path: string, diffText: string): FileChangeSummary {
  const stats = diffStats(diffText)
  const cleanPath = String(path || 'unknown').trim() || 'unknown'
  return {
    path: cleanPath,
    name: fileNameFromPath(cleanPath),
    diff: String(diffText || ''),
    added: stats.added,
    removed: stats.removed,
  }
}
export function fileChangeTotals(changes: FileChangeSummary[]): { added: number; removed: number } {
  return changes.reduce((total, change) => ({
    added: total.added + Math.max(0, change.added || 0),
    removed: total.removed + Math.max(0, change.removed || 0),
  }), { added: 0, removed: 0 })
}
export function formatFileChangeSummary(changes: FileChangeSummary[]): string {
  const count = changes.length
  const noun = count === 1 ? '个文件' : '个文件'
  return `已编辑 ${count} ${noun}`
}
export function formatFileChangeBody(changes: FileChangeSummary[]): string {
  if (changes.length === 0) {
    return ''
  }
  return changes
    .map((change) => `${change.name}  +${change.added || 0} -${change.removed || 0}`)
    .join('\n')
}
export function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max))
}
export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}
export function formatMs(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0ms'
  }
  if (value < 1000) {
    return `${Math.round(value)}ms`
  }
  if (value < 60_000) {
    return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`
  }
  const minutes = Math.floor(value / 60_000)
  const seconds = Math.round((value % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}
export function formatCost(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '$0'
  }
  if (value < 0.01) {
    return `$${value.toFixed(4)}`
  }
  return `$${value.toFixed(2)}`
}
export function formatTraceTime(timestamp?: string): string {
  if (!timestamp) {
    return ''
  }
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) {
    return timestamp
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
export function formatReviewTime(value?: string | null): string {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}
export function parseIntOrFallback(value: string, fallback: number): number {
  const parsed = Number.parseInt(String(value).trim(), 10)
  return Number.isNaN(parsed) ? fallback : parsed
}
export function parseFloatOrFallback(value: string, fallback: number): number {
  const parsed = Number.parseFloat(String(value).trim())
  return Number.isNaN(parsed) ? fallback : parsed
}
export function toTraceNumber(value: unknown): number {
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : 0
}
