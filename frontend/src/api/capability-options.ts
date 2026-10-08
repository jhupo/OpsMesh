import { infiniteQueryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'
import { type PageResponse } from './workspaces'

export type CapabilityOption = { id: string; name: string }
export type OptionSource =
  | 'workspaces'
  | 'users'
  | 'teams'
  | 'credentials'
  | 'mcp'
  | 'tools'
  | 'skills'
  | 'experts'
  | 'knowledge'
  | 'runtime'

// Only existing read contracts. A 403 is retained; platform role does not bypass tenant access.
export function capabilityOptions(source: OptionSource, workspaceId?: string) {
  const paths: Record<OptionSource, string> = {
    workspaces: '/admin/workspaces',
    users: '/admin/users',
    teams: '/teams',
    credentials: '/capabilities/mcp-credentials',
    mcp: '/capabilities/mcp-servers',
    tools: '/capabilities',
    skills: '/capabilities/skills',
    experts: '/agents',
    knowledge: '/knowledge/sources',
    runtime: '/runtime-spaces',
  }
  const global = source === 'workspaces' || source === 'users'
  const path = `${global ? '' : `/workspaces/${encodeURIComponent(workspaceId ?? '')}`}${paths[source]}`
  return infiniteQueryOptions({
    queryKey: ['capability-options', path],
    initialPageParam: 0,
    queryFn: async ({
      pageParam,
      signal,
    }): Promise<PageResponse<CapabilityOption>> => {
      const page = await apiRequest<
        PageResponse<{
          id: string
          name?: string
          display_name?: string
          email?: string
          key?: string
        }>
      >(`${path}?limit=50&offset=${pageParam}` as `/${string}`, { signal })
      return {
        ...page,
        items: page.items.map((item) => ({
          id: item.id,
          name: item.name || item.display_name || item.email || item.key || '—',
        })),
      }
    },
    getNextPageParam: (page) =>
      page.offset + page.items.length < page.total && page.items.length
        ? page.offset + page.items.length
        : undefined,
    enabled: global || !!workspaceId,
    staleTime: 30_000,
    retry: false,
  })
}
