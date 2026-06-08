import type { ReviewItem, ReviewSummary, ReviewStatus } from '../types'

export const MEMORY_TARGET_OPTIONS = [
  { value: 'fact', label: '项目事实' },
  { value: 'user_profile', label: '用户偏好' },
  { value: 'sop', label: 'SOP/技能笔记' },
]
import type { ReviewEditState } from '../app-types'
import { compactText, isRecord, stringifyValue, toTraceNumber } from './format'

export function createReviewEditState(item?: ReviewItem | null): ReviewEditState {
  return {
    itemId: item?.id || '',
    title: item?.title || '',
    target: item?.target || item?.category || '',
    description: item?.description || '',
    content: item?.content || item?.body || '',
  }
}
export function normalizedReviewSummary(item: ReviewItem): ReviewSummary {
  const summary = item.review_summary || {}
  const tools = Array.isArray(summary.tools) && summary.tools.length > 0
    ? summary.tools.map((tool) => stringifyValue(tool).trim()).filter(Boolean)
    : Array.isArray(item.tools)
      ? item.tools.map((tool) => stringifyValue(tool).trim()).filter(Boolean)
      : []
  return {
    why: compactText(item.why || summary.why || item.reason || item.description || '', 260),
    save_as: compactText(item.save_as || summary.save_as || reviewTargetLabel(item), 240),
    reuse: compactText(item.reuse || summary.reuse || '', 280),
    risk: compactText(item.risk || summary.risk || '', 240),
    quality: compactText(summary.quality || reviewQualityLine(item), 220),
    next_action: compactText(summary.next_action || '', 180),
    tools,
  }
}
export function reviewQualityLine(item: ReviewItem): string {
  const validation = isRecord(item.validation) ? item.validation : {}
  const status = stringifyValue(validation.status || item.skill_status || '').trim()
  const score = toTraceNumber(validation.score)
  const issues = Array.isArray(validation.issues)
    ? validation.issues.map((issue) => stringifyValue(issue).trim()).filter(Boolean)
    : []
  const parts = [
    item.kind === 'skill' ? (status ? `validation ${status}` : 'skill note candidate') : 'memory candidate',
    score > 0 ? `score ${score}` : '',
    issues.length > 0 ? issues.slice(0, 3).join(', ') : '',
  ].filter(Boolean)
  return parts.join(' - ')
}
export function reviewSummaryCards(item: ReviewItem): Array<{ label: string; value: string; tone: string }> {
  const summary = normalizedReviewSummary(item)
  return [
    { label: '为什么保存', value: summary.why || '这个候选来自任务结束后的记忆/技能笔记判断。', tone: 'why' },
    { label: '保存成什么', value: summary.save_as || reviewTargetLabel(item), tone: 'save' },
    { label: '以后怎么用', value: summary.reuse || '后续相关任务组装上下文时会显示召回来源和命中理由。', tone: 'reuse' },
    { label: '风险/质量', value: summary.risk || summary.quality || '未标记额外风险。', tone: 'risk' },
  ]
}
export function reviewItemTitle(item: ReviewItem): string {
  return item.title || item.raw_id || item.id
}
export function reviewItemSummary(item: ReviewItem): string {
  return compactText(item.description || item.reason || item.content || item.body || '', 140)
}
export function reviewTargetLabel(item: ReviewItem): string {
  const target = item.target || item.category || ''
  if (item.kind === 'memory') {
    return MEMORY_TARGET_OPTIONS.find((option) => option.value === target)?.label || target || '记忆'
  }
  return target ? `技能笔记 · ${target}` : '技能笔记'
}
export function reviewStatusLabel(status: ReviewStatus | string): string {
  if (status === 'approved') {
    return '已批准'
  }
  if (status === 'discarded') {
    return '已丢弃'
  }
  return '待审核'
}
