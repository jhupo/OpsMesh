import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type WorkspaceResourceView =
  | 'agents'
  | 'teams'
  | 'orchestrations'
  | 'approvals'
  | 'automations'
  | 'schedules'
  | 'webhooks'
  | 'tools'
  | 'skills'
  | 'mcp'
  | 'plugins'
  | 'files'
  | 'knowledge'
  | 'memory'
  | 'runtimes'
  | 'runtime-spaces'
  | 'workers'
  | 'queues'
  | 'members'
  | 'projects'
  | 'quotas'
  | 'costs'

export type WorkspaceResourceConfig = {
  titleKey: string
  path: (workspaceId: string) => `/${string}`
}

const page = (path: string) =>
  `${path}${path.includes('?') ? '&' : '?'}limit=100&offset=0` as `/${string}`

export const workspaceResourceConfigs: Record<
  WorkspaceResourceView,
  WorkspaceResourceConfig
> = {
  agents: {
    titleKey: 'agents',
    path: (id) => page(`/workspaces/${id}/agents`),
  },
  teams: {
    titleKey: 'teams',
    path: (id) => page(`/workspaces/${id}/teams`),
  },
  orchestrations: {
    titleKey: 'orchestration',
    path: (id) => page(`/workspaces/${id}/orchestrations`),
  },
  approvals: {
    titleKey: 'approvals',
    path: (id) => page(`/workspaces/${id}/approvals`),
  },
  automations: {
    titleKey: 'automations',
    path: (id) => `/workspaces/${id}/automations`,
  },
  schedules: {
    titleKey: 'schedules',
    path: (id) => page(`/workspaces/${id}/scheduled-jobs`),
  },
  webhooks: {
    titleKey: 'webhooks',
    path: (id) => page(`/workspaces/${id}/webhook-subscriptions`),
  },
  tools: {
    titleKey: 'tools',
    path: (id) => page(`/workspaces/${id}/capabilities`),
  },
  skills: {
    titleKey: 'skills',
    path: (id) => page(`/workspaces/${id}/capabilities/workspace-skills`),
  },
  mcp: {
    titleKey: 'mcp',
    path: (id) => page(`/workspaces/${id}/capabilities/mcp-servers`),
  },
  plugins: {
    titleKey: 'plugins',
    path: (id) => page(`/workspaces/${id}/plugins`),
  },
  files: {
    titleKey: 'files',
    path: (id) => page(`/workspaces/${id}/files`),
  },
  knowledge: {
    titleKey: 'knowledge',
    path: (id) => page(`/workspaces/${id}/knowledge/sources`),
  },
  memory: {
    titleKey: 'memory',
    path: (id) => page(`/workspaces/${id}/memories/semantic`),
  },
  runtimes: {
    titleKey: 'runtimes',
    path: (id) => page(`/workspaces/${id}/runtimes`),
  },
  'runtime-spaces': {
    titleKey: 'runtimeSpaces',
    path: (id) => page(`/workspaces/${id}/runtime-spaces`),
  },
  workers: {
    titleKey: 'workers',
    path: (id) => page(`/workspaces/${id}/operations/workers`),
  },
  queues: {
    titleKey: 'queues',
    path: (id) => `/workspaces/${id}/operations/queue-metrics`,
  },
  members: {
    titleKey: 'members',
    path: (id) => page(`/workspaces/${id}/members`),
  },
  projects: {
    titleKey: 'projects',
    path: (id) => page(`/workspaces/${id}/projects`),
  },
  quotas: {
    titleKey: 'quotas',
    path: (id) => page(`/workspaces/${id}/quotas`),
  },
  costs: {
    titleKey: 'costs',
    path: (id) => `/workspaces/${id}/costs/summary`,
  },
}

export function workspaceResourceQueryOptions(
  view: WorkspaceResourceView,
  workspaceId: string | undefined
) {
  const titleKey = workspaceResourceConfigs[view].titleKey
  return queryOptions({
    queryKey: ['workspace-console', view, titleKey, workspaceId],
    queryFn: ({ signal }) =>
      apiRequest<unknown>(workspaceResourceConfigs[view].path(workspaceId!), {
        signal,
      }),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
  })
}
