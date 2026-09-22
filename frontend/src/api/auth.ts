import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type CurrentUser = {
  user_id: string
  email: string
  display_name: string
}

type LoginResponse = {
  id: string
  token: string
}

export type LoginCredentials = {
  identifier: string
  password: string
}

export async function login(credentials: LoginCredentials) {
  const identifier = credentials.identifier.trim()
  const identity = identifier.includes('@')
    ? { email: identifier.toLowerCase() }
    : { username: identifier }

  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ ...identity, password: credentials.password }),
    authenticated: false,
  })
}

export function currentUserQueryOptions() {
  return queryOptions({
    queryKey: ['auth', 'current-user'],
    queryFn: () => apiRequest<CurrentUser>('/auth/me'),
    staleTime: 60_000,
  })
}

export async function revokeToken(tokenId: string): Promise<void> {
  await apiRequest(`/auth/tokens/${tokenId}`, { method: 'DELETE' })
}
