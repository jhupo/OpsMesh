import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'
import { type PageResponse } from './workspaces'

export type NotificationItem = {
  id: string
  title: string
  body: string
  read_at: string | null
  created_at: string
}

type NotificationCounts = {
  unread_count: number
}

export function notificationsQueryOptions(workspaceId: string | undefined) {
  return queryOptions({
    queryKey: ['notifications', workspaceId, 'unread'],
    queryFn: () =>
      apiRequest<PageResponse<NotificationItem>>(
        `/workspaces/${workspaceId}/notifications?limit=50&offset=0&include_archived=false`
      ),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
  })
}

export function notificationCountsQueryOptions(
  workspaceId: string | undefined
) {
  return queryOptions({
    queryKey: ['notifications', workspaceId, 'counts'],
    queryFn: () =>
      apiRequest<NotificationCounts>(
        `/workspaces/${workspaceId}/notifications/counts`
      ),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
  })
}

export function markNotificationRead(
  workspaceId: string,
  notificationId: string
) {
  return apiRequest<NotificationItem>(
    `/workspaces/${workspaceId}/notifications/${notificationId}/read`,
    { method: 'POST' }
  )
}
