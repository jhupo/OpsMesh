export const supportedLanguages = [
  { label: '简体中文', value: 'zh-CN' },
  { label: 'English', value: 'en-US' },
] as const

export type SupportedLanguage = (typeof supportedLanguages)[number]['value']

export function normalizeLanguage(language: string): SupportedLanguage {
  return language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US'
}
