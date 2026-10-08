import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Cpu,
  MemoryStick,
  HardDrive,
  Server,
  Workflow,
  Boxes,
  Database,
  Cable,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  type PlatformOverview,
  type PlatformAnalyticsRange,
  platformOperationsSummaryQueryOptions,
  platformSystemConfigurationQueryOptions,
} from '@/api/platform-admin'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardAction,
} from '@/components/ui/card'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
} from '@/components/ui/select'
import { OverviewTimeChart } from './overview-time-chart'

export function PlatformOverviewSystem({
  overview,
  range,
}: {
  overview: PlatformOverview | undefined
  range: PlatformAnalyticsRange
}) {
  const { t, i18n } = useTranslation()
  const [hostId, setHostId] = useState('')
  const operations = useQuery({
    ...platformOperationsSummaryQueryOptions(),
    retry: false,
  })
  const configuration = useQuery({
    ...platformSystemConfigurationQueryOptions(),
    retry: false,
  })
  const count = (value: number | null | undefined) =>
    value == null
      ? '—'
      : new Intl.NumberFormat(i18n.language, {
          maximumFractionDigits: 1,
        }).format(value)
  const system = configuration.data
  const runtime = operations.data
  const hosts = Array.isArray(overview?.hosts)
    ? overview.hosts.filter((item) => item.scope === 'host')
    : []
  const host = hosts.find((item) => item.id === hostId) ?? hosts[0]
  const hostTimeline = host?.range === range ? host.timeline : undefined
  const operationsTimeline =
    overview?.operations_timeline?.range === range
      ? overview.operations_timeline.points ?? []
      : undefined
  const gib = (value: number | null | undefined) =>
    value == null ? '—' : `${count(value / 1024 ** 3)} GiB`
  const percent = (
    used: number | null | undefined,
    total: number | null | undefined
  ) =>
    used == null || total == null || total <= 0 ? null : (used / total) * 100
  const charts = [
    {
      label: 'cpu',
      icon: Cpu,
      key: 'cpu_percent',
      value: host?.cpu_percent,
      percent: true,
      points: hostTimeline,
      detail:
        host?.cpu_cores == null
          ? null
          : `${count(host.cpu_cores)} ${t('platformAdmin.overview.cores')}`,
    },
    {
      label: 'memory',
      icon: MemoryStick,
      key: 'memory_percent',
      value: percent(host?.memory_used_bytes, host?.memory_total_bytes),
      percent: true,
      points: hostTimeline,
      detail: host
        ? `${gib(host.memory_used_bytes)} / ${gib(host.memory_total_bytes)}`
        : null,
    },
    {
      label: 'disk',
      icon: HardDrive,
      key: 'disk_percent',
      value: percent(host?.disk_used_bytes, host?.disk_total_bytes),
      percent: true,
      points: hostTimeline,
      detail: host
        ? `${gib(host.disk_used_bytes)} / ${gib(host.disk_total_bytes)} · ${host.disk_mount}`
        : null,
    },
    {
      label: 'workers',
      icon: Server,
      key: 'workers_online',
      value: runtime?.workers?.online,
      percent: false,
      points: operationsTimeline,
      detail: runtime
        ? t('platformAdmin.overview.totalCount', {
            count: runtime.workers?.total ?? 0,
          })
        : null,
    },
    {
      label: 'queue',
      icon: Workflow,
      key: 'queued',
      value: runtime?.queue?.queued,
      percent: false,
      points: operationsTimeline,
      detail: runtime
        ? t('platformAdmin.overview.deadLetterCount', {
            count: runtime.queue?.dead_letter ?? 0,
          })
        : null,
    },
    {
      label: 'runtimes',
      icon: Boxes,
      key: 'runtimes_running',
      value: overview?.runtimes_running,
      percent: false,
      points: operationsTimeline,
      detail: overview
        ? t('platformAdmin.overview.offlineCount', {
            count: overview.runtimes_offline,
          })
        : null,
    },
    {
      label: 'database',
      icon: Database,
      key: 'database_connections',
      value: system?.database_pool?.checked_out,
      percent: false,
      points: operationsTimeline,
      detail: system?.database_pool?.backend ?? null,
    },
    {
      label: 'redis',
      icon: Cable,
      key: 'redis_connections',
      value: system?.redis_pool?.in_use_connections,
      percent: false,
      points: operationsTimeline,
      detail: null,
    },
  ]
  return (
    <Card className='min-w-0 gap-5'>
      <CardHeader>
        <CardTitle className='text-sm'>
          {t('platformAdmin.overview.systemRuntime')}
        </CardTitle>
        {hosts.length > 0 && (
          <CardAction>
            <Select value={host?.id} onValueChange={setHostId}>
              <SelectTrigger
                className='h-8 max-w-44 text-xs'
                aria-label={t('platformAdmin.overview.host')}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {hosts.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.hostname}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className='grid min-w-0 gap-x-6 gap-y-6 sm:grid-cols-2 xl:grid-cols-4'>
        {charts.map(
          ({ label, icon: Icon, key, value, percent, points, detail }) => (
            <section
              key={key}
              className='flex min-w-0 flex-col gap-3'
              aria-label={t(`platformAdmin.overview.${label}`)}
            >
              <h3 className='flex items-center gap-2 text-xs font-medium text-muted-foreground'>
                <Icon className='size-4' aria-hidden='true' />
                {t(`platformAdmin.overview.${label}`)}
              </h3>
              <div className='flex min-h-8 items-baseline justify-between gap-2'>
                <strong className='text-2xl font-semibold tabular-nums'>
                  {value == null ? '—' : `${count(value)}${percent ? '%' : ''}`}
                </strong>
                {detail && (
                  <span
                    className='truncate text-xs text-muted-foreground'
                    title={detail}
                  >
                    {detail}
                  </span>
                )}
              </div>
              <OverviewTimeChart
                points={points}
                config={{
                  [key]: {
                    label: t(`platformAdmin.overview.${label}`),
                    color: 'var(--chart-2)',
                  },
                }}
                range={range}
                percent={percent}
                height='h-32'
              />
            </section>
          )
        )}
      </CardContent>
    </Card>
  )
}
