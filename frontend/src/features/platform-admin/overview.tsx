import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Building2,
  CheckCircle2,
  Clock3,
  Container,
  Database,
  GitCommitHorizontal,
  Layers3,
  ListChecks,
  MemoryStick,
  Network,
  ServerCog,
  ShieldAlert,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  type PlatformAnalyticsRange,
  type PlatformOperationsSummary,
  platformAnalyticsOverviewQueryOptions,
  platformOperationsSummaryQueryOptions,
  platformOverviewQueryOptions,
  platformSystemConfigurationQueryOptions,
  platformSystemVersionQueryOptions,
} from '@/api/platform-admin'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Main } from '@/components/layout/main'
import { PlatformOverviewAnalytics } from './overview-analytics'

type MetricCardProps = {
  icon: LucideIcon
  title: string
  value: number | undefined
  pending: boolean
  detail: string
  attention?: boolean
}

export function PlatformAdminOverview() {
  const { t } = useTranslation()
  const [range, setRange] = useState<PlatformAnalyticsRange>('24h')
  const analytics = useQuery(platformAnalyticsOverviewQueryOptions(range))
  const overview = useQuery(platformOverviewQueryOptions())
  const operations = useQuery(platformOperationsSummaryQueryOptions())
  const version = useQuery(platformSystemVersionQueryOptions())
  const configuration = useQuery(platformSystemConfigurationQueryOptions())
  const stats = overview.data
  const ops = operations.data
  const config = configuration.data

  return (
    <Main className='flex flex-1 flex-col gap-6'>
      <div className='flex items-center justify-between gap-4'>
        <h1 className='text-2xl font-bold tracking-tight text-balance'>
          {t('platformAdmin.navigation.overview')}
        </h1>
        <Select
          value={range}
          onValueChange={(value) => setRange(value as PlatformAnalyticsRange)}
        >
          <SelectTrigger
            className='w-28'
            aria-label={t('platformAdmin.overview.timeRange')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent align='end'>
            {(['24h', '7d', '30d'] as const).map((value) => (
              <SelectItem key={value} value={value}>
                {t(`platformAdmin.overview.ranges.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <PlatformOverviewAnalytics
        data={analytics.data}
        pending={analytics.isPending}
        error={analytics.isError}
        range={range}
      />

      <section
        aria-label={t('platformAdmin.overview.platformResources')}
        className='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'
      >
        <MetricCard
          icon={Building2}
          title={t('platformAdmin.overview.workspaces')}
          value={stats?.workspaces_total}
          pending={overview.isPending}
          detail={t('platformAdmin.overview.activeCount', {
            count: stats?.workspaces_active ?? 0,
          })}
        />
        <MetricCard
          icon={UsersRound}
          title={t('platformAdmin.overview.workers')}
          value={stats?.workers_online}
          pending={overview.isPending}
          detail={t('platformAdmin.overview.totalCount', {
            count: stats?.workers_total ?? 0,
          })}
          attention={(stats?.workers_total ?? 0) > (stats?.workers_online ?? 0)}
        />
        <MetricCard
          icon={Container}
          title={t('platformAdmin.overview.runtimes')}
          value={stats?.runtimes_running}
          pending={overview.isPending}
          detail={t('platformAdmin.overview.offlineCount', {
            count: stats?.runtimes_offline ?? 0,
          })}
          attention={(stats?.runtimes_offline ?? 0) > 0}
        />
        <MetricCard
          icon={ShieldAlert}
          title={t('platformAdmin.overview.securityEvents')}
          value={stats?.critical_security_events}
          pending={overview.isPending}
          detail={t('platformAdmin.overview.critical')}
          attention={(stats?.critical_security_events ?? 0) > 0}
        />
      </section>

      <div className='grid gap-4 xl:grid-cols-7'>
        <Card className='xl:col-span-4'>
          <CardHeader>
            <CardTitle>{t('platformAdmin.overview.operations')}</CardTitle>
            <CardAction>
              <Badge
                variant={
                  operations.isError || operationNeedsAttention(ops)
                    ? 'destructive'
                    : 'secondary'
                }
              >
                {operations.isPending
                  ? t('common.loading')
                  : operations.isError
                    ? t('platformAdmin.overview.unavailable')
                    : operationNeedsAttention(ops)
                      ? t('platformAdmin.overview.attention')
                      : t('platformAdmin.overview.normal')}
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent className='grid gap-3 sm:grid-cols-2'>
            <OperationalItem
              icon={Layers3}
              label={t('platformAdmin.overview.queue')}
              value={ops?.queue.queued}
              pending={operations.isPending}
              meta={t('platformAdmin.overview.deadLetterCount', {
                count: ops?.queue.dead_letter ?? 0,
              })}
            />
            <OperationalItem
              icon={ListChecks}
              label={t('platformAdmin.overview.approvals')}
              value={ops?.approvals.pending}
              pending={operations.isPending}
              meta={t('platformAdmin.overview.waitingCount', {
                count:
                  (ops?.approvals.runs_waiting ?? 0) +
                  (ops?.approvals.tasks_waiting ?? 0),
              })}
            />
            <OperationalItem
              icon={Activity}
              label={t('platformAdmin.overview.workerCapacity')}
              value={ops?.workers.available_capacity}
              pending={operations.isPending}
              meta={t('platformAdmin.overview.totalCount', {
                count: ops?.workers.total_capacity ?? 0,
              })}
            />
            <OperationalItem
              icon={ServerCog}
              label={t('platformAdmin.overview.runtimeSpaces')}
              value={ops?.runtime_spaces.active}
              pending={operations.isPending}
              meta={t('platformAdmin.overview.quarantinedCount', {
                count: ops?.runtime_spaces.quarantined ?? 0,
              })}
            />
          </CardContent>
        </Card>

        <Card className='xl:col-span-3'>
          <CardHeader>
            <CardTitle>
              {t('platformAdmin.overview.systemInformation')}
            </CardTitle>
            <CardAction>
              <Badge variant='outline'>
                {configuration.isPending
                  ? t('common.loading')
                  : environmentLabel(t, config?.settings.environment)}
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <dl className='flex flex-col'>
              <SystemRow
                icon={GitCommitHorizontal}
                label={t('platformAdmin.overview.version')}
                value={versionLabel(version.data)}
                pending={version.isPending}
              />
              <Separator />
              <SystemRow
                icon={Network}
                label={t('platformAdmin.overview.service')}
                value={config?.settings.service_name}
                pending={configuration.isPending}
              />
              <Separator />
              <SystemRow
                icon={Database}
                label={t('platformAdmin.overview.database')}
                value={poolLabel(
                  config?.database_pool.backend,
                  config?.database_pool.checked_out,
                  config?.database_pool.pool_size
                )}
                pending={configuration.isPending}
              />
              <Separator />
              <SystemRow
                icon={MemoryStick}
                label={t('platformAdmin.overview.redis')}
                value={connectionLabel(
                  config?.redis_pool.in_use_connections,
                  config?.redis_pool.max_connections
                )}
                pending={configuration.isPending}
              />
              <Separator />
              <SystemRow
                icon={config?.settings.tracing_enabled ? CheckCircle2 : Clock3}
                label={t('platformAdmin.overview.tracing')}
                value={
                  config
                    ? t(
                        config.settings.tracing_enabled
                          ? 'platformAdmin.overview.enabled'
                          : 'platformAdmin.overview.disabled'
                      )
                    : undefined
                }
                pending={configuration.isPending}
              />
            </dl>
          </CardContent>
        </Card>
      </div>
    </Main>
  )
}

function MetricCard({
  icon: Icon,
  title,
  value,
  pending,
  detail,
  attention = false,
}: MetricCardProps) {
  return (
    <Card className='gap-4 py-4'>
      <CardHeader>
        <CardTitle className='text-sm font-medium'>{title}</CardTitle>
        <CardAction className='flex size-9 items-center justify-center rounded-lg border bg-muted/40 text-muted-foreground'>
          <Icon className='size-4' aria-hidden='true' />
        </CardAction>
      </CardHeader>
      <CardContent className='flex items-end justify-between gap-3'>
        {pending ? (
          <Skeleton className='h-8 w-16' />
        ) : (
          <span className='text-2xl font-bold tabular-nums'>
            {value ?? '-'}
          </span>
        )}
        {pending ? (
          <Skeleton className='h-5 w-14' />
        ) : (
          <Badge variant={attention ? 'destructive' : 'secondary'}>
            {detail}
          </Badge>
        )}
      </CardContent>
    </Card>
  )
}

function OperationalItem({
  icon: Icon,
  label,
  value,
  pending,
  meta,
}: {
  icon: LucideIcon
  label: string
  value: number | undefined
  pending: boolean
  meta: string
}) {
  return (
    <div className='flex min-w-0 items-center gap-3 rounded-lg border p-4'>
      <div className='flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground'>
        <Icon className='size-4' aria-hidden='true' />
      </div>
      <div className='min-w-0 flex-1'>
        <div className='text-sm font-medium'>{label}</div>
        <div className='truncate text-xs text-muted-foreground'>{meta}</div>
      </div>
      {pending ? (
        <Skeleton className='h-7 w-10' />
      ) : (
        <div className='text-xl font-semibold tabular-nums'>{value ?? '-'}</div>
      )}
    </div>
  )
}

function SystemRow({
  icon: Icon,
  label,
  value,
  pending,
}: {
  icon: LucideIcon
  label: string
  value: string | undefined
  pending: boolean
}) {
  return (
    <div className='flex min-w-0 items-center gap-3 py-3 first:pt-0 last:pb-0'>
      <Icon
        className='size-4 shrink-0 text-muted-foreground'
        aria-hidden='true'
      />
      <dt className='text-sm text-muted-foreground'>{label}</dt>
      <dd className='ms-auto min-w-0 truncate text-sm font-medium'>
        {pending ? <Skeleton className='h-5 w-20' /> : value || '-'}
      </dd>
    </div>
  )
}

function operationNeedsAttention(
  operations: PlatformOperationsSummary | undefined
): boolean {
  return Boolean(
    operations &&
    (operations.queue.dead_letter > 0 ||
      operations.failures.failed_runs > 0 ||
      operations.failures.failed_worker_leases > 0 ||
      operations.runtime_spaces.quarantined > 0)
  )
}

function environmentLabel(
  t: ReturnType<typeof useTranslation>['t'],
  environment: string | undefined
): string {
  if (!environment) return '-'
  const key = `platformAdmin.overview.environments.${environment}`
  return t(key, { defaultValue: environment })
}

function connectionLabel(
  inUse: number | null | undefined,
  total: number | null | undefined
) {
  if (inUse === undefined || total === undefined) return undefined
  if (inUse === null || total === null) return '-'
  return `${inUse} / ${total}`
}

function poolLabel(
  backend: string | undefined,
  inUse: number | null | undefined,
  total: number | null | undefined
) {
  if (!backend) return undefined
  const connections = connectionLabel(inUse, total)
  return connections ? `${backend}  ${connections}` : backend
}

function versionLabel(
  version: { version: string; tag: string; commit: string | null } | undefined
) {
  if (!version) return undefined
  const label = version.tag || version.version
  return version.commit ? `${label}  ${version.commit.slice(0, 8)}` : label
}
