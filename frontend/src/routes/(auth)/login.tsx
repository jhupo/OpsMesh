import { createFileRoute, redirect } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'
import { ApiError } from '@/api/errors'
import { clearAuthSession, getAuthSession } from '@/lib/auth-session'
import { SignIn } from '@/features/auth/sign-in'

type LoginSearch = {
  redirect?: string
}

export const Route = createFileRoute('/(auth)/login')({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
  }),
  beforeLoad: async ({ context }) => {
    if (!getAuthSession()) return

    let authenticated = false
    try {
      await context.queryClient.ensureQueryData(currentUserQueryOptions())
      authenticated = true
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) throw error
      clearAuthSession()
    }
    if (authenticated) throw redirect({ to: '/' })
  },
  component: SignIn,
})
