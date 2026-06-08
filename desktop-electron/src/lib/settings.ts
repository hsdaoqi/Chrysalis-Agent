import type { PermissionLevel, SettingsSnapshot } from '../types'

export const PERMISSION_LEVEL_OPTIONS: Array<{ value: PermissionLevel; label: string }> = [
  { value: 'balanced', label: '默认权限' },
  { value: 'locked', label: '自动审查' },
  { value: 'full', label: '完全访问权限' },
]
import type { SettingsFormState } from '../app-types'
import { parseFloatOrFallback, parseIntOrFallback } from './format'

export function normalizePermissionLevel(value: unknown): PermissionLevel {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'locked' || normalized === 'balanced' || normalized === 'full') {
    return normalized
  }
  if (normalized === 'strict' || normalized === 'safe' || normalized === 'ask') {
    return 'locked'
  }
  if (normalized === 'trusted' || normalized === 'off' || normalized === 'none') {
    return 'full'
  }
  return 'balanced'
}
export function permissionLevelLabel(level: PermissionLevel): string {
  return PERMISSION_LEVEL_OPTIONS.find((option) => option.value === level)?.label || 'Balanced'
}
export function createSettingsForm(settings?: SettingsSnapshot): SettingsFormState {
  const llm = settings?.llm || {}
  return {
    enabled: settings?.enabled ?? false,
    name: llm.name || '',
    provider: llm.provider || 'openai',
    apiKey: llm.api_key || '',
    baseUrl: llm.base_url || '',
    model: llm.model || '',
    wireApi: llm.wire_api || 'chat',
    contextWindow: String(llm.context_window ?? 28000),
    temperature: String(llm.temperature ?? 0.2),
    maxTokens: llm.max_tokens === null || llm.max_tokens === undefined ? '' : String(llm.max_tokens),
    maxRetries: String(llm.max_retries ?? 4),
    timeout: String(llm.timeout ?? 60),
    proxy: llm.proxy || '',
    thinking: llm.thinking || 'disabled',
    thinkingBudget: llm.thinking_budget === null || llm.thinking_budget === undefined ? '' : String(llm.thinking_budget),
    systemPrompt: settings?.system_prompt || '',
    permissionLevel: normalizePermissionLevel(settings?.permission_level),
  }
}
export function settingsPayload(form: SettingsFormState): Record<string, unknown> {
  return {
    enabled: form.enabled,
    permission_level: form.permissionLevel,
    llm: {
      name: form.name,
      provider: form.provider,
      api_key: form.apiKey,
      base_url: form.baseUrl,
      model: form.model,
      wire_api: form.wireApi,
      context_window: parseIntOrFallback(form.contextWindow, 28000),
      temperature: parseFloatOrFallback(form.temperature, 0.2),
      max_tokens: form.maxTokens.trim().length > 0 ? parseIntOrFallback(form.maxTokens, 0) : null,
      max_retries: parseIntOrFallback(form.maxRetries, 4),
      timeout: parseIntOrFallback(form.timeout, 60),
      proxy: form.proxy,
      thinking: form.thinking,
      thinking_budget: form.thinkingBudget.trim().length > 0 ? parseIntOrFallback(form.thinkingBudget, 0) : null,
    },
    system_prompt: form.systemPrompt,
  }
}
