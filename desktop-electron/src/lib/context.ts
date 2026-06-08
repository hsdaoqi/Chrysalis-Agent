import type { ContextAssembly, ContextAssemblySection, ContextAssemblySectionItem } from '../types'
import { formatCompactCount, isRecord, stringifyValue, toTraceNumber } from './format'

export function contextSectionRecallItems(section: ContextAssemblySection): string[] {
  if (!Array.isArray(section.items) || section.items.length === 0) {
    return []
  }
  return section.items
    .slice(0, 5)
    .map((item, index) => contextRecallItemLine(item, index))
    .filter(Boolean)
}
export function contextRecallItemLine(item: ContextAssemblySectionItem, index: number): string {
  const name = stringifyValue(item.name || item.id || item.source || `item ${index + 1}`).trim()
  const source = stringifyValue(item.source || '').trim()
  const score = toTraceNumber(item.score)
  const reasons = Array.isArray(item.reasons)
    ? item.reasons.map((reason) => stringifyValue(reason).trim()).filter(Boolean)
    : []
  const matched = Array.isArray(item.matched)
    ? item.matched.map((match) => stringifyValue(match).trim()).filter(Boolean)
    : []
  const reason = reasons.length > 0
    ? reasons.slice(0, 3).join(', ')
    : matched.length > 0
      ? `matched ${matched.slice(0, 4).join(', ')}`
      : stringifyValue(item.reason || '').trim()
  const parts = [
    name,
    source && source !== name ? source : '',
    score > 0 ? `score ${score.toFixed(2)}` : '',
    reason,
  ].filter(Boolean)
  return parts.join(' - ')
}
export function contextAssemblySections(assembly: ContextAssembly | null | undefined): ContextAssemblySection[] {
  return Array.isArray(assembly?.sections) ? assembly.sections.filter((section) => isRecord(section)) : []
}
export function contextAssemblyLine(assembly: ContextAssembly | null | undefined): string {
  if (!assembly) {
    return ''
  }
  const sections = contextAssemblySections(assembly)
  const total = toTraceNumber(assembly.total_chars)
  const used = toTraceNumber(assembly.used_chars)
  const topSections = sections
    .slice(0, 5)
    .map((section) => section.label || section.name)
    .filter(Boolean)
    .join(', ')
  const parts = [
    used > 0 ? `${formatCompactCount(used)}${total > 0 ? `/${formatCompactCount(total)}` : ''} chars` : '',
    sections.length > 0 ? `${formatCompactCount(sections.length)} sections` : '',
    topSections ? `loaded ${topSections}` : '',
  ].filter(Boolean)
  return parts.join(' / ')
}
export function contextSectionBudgetLine(section: ContextAssemblySection): string {
  const used = toTraceNumber(section.used_chars)
  const allocated = toTraceNumber(section.allocated_chars || section.budget_chars)
  const requested = toTraceNumber(section.requested_chars)
  const percent = allocated > 0 ? Math.round(Math.min(1, used / allocated) * 100) : 0
  const parts = [
    `${formatCompactCount(used)}${allocated > 0 ? `/${formatCompactCount(allocated)}` : ''} chars`,
    allocated > 0 ? `${percent}%` : '',
    requested > used ? `${formatCompactCount(requested)} requested` : '',
    section.truncated ? 'truncated' : '',
    section.stable ? 'stable' : 'runtime',
  ].filter(Boolean)
  return parts.join(' / ')
}
export function contextSectionReason(section: ContextAssemblySection): string {
  const itemHints = (section.items || [])
    .slice(0, 3)
    .map((item) => {
      const name = stringifyValue(item.name || item.id || item.source || '').trim()
      const score = toTraceNumber(item.score)
      const reason = Array.isArray(item.reasons) && item.reasons.length > 0
        ? item.reasons.slice(0, 2).join(', ')
        : Array.isArray(item.matched) && item.matched.length > 0
          ? `matched ${item.matched.slice(0, 3).join(', ')}`
          : stringifyValue(item.reason || '').trim()
      return [name, score > 0 ? `score ${score.toFixed(2)}` : '', reason].filter(Boolean).join(' - ')
    })
    .filter(Boolean)
  return [section.reason, section.source, ...itemHints].filter(Boolean).join(' / ')
}
export function contextAssemblyDetailText(assembly: ContextAssembly | null | undefined): string {
  const sections = contextAssemblySections(assembly)
  if (sections.length === 0) {
    return ''
  }
  return sections
    .map((section) => {
      const title = section.label || section.name
      const reason = contextSectionReason(section)
      return `${title}: ${contextSectionBudgetLine(section)}${reason ? `; ${reason}` : ''}`
    })
    .join('\n')
}
