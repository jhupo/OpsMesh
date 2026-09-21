import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import { normalizeLanguage } from './languages'
import enUS from './locales/en-US.json'
import zhCN from './locales/zh-CN.json'

const resources = {
  'en-US': { translation: enUS },
  'zh-CN': { translation: zhCN },
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en-US',
    supportedLngs: Object.keys(resources),
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'opsmesh.locale',
      caches: ['localStorage'],
      convertDetectedLanguage: normalizeLanguage,
    },
  })

function updateDocumentLanguage(language: string) {
  document.documentElement.lang = normalizeLanguage(language)
  document.documentElement.dir = 'ltr'
}

updateDocumentLanguage(i18n.resolvedLanguage || i18n.language)
i18n.on('languageChanged', updateDocumentLanguage)

export { i18n }
