import { useMemo } from 'react'
import {
  Activity,
  BadgeDollarSign,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Coins,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from 'recharts'
import type {
  PlatformAnalyticsOverview,
  PlatformAnalyticsRange,
  PlatformAnalyticsStatus,
  PlatformAnalyticsTimelinePoint,
} from '@/api/platform-admin'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import { ReadOnlyDataTable } from '@/components/data-table'

type TrendMetric = 'requests' | 'tokens' | 'latency' | 'cost'

type PlatformOverviewAnalyticsProps = {
  data: PlatformAnalyticsOverview | undefined
  pending: boolean
  error: boolean
  range: PlatformAnalyticsRange
}

const chartColors = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
]

export function PlatformOverviewAnalytics({
  data,
  pending,
  error,
  range,
}: PlatformOverviewAnalyticsProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.resolvedLanguage === 'zh' ? 'zh-CN' : 'en-US'

  if (pending) return <AnalyticsSkeleton />

  const received = data
  const fallback: PlatformAnalyticsOverview = {
    range,
    generated_at: '',
    status: {
      overall: 'healthy',
      healthy_percent: 0,
      warning_percent: 0,
      critical_percent: 0,
      degraded_percent: 0,
    },
    totals: {
      requests: 0,
      successful_requests: 0,
      success_rate: 0,
      input_tokens: 0,
      output_tokens: 0,
      cached_tokens: 0,
      total_tokens: 0,
      average_duration_ms: 0,
      p95_duration_ms: 0,
      cost_usd: 0,
    },
    timeline: [],
    models: [],
    providers: [],
  }
  const unavailable = !received?.status || !received?.totals
  // Release skew is expected while the platform aggregate is rolled out. Normalize
  // every optional collection and metric before rendering cached or older responses.
  data = unavailable
    ? fallback
    : {
        ...fallback,
        ...received,
        status: { ...fallback.status, ...received.status },
        totals: { ...fallback.totals, ...received.totals },
        timeline: Array.isArray(received.timeline) ? received.timeline : [],
        models: Array.isArray(received.models) ? received.models : [],
        providers: Array.isArray(received.providers) ? received.providers : [],
      }

  return (
    <div className='flex flex-col gap-4'>
      {error || unavailable ? (
        <div
          role='status'
          className='flex items-center gap-2 rounded-lg border px-4 py-3 text-sm text-muted-foreground'
        >
          <CircleAlert className='size-4 shrink-0' aria-hidden='true' />
          {t('platformAdmin.overview.analyticsUnavailable')}
        </div>
      ) : (
        <HealthStrip status={data.status} />
      )}

      <section
        aria-label={t('platformAdmin.overview.usageMetrics')}
        className='grid grid-cols-2 gap-3 xl:grid-cols-4'
      >
        <AnalyticsMetricCard
          icon={Activity}
          label={t('platformAdmin.overview.requests')}
          value={
            unavailable ? '—' : formatCompact(data.totals.requests, locale)
          }
          meta={`${t('platformAdmin.overview.successRate')} ${unavailable ? '—' : formatPercent(data.totals.success_rate, locale)}`}
          points={data.timeline}
          dataKey='requests'
          color='var(--chart-1)'
        />
        <AnalyticsMetricCard
          icon={Coins}
          label={t('platformAdmin.overview.tokens')}
          value={
            unavailable ? '—' : formatCompact(data.totals.total_tokens, locale)
          }
          meta={`${t('platformAdmin.overview.cachedTokens')} ${unavailable ? '—' : formatCompact(data.totals.cached_tokens, locale)}`}
          points={data.timeline}
          dataKey='total_tokens'
          color='var(--chart-2)'
        />
        <AnalyticsMetricCard
          icon={Clock3}
          label={t('platformAdmin.overview.averageDuration')}
          value={
            unavailable
              ? '—'
              : formatDuration(data.totals.average_duration_ms, locale)
          }
          meta={`P95 ${unavailable ? '—' : formatDuration(data.totals.p95_duration_ms, locale)}`}
          points={data.timeline}
          dataKey='average_duration_ms'
          color='var(--chart-4)'
        />
        <AnalyticsMetricCard
          icon={BadgeDollarSign}
          label={t('platformAdmin.overview.cost')}
          value={
            unavailable ? '—' : formatCurrency(data.totals.cost_usd, locale)
          }
          meta={t(`platformAdmin.overview.ranges.${range}`)}
          points={data.timeline}
          dataKey='cost_usd'
          color='var(--chart-5)'
        />
      </section>

      <div className='grid min-w-0 gap-4 xl:grid-cols-7'>
        <UsageTrendCard data={data.timeline} range={range} locale={locale} />
        <ProviderCostCard data={data} locale={locale} />
      </div>

      <PerformanceTrends data={data} range={range} locale={locale} />
      <ModelPerformanceCard data={data} locale={locale} />
    </div>
  )
}

function HealthStrip({
  status,
}: {
  status: PlatformAnalyticsOverview['status']
}) {
  const { t } = useTranslation()
  const healthy = status.overall === 'healthy'

  return (
    <Card className='gap-0 py-0'>
      <CardContent className='flex min-h-14 flex-col justify-center gap-3 py-3 sm:flex-row sm:items-center sm:justify-between'>
        <div className='flex items-center gap-2 text-sm font-medium'>
          {healthy ? (
            <CheckCircle2 className='size-4 text-chart-2' aria-hidden='true' />
          ) : (
            <CircleAlert className='size-4 text-chart-4' aria-hidden='true' />
          )}
          {t(`platformAdmin.overview.status.${status.overall}`)}
        </div>
        <div className='flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground'>
          <HealthValue
            color='bg-chart-2'
            label={t('platformAdmin.overview.status.healthy')}
            value={status.healthy_percent}
          />
          <HealthValue
            color='bg-chart-4'
            label={t('platformAdmin.overview.status.warning')}
            value={status.warning_percent}
          />
          <HealthValue
            color='bg-destructive'
            label={t('platformAdmin.overview.status.critical')}
            value={status.critical_percent}
          />
          <HealthValue
            color='bg-chart-5'
            label={t('platformAdmin.overview.status.degraded')}
            value={status.degraded_percent}
          />
        </div>
      </CardContent>
    </Card>
  )
}

function HealthValue({
  color,
  label,
  value,
}: {
  color: string
  label: string
  value: number
}) {
  return (
    <span className='inline-flex items-center gap-1.5'>
      <span className={cn('size-1.5 rounded-full', color)} aria-hidden='true' />
      {label}
      <strong className='font-medium text-foreground tabular-nums'>
        {value}%
      </strong>
    </span>
  )
}

function AnalyticsMetricCard({
  icon: Icon,
  label,
  value,
  meta,
  points,
  dataKey,
  color,
}: {
  icon: LucideIcon
  label: string
  value: string
  meta: string
  points: PlatformAnalyticsTimelinePoint[]
  dataKey: keyof PlatformAnalyticsTimelinePoint
  color: string
}) {
  const config = useMemo(
    () => ({ value: { label, color } }) satisfies ChartConfig,
    [color, label]
  )

  return (
    <Card className='gap-3 py-4'>
      <CardHeader className='px-4'>
        <CardTitle className='flex items-center gap-2 text-sm font-medium text-muted-foreground'>
          <span className='flex size-8 items-center justify-center rounded-lg bg-muted text-foreground'>
            <Icon className='size-4' aria-hidden='true' />
          </span>
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className='grid grid-cols-1 items-end gap-3 px-4 sm:grid-cols-[1fr_7rem]'>
        <div className='min-w-0'>
          <div className='truncate text-2xl font-semibold tracking-tight tabular-nums'>
            {value}
          </div>
          <div className='mt-1 truncate text-xs text-muted-foreground'>
            {meta}
          </div>
        </div>
        <ChartContainer config={config} className='hidden h-14 w-full sm:block'>
          <AreaChart accessibilityLayer data={points}>
            <defs>
              <linearGradient
                id={`metric-${String(dataKey)}`}
                x1='0'
                y1='0'
                x2='0'
                y2='1'
              >
                <stop offset='10%' stopColor={color} stopOpacity={0.28} />
                <stop offset='95%' stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              dataKey={dataKey}
              type='monotone'
              fill={`url(#metric-${String(dataKey)})`}
              fillOpacity={1}
              stroke={color}
              strokeWidth={1.8}
              isAnimationActive={false}
            />
          </AreaChart>
        </ChartContainer>
      </CardContent>
    </Card>
  )
}

function UsageTrendCard({
  data,
  range,
  locale,
}: {
  data: PlatformAnalyticsTimelinePoint[]
  range: PlatformAnalyticsRange
  locale: string
}) {
  const { t } = useTranslation()
  const metric = 'tokens' as TrendMetric
  const config = {
    successful_requests: {
      label: t('platformAdmin.overview.successfulRequests'),
      color: 'var(--chart-2)',
    },
    failed_requests: {
      label: t('platformAdmin.overview.failedRequests'),
      color: 'var(--destructive)',
    },
    input_tokens: {
      label: t('platformAdmin.overview.inputTokens'),
      color: 'var(--chart-1)',
    },
    output_tokens: {
      label: t('platformAdmin.overview.outputTokens'),
      color: 'var(--chart-2)',
    },
    cached_tokens: {
      label: t('platformAdmin.overview.cachedTokens'),
      color: 'var(--chart-3)',
    },
    average_duration_ms: {
      label: t('platformAdmin.overview.averageDuration'),
      color: 'var(--chart-4)',
    },
    cost_usd: {
      label: t('platformAdmin.overview.cost'),
      color: 'var(--chart-5)',
    },
  } satisfies ChartConfig
  const series = trendSeries(metric)

  return (
    <Card className='min-w-0 xl:col-span-5'>
      <CardHeader className='min-w-0 gap-3 has-data-[slot=card-action]:grid-cols-1 sm:has-data-[slot=card-action]:grid-cols-[1fr_auto]'>
        <CardTitle className='text-sm'>
          {t('platformAdmin.overview.usageTrend')}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className='mb-5 grid grid-cols-3 gap-3 rounded-lg bg-muted/40 p-3 text-xs'>
          {series.slice(0, 3).map((entry) => (
            <div key={entry.key}>
              <dt className='truncate text-muted-foreground'>
                {config[entry.key].label}
              </dt>
              <dd className='mt-1 font-semibold tabular-nums'>
                {data.length
                  ? formatAxisValue(
                      metric,
                      metric === 'latency'
                        ? data.reduce(
                            (sum, point) =>
                              sum + point.average_duration_ms * point.requests,
                            0
                          ) /
                            Math.max(
                              1,
                              data.reduce(
                                (sum, point) => sum + point.requests,
                                0
                              )
                            )
                        : data.reduce(
                            (sum, point) => sum + Number(point[entry.key]),
                            0
                          ),
                      locale
                    )
                  : '—'}
              </dd>
            </div>
          ))}
        </dl>
        {data.length === 0 ? (
          <ChartEmpty />
        ) : (
          <ChartContainer
            config={config}
            className='h-[290px] w-full min-w-0 overflow-hidden'
            initialDimension={{ width: 240, height: 200 }}
          >
            <AreaChart
              accessibilityLayer
              data={data}
              margin={{ left: 2, right: 10 }}
            >
              <defs>
                {series.map((entry) => (
                  <linearGradient
                    key={entry.key}
                    id={`trend-${entry.key}`}
                    x1='0'
                    y1='0'
                    x2='0'
                    y2='1'
                  >
                    <stop
                      offset='8%'
                      stopColor={entry.color}
                      stopOpacity={0.3}
                    />
                    <stop
                      offset='95%'
                      stopColor={entry.color}
                      stopOpacity={0}
                    />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey='timestamp'
                tickLine={false}
                axisLine={false}
                tickMargin={10}
                minTickGap={28}
                tickFormatter={(value) =>
                  formatTimelineTick(value, locale, range)
                }
              />
              <YAxis
                width={48}
                tickLine={false}
                axisLine={false}
                tickFormatter={(value) =>
                  formatAxisValue(metric, Number(value), locale)
                }
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    indicator='line'
                    labelFormatter={(value) =>
                      formatTimelineLabel(String(value), locale)
                    }
                  />
                }
              />
              <ChartLegend content={<ChartLegendContent />} />
              {series.map((entry) => (
                <Area
                  key={entry.key}
                  dataKey={entry.key}
                  type='monotone'
                  fill={`url(#trend-${entry.key})`}
                  fillOpacity={1}
                  stroke={entry.color}
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}

function ProviderCostCard({
  data,
  locale,
}: {
  data: PlatformAnalyticsOverview
  locale: string
}) {
  const { t } = useTranslation()
  const providerData = data.providers.map((provider, index) => ({
    ...provider,
    fill: chartColors[index % chartColors.length],
  }))
  const config = Object.fromEntries(
    providerData.map((provider) => [
      provider.provider,
      { label: provider.provider, color: provider.fill },
    ])
  ) satisfies ChartConfig

  return (
    <Card className='xl:col-span-2'>
      <CardHeader>
        <CardTitle className='text-sm'>
          {t('platformAdmin.overview.providerCost')}
        </CardTitle>
      </CardHeader>
      <CardContent className='flex flex-col gap-2'>
        {providerData.length === 0 ? (
          <div className='flex min-h-[260px] items-center justify-center text-sm text-muted-foreground'>
            {t('platformAdmin.overview.noData')}
          </div>
        ) : (
          <>
            <div className='relative'>
              <ChartContainer
                config={config}
                className='mx-auto h-[250px] w-full max-w-[280px]'
              >
                <PieChart accessibilityLayer>
                  <ChartTooltip
                    content={
                      <ChartTooltipContent nameKey='provider' hideLabel />
                    }
                  />
                  <Pie
                    data={providerData}
                    dataKey='cost_usd'
                    nameKey='provider'
                    innerRadius={58}
                    outerRadius={86}
                    paddingAngle={2}
                    strokeWidth={0}
                    isAnimationActive={false}
                  />
                </PieChart>
              </ChartContainer>
              <div className='pointer-events-none absolute inset-0 flex flex-col items-center justify-center'>
                <span className='text-xs text-muted-foreground'>
                  {t('platformAdmin.overview.total')}
                </span>
                <strong className='text-lg tabular-nums'>
                  {formatCurrency(data.totals.cost_usd, locale)}
                </strong>
              </div>
            </div>
            <div className='grid gap-2'>
              {providerData.map((provider) => (
                <div
                  key={provider.provider}
                  className='flex min-w-0 items-center gap-2 text-xs'
                >
                  <span
                    className='size-2 shrink-0 rounded-full'
                    style={{ backgroundColor: provider.fill }}
                    aria-hidden='true'
                  />
                  <span className='truncate'>{provider.provider}</span>
                  <span className='ms-auto text-muted-foreground tabular-nums'>
                    {formatCurrency(provider.cost_usd, locale)}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function ChartEmpty() {
  const { t } = useTranslation()
  return (
    <div className='flex h-[250px] items-center justify-center text-sm text-muted-foreground'>
      {t('platformAdmin.overview.noData')}
    </div>
  )
}

function PerformanceTrends({
  data,
  range,
  locale,
}: {
  data: PlatformAnalyticsOverview
  range: PlatformAnalyticsRange
  locale: string
}) {
  const { t } = useTranslation()
  const config = {
    average_duration_ms: {
      label: t('platformAdmin.overview.averageDuration'),
      color: 'var(--chart-1)',
    },
    error_rate: {
      label: t('platformAdmin.overview.errorRate'),
      color: 'var(--destructive)',
    },
    cost_usd: {
      label: t('platformAdmin.overview.cost'),
      color: 'var(--chart-2)',
    },
  } satisfies ChartConfig
  const timeline = data.timeline.map((point) => ({
    ...point,
    error_rate: point.requests
      ? (point.failed_requests / point.requests) * 100
      : 0,
  }))
  const tick = (value: string) => formatTimelineTick(value, locale, range)
  return (
    <div className='grid min-w-0 gap-4 lg:grid-cols-2'>
      <Card className='min-w-0 gap-4'>
        <CardHeader>
          <CardTitle className='text-sm'>
            {t('platformAdmin.overview.responseHealth')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className='mb-4 flex gap-8 text-xs'>
            <div>
              <dt className='text-muted-foreground'>
                {t('platformAdmin.overview.averageDuration')}
              </dt>
              <dd className='mt-1 text-lg font-semibold tabular-nums'>
                {timeline.length
                  ? formatDuration(data.totals.average_duration_ms, locale)
                  : '—'}
              </dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>
                {t('platformAdmin.overview.errorRate')}
              </dt>
              <dd className='mt-1 text-lg font-semibold tabular-nums'>
                {timeline.length
                  ? formatPercent(
                      data.totals.requests
                        ? ((data.totals.requests -
                            data.totals.successful_requests) /
                            data.totals.requests) *
                            100
                        : 0,
                      locale
                    )
                  : '—'}
              </dd>
            </div>
          </dl>
          {!timeline.length ? (
            <ChartEmpty />
          ) : (
            <ChartContainer config={config} className='h-[220px] w-full'>
              <ComposedChart
                accessibilityLayer
                data={timeline}
                margin={{ right: 0, left: 0 }}
              >
                <CartesianGrid vertical={false} strokeDasharray='3 3' />
                <XAxis
                  dataKey='timestamp'
                  tickLine={false}
                  axisLine={false}
                  minTickGap={36}
                  tickFormatter={tick}
                />
                <YAxis
                  yAxisId='latency'
                  width={42}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) =>
                    formatDuration(Number(value), locale)
                  }
                />
                <YAxis
                  yAxisId='error'
                  orientation='right'
                  width={36}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => `${value}%`}
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      labelFormatter={(value) =>
                        formatTimelineLabel(String(value), locale)
                      }
                      formatter={(value, name) => (
                        <span className='flex w-full justify-between gap-6'>
                          <span className='text-muted-foreground'>
                            {config[name as keyof typeof config]?.label}
                          </span>
                          <span className='font-mono tabular-nums'>
                            {name === 'error_rate'
                              ? formatPercent(Number(value), locale)
                              : formatDuration(Number(value), locale)}
                          </span>
                        </span>
                      )}
                    />
                  }
                />
                <ChartLegend content={<ChartLegendContent />} />
                <Bar
                  yAxisId='error'
                  dataKey='error_rate'
                  fill='var(--destructive)'
                  fillOpacity={0.25}
                  radius={[2, 2, 0, 0]}
                  isAnimationActive={false}
                />
                <Line
                  yAxisId='latency'
                  dataKey='average_duration_ms'
                  type='monotone'
                  stroke='var(--chart-1)'
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>
      <Card className='min-w-0 gap-4'>
        <CardHeader>
          <CardTitle className='text-sm'>
            {t('platformAdmin.overview.costTrend')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className='mb-4 flex gap-8 text-xs'>
            <div>
              <dt className='text-muted-foreground'>
                {t('platformAdmin.overview.cost')}
              </dt>
              <dd className='mt-1 text-lg font-semibold tabular-nums'>
                {timeline.length
                  ? formatCurrency(data.totals.cost_usd, locale)
                  : '—'}
              </dd>
            </div>
            <div>
              <dt className='text-muted-foreground'>
                {t('platformAdmin.overview.costPerRequest')}
              </dt>
              <dd className='mt-1 text-lg font-semibold tabular-nums'>
                {timeline.length && data.totals.requests
                  ? formatCurrency(
                      data.totals.cost_usd / data.totals.requests,
                      locale
                    )
                  : '—'}
              </dd>
            </div>
          </dl>
          {!timeline.length ? (
            <ChartEmpty />
          ) : (
            <ChartContainer config={config} className='h-[220px] w-full'>
              <BarChart accessibilityLayer data={timeline}>
                <CartesianGrid vertical={false} strokeDasharray='3 3' />
                <XAxis
                  dataKey='timestamp'
                  tickLine={false}
                  axisLine={false}
                  minTickGap={36}
                  tickFormatter={tick}
                />
                <YAxis
                  width={48}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) =>
                    `$${formatCompact(Number(value), locale)}`
                  }
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      labelFormatter={(value) =>
                        formatTimelineLabel(String(value), locale)
                      }
                      formatter={(value) => (
                        <span className='font-mono tabular-nums'>
                          {formatCurrency(Number(value), locale)}
                        </span>
                      )}
                    />
                  }
                />
                <Bar
                  dataKey='cost_usd'
                  fill='var(--chart-2)'
                  radius={[3, 3, 0, 0]}
                  maxBarSize={24}
                  isAnimationActive={false}
                />
              </BarChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function ModelPerformanceCard({
  data,
  locale,
}: {
  data: PlatformAnalyticsOverview
  locale: string
}) {
  const { t } = useTranslation()

  return (
    <Card>
      <CardHeader>
        <CardTitle className='text-sm'>
          {t('platformAdmin.overview.modelPerformance')}
        </CardTitle>
      </CardHeader>
      <CardContent className='overflow-x-auto px-0'>
        <ReadOnlyDataTable
          data={data.models}
          columns={[
            {
              accessorKey: 'model',
              header: t('platformAdmin.overview.model'),
              cell: ({ row }) => (
                <div className='w-28 sm:w-52'>
                  <div
                    className='truncate font-medium'
                    title={row.original.model}
                  >
                    {row.original.model}
                  </div>
                  <div className='truncate text-xs text-muted-foreground'>
                    {row.original.provider}
                  </div>
                </div>
              ),
            },
            {
              accessorKey: 'requests',
              header: t('platformAdmin.overview.requests'),
              cell: ({ row }) => formatCompact(row.original.requests, locale),
            },
            {
              accessorKey: 'success_rate',
              header: t('platformAdmin.overview.successRate'),
              cell: ({ row }) =>
                formatPercent(row.original.success_rate, locale),
            },
            {
              accessorKey: 'p50_duration_ms',
              header: 'P50',
              cell: ({ row }) =>
                formatDuration(row.original.p50_duration_ms, locale),
            },
            {
              accessorKey: 'p95_duration_ms',
              header: 'P95',
              cell: ({ row }) =>
                formatDuration(row.original.p95_duration_ms, locale),
            },
            {
              accessorKey: 'throughput_per_minute',
              header: t('platformAdmin.overview.throughput'),
              cell: ({ row }) =>
                `${formatCompact(row.original.throughput_per_minute, locale)}/m`,
            },
            {
              accessorKey: 'cost_usd',
              header: t('platformAdmin.overview.cost'),
              cell: ({ row }) => formatCurrency(row.original.cost_usd, locale),
            },
            {
              accessorKey: 'status',
              header: t('platformAdmin.overview.state'),
              cell: ({ row }) => <StatusBadge status={row.original.status} />,
            },
          ]}
        />
      </CardContent>
    </Card>
  )
}

function StatusBadge({ status }: { status: PlatformAnalyticsStatus }) {
  const { t } = useTranslation()
  return (
    <Badge
      variant={
        status === 'critical'
          ? 'destructive'
          : status === 'healthy'
            ? 'secondary'
            : 'outline'
      }
    >
      {t(`platformAdmin.overview.status.${status}`)}
    </Badge>
  )
}

function AnalyticsSkeleton() {
  return (
    <div className='flex flex-col gap-4' aria-hidden='true'>
      <Skeleton className='h-14 w-full rounded-xl' />
      <div className='grid grid-cols-2 gap-3 xl:grid-cols-4'>
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className='h-32 rounded-xl' />
        ))}
      </div>
      <div className='grid gap-4 xl:grid-cols-7'>
        <Skeleton className='h-[390px] rounded-xl xl:col-span-5' />
        <Skeleton className='h-[390px] rounded-xl xl:col-span-2' />
      </div>
      <div className='grid gap-4 lg:grid-cols-2'>
        <Skeleton className='h-[360px] rounded-xl' />
        <Skeleton className='h-[360px] rounded-xl' />
      </div>
      <Skeleton className='h-80 rounded-xl' />
    </div>
  )
}

function trendSeries(metric: TrendMetric) {
  if (metric === 'requests') {
    return [
      { key: 'successful_requests', color: 'var(--chart-2)' },
      { key: 'failed_requests', color: 'var(--destructive)' },
    ] as const
  }
  if (metric === 'tokens') {
    return [
      { key: 'input_tokens', color: 'var(--chart-1)' },
      { key: 'output_tokens', color: 'var(--chart-2)' },
      { key: 'cached_tokens', color: 'var(--chart-3)' },
    ] as const
  }
  if (metric === 'latency') {
    return [{ key: 'average_duration_ms', color: 'var(--chart-4)' }] as const
  }
  return [{ key: 'cost_usd', color: 'var(--chart-5)' }] as const
}

function formatCompact(value: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

function formatPercent(value: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(value / 100)
}

function formatDuration(value: number, locale: string) {
  if (value >= 1000) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1000)}s`
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)}ms`
}

function formatCurrency(value: number, locale: string) {
  return `$${new Intl.NumberFormat(locale, {
    minimumFractionDigits: value < 1 ? 3 : 2,
    maximumFractionDigits: value < 1 ? 3 : 2,
  }).format(value)}`
}

function formatTimelineTick(
  value: string,
  locale: string,
  range: PlatformAnalyticsRange
) {
  return new Intl.DateTimeFormat(
    locale,
    range === '24h'
      ? { hour: '2-digit', minute: '2-digit' }
      : { month: 'short', day: 'numeric' }
  ).format(new Date(value))
}

function formatTimelineLabel(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatAxisValue(metric: TrendMetric, value: number, locale: string) {
  if (metric === 'latency') return formatDuration(value, locale)
  if (metric === 'cost') return formatCurrency(value, locale)
  return formatCompact(value, locale)
}
