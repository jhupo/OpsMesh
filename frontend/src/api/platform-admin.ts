import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

type ReleaseVersion = {
  version: string
  tag: string
  commit: string | null
}

export type ReleaseUpdateCheck = {
  current: ReleaseVersion
  latest: ReleaseVersion | null
  update_available: boolean
  release_url: string | null
  cached: boolean
}

export function releaseUpdateCheckQueryOptions(enabled: boolean) {
  return queryOptions({
    queryKey: ['platform-admin', 'system', 'update-check'],
    queryFn: () =>
      apiRequest<ReleaseUpdateCheck>('/admin/system/check-updates'),
    enabled,
    retry: false,
    staleTime: 15 * 60_000,
  })
}
