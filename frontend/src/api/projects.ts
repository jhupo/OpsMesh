import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'
import { type PageResponse } from './workspaces'

export type Project = {
  id: string
  workspace_id: string
  name: string
  slug: string
  description: string
  status: string
}

export function projectsQueryOptions(workspaceId: string | undefined) {
  return queryOptions({
    queryKey: ['projects', workspaceId, 'active'],
    queryFn: () =>
      apiRequest<PageResponse<Project>>(
        `/workspaces/${workspaceId}/projects?limit=100&offset=0&include_archived=false`
      ),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
  })
}
