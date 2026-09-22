import { Outlet, createFileRoute, redirect } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'

export const Route = createFileRoute('/_authenticated/admin')({
  beforeLoad: async ({ context }) => {
    const user = await context.queryClient.ensureQueryData(
      currentUserQueryOptions()
    )
    if (!user.platform_admin) {
      throw redirect({
        to: '/errors/$error',
        params: { error: 'forbidden' },
      })
    }
  },
  component: Outlet,
})
