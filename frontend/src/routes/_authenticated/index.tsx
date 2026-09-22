import { createFileRoute, redirect } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'
import { Dashboard } from '@/features/dashboard'

export const Route = createFileRoute('/_authenticated/')({
  beforeLoad: async ({ context }) => {
    const user = await context.queryClient.ensureQueryData(
      currentUserQueryOptions()
    )
    if (user.platform_admin) {
      throw redirect({
        to: '/admin/$section',
        params: { section: 'overview' },
      })
    }
  },
  component: Dashboard,
})
