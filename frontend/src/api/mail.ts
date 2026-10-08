import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type MailConfiguration = {
  enabled: boolean
  host: string
  port: number
  security: 'starttls' | 'tls'
  username: string
  from_email: string
  from_name: string
  public_base_url: string
  invitation_expiry_hours: number
  password_configured: boolean
}

export type UserInvitation = {
  id: string
  user_id: string
  delivery_status: 'pending' | 'sent' | 'failed'
  expires_at: string
  sent_at: string | null
}

export const mailConfigurationQueryOptions = () =>
  queryOptions({
    queryKey: ['platform-admin', 'mail-configuration'],
    queryFn: () => apiRequest<MailConfiguration>('/admin/system/mail'),
    refetchOnWindowFocus: false,
  })

export function saveMailConfiguration(
  payload: Omit<MailConfiguration, 'password_configured'> & {
    password?: string
    clear_password?: boolean
  }
) {
  return apiRequest<MailConfiguration>('/admin/system/mail', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function sendTestEmail(email: string) {
  return apiRequest<{ sent: boolean }>('/admin/system/mail/test', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function inviteUser(payload: {
  email: string
  display_name: string
  platform_admin: boolean
}) {
  return apiRequest<UserInvitation>('/admin/user-invitations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function resendUserInvitation(userId: string) {
  return apiRequest<UserInvitation>(
    `/admin/users/${userId}/invitation/resend`,
    { method: 'POST' }
  )
}

export function acceptUserInvitation(payload: {
  token: string
  password: string
  display_name: string
  username: string
}) {
  return apiRequest<{ email: string }>('/auth/invitations/accept', {
    method: 'POST',
    authenticated: false,
    body: JSON.stringify(payload),
  })
}
