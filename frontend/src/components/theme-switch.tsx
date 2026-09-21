import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '@/context/theme-provider'
import { AnimatedThemeToggle } from '@/components/shadcn-space/toggle/toggle-01'

export function ThemeSwitch() {
  const { t } = useTranslation()
  const { resolvedTheme, setTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  useEffect(() => {
    const metaThemeColor = document.querySelector("meta[name='theme-color']")
    metaThemeColor?.setAttribute('content', isDark ? '#272832' : '#fff')
  }, [isDark])

  return (
    <AnimatedThemeToggle
      isDark={isDark}
      onToggle={() => setTheme(isDark ? 'light' : 'dark')}
      className='rounded-full'
      aria-label={t('common.theme.label')}
    />
  )
}
