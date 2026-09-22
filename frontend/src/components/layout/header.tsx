import { useEffect, useState } from 'react'
import { useSuspenseQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions } from '@/api/auth'
import { cn } from '@/lib/utils'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { LanguageSwitch } from '@/components/language-switch'
import { ThemeSwitch } from '@/components/theme-switch'
import { MessageCenter } from '@/features/messages'
import { NotificationCenter } from '@/features/notifications'
import { NavUser } from './nav-user'

type HeaderProps = React.HTMLAttributes<HTMLElement> & {
  fixed?: boolean
  ref?: React.Ref<HTMLElement>
}

export function Header({ className, fixed, children, ...props }: HeaderProps) {
  const [offset, setOffset] = useState(0)
  const { t } = useTranslation()
  const { data: currentUser } = useSuspenseQuery(currentUserQueryOptions())

  useEffect(() => {
    const onScroll = () => {
      setOffset(document.body.scrollTop || document.documentElement.scrollTop)
    }

    // Add scroll listener to the body
    document.addEventListener('scroll', onScroll, { passive: true })

    // Clean up the event listener on unmount
    return () => document.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header
      className={cn(
        'z-50 h-16 border-b border-border',
        fixed && 'header-fixed peer/header sticky top-0 w-[inherit]',
        offset > 10 && fixed ? 'shadow' : 'shadow-none',
        className
      )}
      {...props}
    >
      <div
        className={cn(
          'relative flex h-full items-center gap-3 p-4 sm:gap-4',
          offset > 10 &&
            fixed &&
            'after:absolute after:inset-0 after:-z-10 after:bg-background/20 after:backdrop-blur-lg'
        )}
      >
        <SidebarTrigger aria-label={t('common.toggleSidebar')} />
        <Separator orientation='vertical' className='h-6' />
        {children}
        <div className='app-header-actions ms-auto flex shrink-0 items-center gap-1 sm:gap-2'>
          <ThemeSwitch />
          <LanguageSwitch />
          <NotificationCenter />
          <MessageCenter />
          <NavUser user={currentUser} />
        </div>
      </div>
    </header>
  )
}
