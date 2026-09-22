import { createFileRoute } from '@tanstack/react-router'
import { Main } from '@/components/layout/main'
import { SettingsPage, type SettingsTab } from '@/features/settings'

type SettingsSearch = {
  tab: SettingsTab
}

const SETTINGS_TABS = new Set<SettingsTab>([
  'profile',
  'notifications',
  'settings',
])

export const Route = createFileRoute('/_app/settings')({
  validateSearch: (search: Record<string, unknown>): SettingsSearch => ({
    tab:
      typeof search.tab === 'string' &&
      SETTINGS_TABS.has(search.tab as SettingsTab)
        ? (search.tab as SettingsTab)
        : 'profile',
  }),
  component: function SettingsRoute() {
    const { tab } = Route.useSearch()
    const navigate = Route.useNavigate()

    return (
      <Main>
        <SettingsPage
          activeTab={tab}
          onTabChange={(nextTab) => {
            void navigate({ search: { tab: nextTab }, replace: true })
          }}
        />
      </Main>
    )
  },
})
