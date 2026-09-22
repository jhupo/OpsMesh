import { lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { currentUserQueryOptions } from '@/api/auth'

const RouterDevtools = lazy(() =>
  import('@tanstack/react-router-devtools').then((module) => ({
    default: module.TanStackRouterDevtools,
  }))
)
const QueryDevtools = lazy(() =>
  import('@tanstack/react-query-devtools').then((module) => ({
    default: module.ReactQueryDevtools,
  }))
)

type DebugPreferences = {
  enabledByUser: Record<string, boolean>
  setEnabled: (userId: string, enabled: boolean) => void
}

// A per-user display preference, never an authorization source.
// eslint-disable-next-line react-refresh/only-export-components
export const useDebugPreferences = create<DebugPreferences>()(
  persist(
    (set) => ({
      enabledByUser: {},
      setEnabled: (userId, enabled) =>
        set((state) => ({
          enabledByUser: { ...state.enabledByUser, [userId]: enabled },
        })),
    }),
    { name: 'opsmesh.debug-display' }
  )
)

export function DeveloperTools() {
  const { data: user, isError } = useQuery(currentUserQueryOptions())
  const enabled = useDebugPreferences((state) =>
    user ? state.enabledByUser[user.user_id] : false
  )
  if (
    !import.meta.env.DEV ||
    isError ||
    user?.platform_admin !== true ||
    !enabled
  )
    return null
  return (
    <Suspense fallback={null}>
      <QueryDevtools buttonPosition='bottom-left' />
      <RouterDevtools position='bottom-right' />
    </Suspense>
  )
}
