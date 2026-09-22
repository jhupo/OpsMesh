import { clearAuthSession, getAuthSession } from '@/lib/auth-session'
import { ApiError } from './errors'

type ApiErrorEnvelope = {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(
  /\/$/,
  ''
)

type ApiRequestInit = RequestInit & {
  authenticated?: boolean
  clearSessionOnUnauthorized?: boolean
  responseType?: 'json' | 'blob'
}

export async function apiRequest<T>(
  path: `/${string}`,
  init: ApiRequestInit = {}
): Promise<T> {
  const {
    authenticated = true,
    clearSessionOnUnauthorized = true,
    responseType = 'json',
    ...requestInit
  } = init
  const headers = new Headers(requestInit.headers)
  if (requestInit.body && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  if (authenticated) {
    const session = getAuthSession()
    if (session) headers.set('authorization', `Bearer ${session.token}`)
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...requestInit,
    credentials: 'include',
    headers,
  })

  if (!response.ok) {
    const envelope = await parseErrorEnvelope(response)
    if (
      authenticated &&
      clearSessionOnUnauthorized &&
      response.status === 401
    ) {
      clearAuthSession()
    }
    throw new ApiError(envelope.error?.message || response.statusText, {
      status: response.status,
      code: envelope.error?.code,
      requestId:
        envelope.error?.request_id ||
        response.headers.get('x-request-id') ||
        undefined,
    })
  }

  if (response.status === 204) return undefined as T
  if (responseType === 'blob') return (await response.blob()) as T
  return (await response.json()) as T
}

async function parseErrorEnvelope(
  response: Response
): Promise<ApiErrorEnvelope> {
  try {
    return (await response.json()) as ApiErrorEnvelope
  } catch {
    return {}
  }
}
