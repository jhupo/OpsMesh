import { createFileRoute, redirect } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'
import { SettingsDisplay } from '@/features/settings/display'

export const Route = createFileRoute('/_authenticated/settings/display')({
  beforeLoad: async ({ context }) => {
    const user = await context.queryClient.fetchQuery(currentUserQueryOptions())
    if (!import.meta.env.DEV || user.platform_admin !== true) {
      throw redirect({ to: '/settings', replace: true })
    }
  },
  component: SettingsDisplay,
})
