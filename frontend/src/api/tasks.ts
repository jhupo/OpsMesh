import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'
import type { PageResponse } from './workspaces'

export type WorkspaceTask = {
  id: string
  workspace_id: string
  title: string
  description: string
  status: string
  priority: number
  created_at: string
  updated_at: string
}

export function tasksQueryOptions(
  workspaceId: string | undefined,
  page = 0,
  pageSize = 20
) {
  return queryOptions({
    queryKey: ['workspaces', workspaceId, 'tasks', page, pageSize],
    queryFn: ({ signal }) =>
      apiRequest<PageResponse<WorkspaceTask>>(
        `/workspaces/${workspaceId}/tasks?limit=${pageSize}&offset=${page * pageSize}`,
        { signal }
      ),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
  })
}
