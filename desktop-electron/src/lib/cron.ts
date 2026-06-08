import type { CronSchedule, CronJob, CronJobCreateSpec, CronDaemonSnapshot } from '../types'
import type { CronFormState } from '../app-types'
import { parseIntOrFallback } from './format'

const CRON_CALENDAR_PERIODS = new Set(['daily', 'weekly', 'monthly', 'yearly'])
const CRON_INTERVAL_PERIOD_UNITS: Record<string, CronFormState['intervalUnit']> = {
  everyminute: 'minutes',
  everyminutes: 'minutes',
  everyhour: 'hours',
  everyhours: 'hours',
  everyday: 'days',
  everydaily: 'days',
  everyweek: 'weeks',
  everyweekly: 'weeks',
  everymonth: 'months',
  everymonthly: 'months',
  everyyear: 'years',
  everyyearly: 'years',
}

export function toLocalDateTimeValue(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}
export function createCronForm(): CronFormState {
  const now = new Date()
  const later = new Date(now.getTime() + 15 * 60_000)
  return {
    id: '',
    name: '',
    scheduleType: 'once',
    period: 'daily',
    runAt: toLocalDateTimeValue(later),
    time: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
    startAt: toLocalDateTimeValue(now),
    weekday: '1',
    day: '1',
    month: '1',
    intervalCount: '1',
    intervalUnit: 'minutes',
    prompt: '',
    script: '',
    noAgent: false,
    contextFrom: '',
    workdir: '',
    repeatTimes: '',
    maxDelayMinutes: '',
  }
}
export function normalizeCronDateTimeInput(value: unknown, fallback: string): string {
  const text = String(value ?? '').trim()
  return text ? text.slice(0, 16) : fallback
}
export function normalizeCronIntervalUnit(value: unknown): CronFormState['intervalUnit'] {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'hours' || text === 'days' || text === 'weeks' || text === 'months' || text === 'years') {
    return text
  }
  return 'minutes'
}
export function positiveCronNumber(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}
export function cronIntervalFormValues(schedule: CronSchedule): Pick<CronFormState, 'intervalCount' | 'intervalUnit'> {
  const periodKey = String(schedule.period || '').trim().toLowerCase().replace(/[_-]+/g, '')
  let unit = CRON_INTERVAL_PERIOD_UNITS[periodKey] || normalizeCronIntervalUnit(schedule.interval_unit)
  let count = positiveCronNumber(schedule.interval_count ?? schedule.every ?? schedule.n, 1)

  if (schedule.every_hours !== null && schedule.every_hours !== undefined) {
    unit = 'hours'
    count = positiveCronNumber(schedule.every_hours, 1)
  } else if (schedule.every_days !== null && schedule.every_days !== undefined) {
    unit = 'days'
    count = positiveCronNumber(schedule.every_days, 1)
  } else if (schedule.every_minutes !== null && schedule.every_minutes !== undefined) {
    const minutes = positiveCronNumber(schedule.every_minutes, 1)
    if (minutes % 1440 === 0) {
      unit = 'days'
      count = minutes / 1440
    } else if (minutes % 60 === 0) {
      unit = 'hours'
      count = minutes / 60
    } else {
      unit = 'minutes'
      count = minutes
    }
  }

  return {
    intervalCount: String(Math.max(1, Math.round(count))),
    intervalUnit: unit,
  }
}
export function createCronFormFromJob(job: CronJob): CronFormState {
  const base = createCronForm()
  const schedule = job.schedule || {}
  const contextFrom = Array.isArray(job.context_from) ? job.context_from.join(', ') : ''
  const form: CronFormState = {
    ...base,
    id: job.id || '',
    name: job.name || '',
    prompt: job.prompt || '',
    script: job.script || '',
    noAgent: Boolean(job.no_agent),
    contextFrom,
    workdir: job.workdir || '',
    repeatTimes: job.repeat?.times === null || job.repeat?.times === undefined ? '' : String(job.repeat.times),
    maxDelayMinutes: job.max_delay_minutes === null || job.max_delay_minutes === undefined ? '' : String(job.max_delay_minutes),
  }

  if (schedule.type === 'once') {
    return {
      ...form,
      scheduleType: 'once',
      runAt: normalizeCronDateTimeInput(schedule.run_at, base.runAt),
    }
  }

  if (schedule.type === 'periodic') {
    const period = String(schedule.period || 'daily').trim().toLowerCase()
    const isCalendarPeriod = CRON_CALENDAR_PERIODS.has(period)
    const intervalValues = isCalendarPeriod ? null : cronIntervalFormValues(schedule)
    return {
      ...form,
      scheduleType: 'periodic',
      period: isCalendarPeriod ? period : 'interval',
      time: String(schedule.time || base.time),
      startAt: normalizeCronDateTimeInput(schedule.start_at, base.startAt),
      weekday: String(schedule.weekday ?? base.weekday),
      day: String(schedule.day ?? base.day),
      month: String(schedule.month ?? base.month),
      intervalCount: intervalValues?.intervalCount || base.intervalCount,
      intervalUnit: intervalValues?.intervalUnit || base.intervalUnit,
    }
  }

  return form
}
export function parseCronContext(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}
export function cronScheduleLabel(schedule?: CronSchedule): string {
  if (!schedule) {
    return '未设置'
  }
  if (schedule.type === 'once') {
    return `一次 · ${schedule.run_at || '-'}`
  }
  const period = String(schedule.period || 'periodic')
  if (period === 'daily') {
    return `每天 · ${schedule.time || '-'}`
  }
  if (period === 'weekly') {
    return `每周 · ${schedule.weekday || '-'} · ${schedule.time || '-'}`
  }
  if (period === 'monthly') {
    return `每月 · ${schedule.day || '-'} · ${schedule.time || '-'}`
  }
  if (period === 'yearly') {
    return `每年 · ${schedule.month || '-'}/${schedule.day || '-'} · ${schedule.time || '-'}`
  }
  if (period === 'interval') {
    const count = schedule.interval_count ?? schedule.every_minutes ?? schedule.every_hours ?? schedule.every_days ?? schedule.every ?? schedule.n ?? '-'
    const unit = cronIntervalUnitLabel(schedule.interval_unit || 'minutes')
    return `每隔 · ${count} ${unit}`
  }
  if (period.startsWith('every')) {
    const unit = cronIntervalUnitLabel({
      everyminute: 'minutes',
      everyhour: 'hours',
      everyday: 'days',
      everyweek: 'weeks',
      everymonth: 'months',
      everyyear: 'years',
    }[period] || schedule.interval_unit || 'minutes')
    const count = schedule.interval_count ?? schedule.every_minutes ?? schedule.every_hours ?? schedule.every_days ?? schedule.every ?? schedule.n ?? 1
    return `每隔 · ${count} ${unit}`
  }
  return String(schedule.period || 'periodic')
}
export function cronDaemonLabel(daemon?: CronDaemonSnapshot): string {
  if (!daemon?.running) {
    return '守护进程已停止'
  }
  return `运行中 · ${daemon.interval_seconds ?? 60}s`
}
export function cronIntervalUnitLabel(unit?: string | null): string {
  if (unit === 'minutes') {
    return '分钟'
  }
  if (unit === 'hours') {
    return '小时'
  }
  if (unit === 'days') {
    return '天'
  }
  if (unit === 'weeks') {
    return '周'
  }
  if (unit === 'months') {
    return '月'
  }
  if (unit === 'years') {
    return '年'
  }
  return unit || ''
}
export function cronLastStatusLabel(status?: string | null): string {
  if (!status) {
    return '-'
  }
  if (status === 'ok') {
    return '已完成'
  }
  if (status === 'error') {
    return '失败'
  }
  if (status === 'missed') {
    return '已错过'
  }
  if (status === 'running') {
    return '运行中'
  }
  return status
}
export function cronJobStatus(job: CronJob): string {
  const state = job.state || {}
  if (state.running) {
    return '运行中'
  }
  if (job.enabled === false) {
    return '已停用'
  }
  if (state.last_status === 'error') {
    return '失败'
  }
  if (state.last_status === 'missed') {
    return '已错过'
  }
  if (state.last_status === 'ok') {
    return '已完成'
  }
  return '待命'
}
export function cronJobNotice(message: string, job?: CronJob | null): string {
  const path = String(job?.path || '').trim()
  return path ? `${message}，配置已写入 ${path}` : message
}
export function buildCronSchedule(form: CronFormState): CronSchedule {
  if (form.scheduleType === 'once') {
    return {
      type: 'once',
      run_at: form.runAt.trim(),
    }
  }

  if (form.period === 'daily' || form.period === 'weekly' || form.period === 'monthly' || form.period === 'yearly') {
    const schedule: CronSchedule = {
      type: 'periodic',
      period: form.period,
      time: form.time.trim(),
      start_at: form.startAt.trim(),
    }
    if (form.period === 'weekly') {
      schedule.weekday = form.weekday.trim() || '1'
    } else if (form.period === 'monthly') {
      schedule.day = parseIntOrFallback(form.day, 1)
    } else if (form.period === 'yearly') {
      schedule.month = parseIntOrFallback(form.month, 1)
      schedule.day = parseIntOrFallback(form.day, 1)
    }
    return schedule
  }

  const count = Math.max(1, parseIntOrFallback(form.intervalCount, 1))
  const intervalPeriod = {
    minutes: 'everyminute',
    hours: 'everyhour',
    days: 'everyday',
    weeks: 'everyweek',
    months: 'everymonth',
    years: 'everyyear',
  }[form.intervalUnit]
  const schedule: CronSchedule = {
    type: 'periodic',
    period: intervalPeriod,
    start_at: form.startAt.trim(),
  }
  if (form.intervalUnit === 'minutes') {
    schedule.every_minutes = count
  } else if (form.intervalUnit === 'hours') {
    schedule.every_hours = count
  } else if (form.intervalUnit === 'days') {
    schedule.every_days = count
  } else {
    schedule.every = count
    schedule.n = count
  }
  return schedule
}
export function buildCronSpec(form: CronFormState): CronJobCreateSpec {
  return {
    id: form.id.trim() || undefined,
    name: form.name.trim() || undefined,
    schedule: buildCronSchedule(form),
    prompt: form.noAgent ? '' : form.prompt,
    script: form.script || undefined,
    no_agent: form.noAgent,
    context_from: parseCronContext(form.contextFrom),
    workdir: form.workdir.trim() || undefined,
    repeat_times: form.repeatTimes.trim().length > 0 ? parseIntOrFallback(form.repeatTimes, 0) : null,
    max_delay_minutes: form.maxDelayMinutes.trim().length > 0 ? parseIntOrFallback(form.maxDelayMinutes, 0) : null,
  }
}
