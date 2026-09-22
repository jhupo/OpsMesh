import { useSuspenseQuery } from '@tanstack/react-query'
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router'
import { Bell, Monitor, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions } from '@/api/auth'
import { Main } from '@/components/layout/main'
import TabsVertical from '@/components/shadcn-space/tabs/tabs-06'

export function Settings() {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const items = [
    { id: '/settings', label: t('settings.profile'), icon: UserRound },
    {
      id: '/settings/notifications',
      label: t('settings.notifications'),
      icon: Bell,
    },
    ...(import.meta.env.DEV && user.platform_admin === true
      ? [
          {
            id: '/settings/display',
            label: t('settings.display'),
            icon: Monitor,
          },
        ]
      : []),
  ]
  return (
    <Main>
      <div className='mx-auto flex w-full max-w-5xl flex-col gap-8 py-2 md:py-6'>
        <h1 className='text-2xl font-semibold tracking-tight'>
          {t('settings.title')}
        </h1>
        <TabsVertical
          items={items}
          value={pathname.replace(/\/$/, '')}
          onValueChange={(to) => void navigate({ to })}
        >
          <Outlet />
        </TabsVertical>
      </div>
    </Main>
  )
}
