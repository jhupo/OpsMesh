const AUTH_SESSION_KEY = 'opsmesh.auth-session'

export type AuthSession = {
  token: string
  tokenId: string
}

export function getAuthSession(): AuthSession | null {
  if (typeof window === 'undefined') return null

  const serialized = window.sessionStorage.getItem(AUTH_SESSION_KEY)
  if (!serialized) return null

  try {
    const value = JSON.parse(serialized) as Partial<AuthSession>
    if (typeof value.token === 'string' && typeof value.tokenId === 'string') {
      return { token: value.token, tokenId: value.tokenId }
    }
  } catch {
    // Invalid session data is cleared below and never reused as credentials.
  }

  clearAuthSession()
  return null
}

export function setAuthSession(session: AuthSession): void {
  window.sessionStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(session))
}

export function clearAuthSession(): void {
  if (typeof window !== 'undefined') {
    window.sessionStorage.removeItem(AUTH_SESSION_KEY)
  }
}

export function safeRedirectPath(value: string | undefined): string {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\')
  )
    return '/'
  const target = new URL(value, 'https://opsmesh.invalid')
  if (
    target.origin !== 'https://opsmesh.invalid' ||
    /^\/sign-in(?:-2)?\/?$/.test(target.pathname)
  )
    return '/'
  return target.pathname + target.search + target.hash
}
