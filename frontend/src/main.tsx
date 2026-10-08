import { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import i18n from '@/i18n'
import { toast } from 'sonner'
import { ApiError } from '@/api/errors'
import { clearAuthSession, safeRedirectPath } from '@/lib/auth-session'
import { handleServerError } from '@/lib/handle-server-error'
import { DirectionProvider } from './context/direction-provider'
import { FontProvider } from './context/font-provider'
import { ThemeProvider } from './context/theme-provider'
// Generated Routes
import { routeTree } from './routeTree.gen'
// Styles
import './styles/index.css'

let redirectingToSignIn = false
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // eslint-disable-next-line no-console
        if (import.meta.env.DEV) console.log({ failureCount, error })

        if (import.meta.env.DEV || failureCount >= 1) return false
        return !(error instanceof ApiError && error.status < 500)
      },
      refetchOnWindowFocus: import.meta.env.PROD,
      staleTime: 10 * 1000, // 10s
    },
    mutations: {
      onError: (error) => {
        handleServerError(error)

        if (error instanceof ApiError) {
          if (error.status === 304) {
            toast.error(i18n.t('errors.content_not_modified'))
          }
        }
      },
    },
  },
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        clearAuthSession()
        if (
          redirectingToSignIn ||
          router.history.location.pathname === '/sign-in'
        )
          return
        redirectingToSignIn = true
        void router
          .navigate({
            to: '/sign-in',
            search: {
              redirect: safeRedirectPath(router.history.location.href),
            },
            replace: true,
          })
          .then(
            () => queryClient.clear(),
            () => queryClient.clear()
          )
          .finally(() => {
            redirectingToSignIn = false
          })
        return
      }
    },
  }),
})

// Create a new router instance
const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
  scrollToTopSelectors: ['#content'],
})

// Register the router instance for type safety
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// Render the app
const rootElement = document.getElementById('root')!
if (!rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement)
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <FontProvider>
            <DirectionProvider>
              <RouterProvider router={router} />
            </DirectionProvider>
          </FontProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </StrictMode>
  )
}
