import { compactText, isRecord, stringifyValue } from './format'
import type { PendingChoice, PendingRequestState } from '../app-types'

export function createPendingRequest(sessionId: string, raw: unknown): PendingRequestState | null {
  if (!sessionId || !isRecord(raw)) {
    return null
  }
  const source = isRecord(raw.result) ? raw.result : raw
  if (!source.need_user) {
    return null
  }
  const permission = Boolean(source.permission_request)
  const rawQuestion = stringifyValue(source.question || source.final || source.prompt || source.reason || source.message || '').trim()
  const tool = stringifyValue(source.tool || '').trim()
  const risk = stringifyValue(source.risk || '').trim()
  const reason = stringifyValue(source.reason || '').trim()
  const summary = permission ? permissionSummary(source) : stringifyValue(source.message || reason || '').trim()
  const inlineChoices = permission ? { question: rawQuestion, choices: [] as PendingChoice[] } : extractInlinePendingChoices(rawQuestion)
  const question = inlineChoices.question || rawQuestion
  const choices = pendingChoices(source, inlineChoices.choices)
  const id = [
    sessionId,
    permission ? 'permission' : 'ask_user',
    tool,
    risk,
    question,
    choices.map((choice) => choice.label).join('|'),
  ].join('::')
  return {
    id,
    sessionId,
    kind: permission ? 'permission' : 'ask_user',
    title: permission ? 'Approval required' : 'Waiting for input',
    question,
    summary,
    tool,
    risk,
    reason,
    choices,
  }
}
export function pendingChoices(source: Record<string, unknown>, fallbackChoices: PendingChoice[] = []): PendingChoice[] {
  const fromOptions = Array.isArray(source.options)
    ? source.options
    : []
  const fromCandidates = Array.isArray(source.candidates)
    ? source.candidates
    : []
  const rawChoices = fromOptions.length > 0 ? fromOptions : fromCandidates
  const structuredChoices = rawChoices
    .map((choice) => {
      if (isRecord(choice)) {
        const label = stringifyValue(choice.label || choice.title || choice.name || choice.text || choice.value || choice.id || '').trim()
        const value = stringifyValue(choice.value || choice.id || label).trim()
        const description = stringifyValue(choice.description || '').trim()
        return label ? { label, value, description } : null
      }
      const label = stringifyValue(choice).trim()
      return label ? { label, value: label, description: '' } : null
    })
    .filter((choice): choice is PendingChoice => Boolean(choice))
  return dedupePendingChoices(structuredChoices.length > 0 ? structuredChoices : fallbackChoices)
}
export function extractInlinePendingChoices(text: string): { question: string; choices: PendingChoice[] } {
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
  const consumed = new Set<number>()
  const choices: PendingChoice[] = []
  let currentChoice: PendingChoice | null = null
  const descriptionParts: string[][] = []

  lines.forEach((line, index) => {
    const match = line.match(/^\s*(?:[>*-]\s*)?(?:\(?([0-9]{1,2}|[A-Za-z])\)?[.)、:：])\s+(.+?)\s*$/)
    if (match) {
      const label = match[2].trim()
      if (label) {
        currentChoice = { label, value: label, description: '' }
        choices.push(currentChoice)
        descriptionParts.push([])
        consumed.add(index)
      }
      return
    }

    if (currentChoice && line.trim() && /^\s{2,}\S/.test(line)) {
      descriptionParts[descriptionParts.length - 1]?.push(line.trim())
      consumed.add(index)
      return
    }

    if (line.trim()) {
      currentChoice = null
    }
  })

  if (choices.length < 2) {
    return { question: text, choices: [] }
  }

  const parsedChoices = choices.map((choice, index) => ({
    ...choice,
    description: descriptionParts[index]?.join('\n').trim() || choice.description,
  }))
  const question = lines.filter((_, index) => !consumed.has(index)).join('\n').trim()
  return {
    question,
    choices: dedupePendingChoices(parsedChoices),
  }
}
export function dedupePendingChoices(choices: PendingChoice[]): PendingChoice[] {
  const seen = new Set<string>()
  const deduped: PendingChoice[] = []
  choices.forEach((choice) => {
    const label = choice.label.trim()
    const value = (choice.value || label).trim()
    if (!label) {
      return
    }
    const key = `${label}\u0000${value}`.toLowerCase()
    if (seen.has(key)) {
      return
    }
    seen.add(key)
    deduped.push({
      label,
      value,
      description: choice.description.trim(),
    })
  })
  return deduped
}
export function permissionSummary(source: Record<string, unknown>): string {
  const tool = stringifyValue(source.tool || 'command').trim() || 'command'
  const details = isRecord(source.details) ? source.details : {}
  if (tool === 'code_run') {
    const codeType = stringifyValue(details.code_type || 'code').trim() || 'code'
    const preview = stringifyValue(details.preview || '').trim()
    const firstLine = preview.split('\n').find((line) => line.trim()) || ''
    return `${codeType} command${firstLine ? `  ${compactText(firstLine, 120)}` : ''}`
  }
  if (tool === 'file_write' || tool === 'file_patch' || tool === 'file_read') {
    return stringifyValue(details.path || source.question || tool).trim()
  }
  if (tool === 'web_scan') {
    return stringifyValue(details.url || '(current tab)').trim()
  }
  return stringifyValue(source.question || tool).trim()
}
