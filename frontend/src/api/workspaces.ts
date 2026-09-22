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

export type WorkspaceCreate = {
  name: string
  slug: string
}

export function workspacesQueryOptions() {
  return queryOptions({
    queryKey: ['workspaces', 'accessible'],
    queryFn: () =>
      apiRequest<PageResponse<Workspace>>('/workspaces?limit=50&offset=0'),
    staleTime: 60_000,
  })
}

export async function createWorkspace(
  input: WorkspaceCreate,
  idempotencyKey: string
) {
  return apiRequest<Workspace>('/workspaces', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ ...input, settings: {} }),
  })
}
