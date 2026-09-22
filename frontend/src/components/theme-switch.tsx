import { useEffect } from 'react'
import { Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '@/context/theme-provider'
import { Button } from '@/components/ui/button'

export function ThemeSwitch() {
  const { resolvedTheme, setTheme } = useTheme()
  const { t } = useTranslation()
  useEffect(() => {
    document
      .querySelector("meta[name='theme-color']")
      ?.setAttribute(
        'content',
        resolvedTheme === 'dark' ? '#020817' : '#ffffff'
      )
  }, [resolvedTheme])
  return (
    <Button
      variant='ghost'
      size='icon'
      className='relative rounded-full'
      aria-label={t('theme.toggle_theme')}
      onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
    >
      <Sun className='scale-100 rotate-0 transition-transform motion-reduce:transition-none dark:scale-0 dark:-rotate-90' />
      <Moon className='absolute scale-0 rotate-90 transition-transform motion-reduce:transition-none dark:scale-100 dark:rotate-0' />
    </Button>
  )
}
