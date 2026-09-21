import { createFileRoute, redirect } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'
import { ApiError } from '@/api/errors'
import { clearAuthSession, getAuthSession } from '@/lib/auth-session'
import { AppLayout } from '@/components/layout/app-layout'

export const Route = createFileRoute('/_app')({
  beforeLoad: async ({ context, location }) => {
    if (!getAuthSession()) {
      throw redirect({
        to: '/login',
        search: { redirect: location.href },
      })
    }

    try {
      await context.queryClient.ensureQueryData(currentUserQueryOptions())
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearAuthSession()
        throw redirect({
          to: '/login',
          search: { redirect: location.href },
        })
      }
      throw error
    }
  },
  component: AppLayout,
})
