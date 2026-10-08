import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type Workspace = {
  id: string
  name: string
  slug: string
  status: string
}

export type PageResponse<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export function workspacesQueryOptions() {
  return queryOptions({
    queryKey: ['workspaces', 'accessible'],
    queryFn: ({ signal }) =>
      apiRequest<PageResponse<Workspace>>('/workspaces?limit=50&offset=0', {
        signal,
      }),
    staleTime: 60_000,
  })
}
