import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type NotificationItem = {
  id: string
  workspace_id: string
  notification_type: string
  severity: string
  source_type: string
  source_id: string | null
  title: string
  body: string
  metadata: Record<string, unknown>
  read_at: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export type NotificationCounts = {
  workspace_id: string
  generated_at: string
  total_count: number
  unread_count: number
  read_count: number
  archived_count: number
  severity_counts: Record<string, number>
  type_counts: Record<string, number>
  source_type_counts: Record<string, number>
}

type PageResponse<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
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

export async function markNotificationRead(
  workspaceId: string,
  notificationId: string
) {
  return apiRequest<NotificationItem>(
    `/workspaces/${workspaceId}/notifications/${notificationId}/read`,
    { method: 'POST' }
  )
}
