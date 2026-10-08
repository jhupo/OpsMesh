import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type WorkspaceAction =
  | 'read'
  | 'write'
  | 'approve'
  | 'operate'
  | 'manage_runtime'
  | 'manage_capability'
  | 'manage_members'
  | 'admin'
  | 'owner'

export type WorkspaceAccess = {
  workspace_id: string
  user_id: string
  role: 'owner' | 'admin' | 'operator' | 'viewer'
  workspace_status: string
  membership_status: string
  allowed_actions: WorkspaceAction[]
}

export const workspaceAccessQueryOptions = (id?: string) =>
  queryOptions({
    queryKey: ['workspace-access', id],
    queryFn: ({ signal }) =>
      apiRequest<WorkspaceAccess>(`/workspaces/${id}/access/context`, {
        signal,
      }),
    enabled: Boolean(id),
    staleTime: 0,
    refetchInterval: 30_000,
    retry: false,
  })
