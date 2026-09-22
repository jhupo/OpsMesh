import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type ReleaseVersion = {
  version: string
  tag: string
  commit: string | null
}

export type PlatformOverview = {
  workspaces_total: number
  workspaces_active: number
  workers_total: number
  workers_online: number
  workers_draining: number
  active_worker_leases: number
  runtime_spaces_total: number
  runtime_spaces_quarantined: number
  runtimes_running: number
  runtimes_offline: number
  critical_security_events: number
}

export type PlatformAnalyticsRange = '24h' | '7d' | '30d'

export type PlatformAnalyticsStatus =
  | 'healthy'
  | 'warning'
  | 'critical'
  | 'degraded'

export type PlatformAnalyticsTimelinePoint = {
  timestamp: string
  requests: number
  successful_requests: number
  failed_requests: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  total_tokens: number
  average_duration_ms: number
  cost_usd: number
}

export type PlatformAnalyticsOverview = {
  range: PlatformAnalyticsRange
  generated_at: string
  status: {
    overall: PlatformAnalyticsStatus
    healthy_percent: number
    warning_percent: number
    critical_percent: number
    degraded_percent: number
  }
  totals: {
    requests: number
    successful_requests: number
    success_rate: number
    input_tokens: number
    output_tokens: number
    cached_tokens: number
    total_tokens: number
    average_duration_ms: number
    p95_duration_ms: number
    cost_usd: number
  }
  timeline: PlatformAnalyticsTimelinePoint[]
  providers: Array<{
    provider: string
    requests: number
    tokens: number
    cost_usd: number
    success_rate: number
  }>
  models: Array<{
    provider: string
    model: string
    requests: number
    success_rate: number
    p50_duration_ms: number
    p95_duration_ms: number
    error_rate: number
    throughput_per_minute: number
    cost_usd: number
    status: PlatformAnalyticsStatus
  }>
}

type QueueSummary = {
  queue_name: string
  queued: number
  dead_letter: number
  idempotency_keys: number
  oldest_queued_at: string | null
  highest_priority: number | null
}

type WorkerSummary = {
  total: number
  online: number
  draining: number
  offline: number
  by_status: Record<string, number>
  by_type: Record<string, number>
  running_leases: number
  total_capacity: number
  available_capacity: number
}

type RuntimeSpaceSummary = {
  total: number
  active: number
  quarantined: number
  quota_usage: Record<
    string,
    {
      limit_value: number
      reserved_value: number
      unit: string
      max_utilization: number
    }
  >
}

type ApprovalSummary = {
  pending: number
  runs_waiting: number
  tasks_waiting: number
}

type FailureSummary = {
  failed_runs: number
  failed_worker_leases: number
  top_run_error_codes: Array<{ key: string; count: number }>
  top_security_reasons: Array<{ key: string; count: number }>
}

export type PlatformOperationsSummary = {
  queue: QueueSummary
  workers: WorkerSummary
  runtime_spaces: RuntimeSpaceSummary
  approvals: ApprovalSummary
  failures: FailureSummary
}

export type PlatformSystemConfiguration = {
  settings: {
    environment: string
    service_name: string
    api_prefix: string
    log_level: string
    worker_queue_name: string
    tracing_enabled: boolean
    enable_api_docs: boolean
  }
  recommended_resources: Record<string, number>
  configured_resources: Record<string, number>
  resource_deltas: Record<string, number>
  blocking_executor: {
    configured_workers: number
    active_threads: number
    queued_work_items: number
    initialized: boolean
  }
  database_pool: {
    backend: string
    pool_class: string
    pool_size: number | null
    checked_in: number | null
    checked_out: number | null
    overflow: number | null
    max_overflow: number | null
    status: string
  }
  redis_pool: {
    max_connections: number | null
    created_connections: number | null
    available_connections: number | null
    in_use_connections: number | null
  }
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

export function platformOverviewQueryOptions() {
  return queryOptions({
    queryKey: ['platform-admin', 'overview'],
    queryFn: () => apiRequest<PlatformOverview>('/admin/overview'),
    staleTime: 30_000,
  })
}

export function platformAnalyticsOverviewQueryOptions(
  range: PlatformAnalyticsRange
) {
  return queryOptions({
    queryKey: ['platform-admin', 'analytics', 'overview', range],
    queryFn: () =>
      apiRequest<PlatformAnalyticsOverview>(
        `/admin/analytics/overview?range=${range}`
      ),
    retry: false,
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
}

export function platformOperationsSummaryQueryOptions() {
  return queryOptions({
    queryKey: ['platform-admin', 'operations', 'summary'],
    queryFn: () =>
      apiRequest<PlatformOperationsSummary>('/admin/operations/summary'),
    staleTime: 30_000,
  })
}

export function platformSystemVersionQueryOptions() {
  return queryOptions({
    queryKey: ['platform-admin', 'system', 'version'],
    queryFn: () => apiRequest<ReleaseVersion>('/admin/system/version'),
    staleTime: 15 * 60_000,
  })
}

export function platformSystemConfigurationQueryOptions() {
  return queryOptions({
    queryKey: ['platform-admin', 'system', 'configuration'],
    queryFn: () =>
      apiRequest<PlatformSystemConfiguration>('/admin/system/configuration'),
    staleTime: 60_000,
  })
}
