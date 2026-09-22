import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type CurrentUser = {
  user_id: string
  email: string
  display_name: string
  platform_admin: boolean
  avatar_version: string | null
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

export async function logout(): Promise<void> {
  await apiRequest('/auth/logout', { method: 'POST' })
}

export function currentUserQueryOptions() {
  return queryOptions({
    queryKey: ['auth', 'current-user'],
    queryFn: () => apiRequest<CurrentUser>('/auth/me'),
    staleTime: 60_000,
  })
}

export type ProfileUpdate = {
  display_name: string
  avatar_base64?: string | null
  password?: { current_password: string; new_password: string }
}

export function updateProfile(update: ProfileUpdate) {
  return apiRequest<CurrentUser>('/auth/me', {
    method: 'PATCH',
    clearSessionOnUnauthorized: !update.password,
    body: JSON.stringify(update),
  })
}

export function avatarQueryOptions(user: CurrentUser) {
  return queryOptions({
    queryKey: ['auth', 'avatar', user.user_id, user.avatar_version],
    queryFn: () =>
      apiRequest<Blob>('/auth/me/avatar', { responseType: 'blob' }),
    enabled: Boolean(user.avatar_version),
    staleTime: Infinity,
    retry: false,
  })
}
