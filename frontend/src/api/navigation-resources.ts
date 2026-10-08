import { apiRequest } from './client'

export const readNavigationResource = (path: string, signal: AbortSignal) =>
  apiRequest<unknown>(path as `/${string}`, { signal })

export function decideNavigationApproval(
  workspaceId: string,
  id: string,
  decision: 'approve' | 'reject',
  reason: string,
  admin: boolean
) {
  return apiRequest(
    admin
      ? `/admin/workspaces/${workspaceId}/marketplace/reviews/${id}/decision`
      : `/workspaces/${workspaceId}/approvals/${id}/${decision}`,
    {
      method: 'POST',
      body: JSON.stringify(
        admin
          ? {
              decision: decision === 'approve' ? 'approved' : 'rejected',
              reason,
            }
          : { reason }
      ),
    }
  )
}

export function saveNotificationPreference(
  workspaceId: string,
  key: string,
  value: boolean
) {
  return apiRequest(`/workspaces/${workspaceId}/notifications/preferences`, {
    method: 'PUT',
    body: JSON.stringify({ [key]: value }),
  })
}
