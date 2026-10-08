import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'
import { type PageResponse } from './workspaces'

export type AdminUser = {
  id: string
  created_at: string
  updated_at: string
  username: string | null
  email: string
  display_name: string
  status: string
  platform_admin: boolean
}

export type AdminUserListItem = AdminUser & {
  invitation_delivery_status?: string | null
  workspace_count: number
  active_workspace_count: number
  resource_usage_rate: number
}

export type AdminWorkspaceMember = {
  id: string
  workspace_id: string
  user_id: string
  email: string
  display_name: string
  role: 'owner' | 'admin' | 'operator' | 'viewer'
  status: 'active' | 'disabled'
  created_at: string
  updated_at: string
}

export type AdminUserDetail = AdminUser & {
  workspace_memberships: AdminWorkspaceMember[]
}

export type AdminWorkspace = {
  id: string
  created_at: string
  updated_at: string
  owner_user_id: string
  name: string
  slug: string
  status: string
  settings: Record<string, unknown>
  member_count: number
  project_count: number
}

export type AdminProject = {
  id: string
  workspace_id: string
  created_by_user_id: string | null
  name: string
  slug: string
  description: string
  input_path: string
  work_path: string
  output_path: string
  configuration: Record<string, unknown>
  configuration_version: number
  status: string
  created_at: string
  updated_at: string
}

export type AdminProjectQuota = {
  workspace_id: string
  project_id: string
  quota_key: string
  limit_value: number
  reserved_value: number
  unit: string
  status: string
  available_value: number
  utilization: number
  saturated: boolean
  over_reserved: boolean
  created_at: string
  updated_at: string
}

export type AdminUserCreateRequest = {
  email: string
  display_name: string
  username?: string
  password?: string
  platform_admin: boolean
}

export type AdminUserCreateResponse = AdminUser & {
  initial_password: string
}

const pageQuery = (limit: number, offset: number, status?: string) => {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (status && status !== 'all') params.set('status', status)
  return params.toString()
}

export function adminUsersQueryOptions(
  status = 'all',
  limit = 100,
  offset = 0
) {
  return queryOptions({
    queryKey: [
      'platform-admin',
      'organization',
      'users',
      'list',
      status,
      limit,
      offset,
    ],
    queryFn: () =>
      apiRequest<PageResponse<AdminUserListItem>>(
        `/admin/users?${pageQuery(limit, offset, status)}`
      ),
    staleTime: 30_000,
  })
}

export function adminUserDetailQueryOptions(userId: string | undefined) {
  return queryOptions({
    queryKey: ['platform-admin', 'organization', 'users', 'detail', userId],
    queryFn: () => apiRequest<AdminUserDetail>(`/admin/users/${userId}`),
    enabled: Boolean(userId),
  })
}

export function adminWorkspacesQueryOptions(
  status = 'all',
  limit = 20,
  offset = 0
) {
  return queryOptions({
    queryKey: [
      'platform-admin',
      'organization',
      'workspaces',
      status,
      limit,
      offset,
    ],
    queryFn: () =>
      apiRequest<PageResponse<AdminWorkspace>>(
        `/admin/workspaces?${pageQuery(limit, offset, status)}`
      ),
    staleTime: 30_000,
  })
}

export function adminWorkspaceQueryOptions(workspaceId: string | undefined) {
  return queryOptions({
    queryKey: ['platform-admin', 'organization', 'workspace', workspaceId],
    queryFn: () =>
      apiRequest<AdminWorkspace>(`/admin/workspaces/${workspaceId}`),
    enabled: Boolean(workspaceId),
  })
}

export function adminWorkspaceMembersQueryOptions(
  workspaceId: string | undefined
) {
  return queryOptions({
    queryKey: [
      'platform-admin',
      'organization',
      'workspace-members',
      workspaceId,
    ],
    queryFn: () =>
      apiRequest<PageResponse<AdminWorkspaceMember>>(
        `/admin/workspaces/${workspaceId}/members?${pageQuery(100, 0)}`
      ),
    enabled: Boolean(workspaceId),
  })
}

export function adminWorkspaceProjectsQueryOptions(
  workspaceId: string | undefined
) {
  return queryOptions({
    queryKey: [
      'platform-admin',
      'organization',
      'workspace-projects',
      workspaceId,
    ],
    queryFn: () =>
      apiRequest<PageResponse<AdminProject>>(
        `/admin/workspaces/${workspaceId}/projects?${pageQuery(100, 0)}`
      ),
    enabled: Boolean(workspaceId),
  })
}

export function adminProjectQuotasQueryOptions(
  workspaceId: string | undefined,
  projectId: string | undefined
) {
  return queryOptions({
    queryKey: [
      'platform-admin',
      'organization',
      'project-quotas',
      workspaceId,
      projectId,
    ],
    queryFn: () =>
      apiRequest<AdminProjectQuota[]>(
        `/admin/workspaces/${workspaceId}/projects/${projectId}/quotas`
      ),
    enabled: Boolean(workspaceId && projectId),
  })
}

export function createAdminUser(payload: AdminUserCreateRequest) {
  return apiRequest<AdminUserCreateResponse>('/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateAdminUser(
  userId: string,
  payload: Partial<
    Pick<AdminUser, 'display_name' | 'username' | 'platform_admin'>
  >
) {
  return apiRequest<AdminUser>(`/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function updateAdminUserStatus(
  userId: string,
  status: 'active' | 'disabled'
) {
  return apiRequest<AdminUser>(`/admin/users/${userId}/status`, {
    method: 'PUT',
    body: JSON.stringify({ status }),
  })
}

export function resetAdminUserPassword(userId: string) {
  return apiRequest<AdminUser & { temporary_password: string }>(
    `/admin/users/${userId}/reset-password`,
    { method: 'POST' }
  )
}

export function revokeAdminUserTokens(userId: string) {
  return apiRequest<{ revoked: number }>(
    `/admin/users/${userId}/revoke-tokens`,
    {
      method: 'POST',
    }
  )
}

export function updateAdminWorkspaceStatus(
  workspaceId: string,
  status: 'active' | 'paused' | 'disabled' | 'archived'
) {
  return apiRequest<AdminWorkspace>(`/admin/workspaces/${workspaceId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function addAdminWorkspaceMember(
  workspaceId: string,
  userId: string,
  role: AdminWorkspaceMember['role']
) {
  return apiRequest<AdminWorkspaceMember>(
    `/admin/workspaces/${workspaceId}/members`,
    {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, role }),
    }
  )
}

export function updateAdminWorkspaceMember(
  workspaceId: string,
  memberId: string,
  payload: Partial<Pick<AdminWorkspaceMember, 'role' | 'status'>>
) {
  return apiRequest<AdminWorkspaceMember>(
    `/admin/workspaces/${workspaceId}/members/${memberId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }
  )
}

export function removeAdminWorkspaceMember(
  workspaceId: string,
  memberId: string
) {
  return apiRequest<AdminWorkspaceMember>(
    `/admin/workspaces/${workspaceId}/members/${memberId}`,
    { method: 'DELETE' }
  )
}

export function updateAdminProjectStatus(
  workspaceId: string,
  projectId: string,
  status: 'active' | 'archived' | 'disabled'
) {
  return apiRequest<AdminProject>(
    `/admin/workspaces/${workspaceId}/projects/${projectId}/status`,
    {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }
  )
}

export function upsertAdminProjectQuotas(
  workspaceId: string,
  projectId: string,
  quotas: Array<Pick<AdminProjectQuota, 'quota_key' | 'limit_value' | 'unit'>>
) {
  return apiRequest<AdminProjectQuota[]>(
    `/admin/workspaces/${workspaceId}/projects/${projectId}/quotas`,
    {
      method: 'PUT',
      body: JSON.stringify({ quotas }),
    }
  )
}

export function disableAdminProjectQuota(
  workspaceId: string,
  projectId: string,
  quotaKey: string
) {
  return apiRequest<AdminProjectQuota>(
    `/admin/workspaces/${workspaceId}/projects/${projectId}/quotas/${encodeURIComponent(quotaKey)}`,
    { method: 'DELETE' }
  )
}
