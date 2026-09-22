import { useMemo, useState } from 'react'
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
  CartesianGrid,
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
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

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

  if (error || !data) {
    return (
      <Card>
        <CardContent className='flex min-h-32 items-center justify-center text-sm text-muted-foreground'>
          {t('platformAdmin.overview.analyticsUnavailable')}
        </CardContent>
      </Card>
    )
  }

  return (
    <div className='flex flex-col gap-4'>
      <HealthStrip status={data.status} />

      <section
        aria-label={t('platformAdmin.overview.usageMetrics')}
        className='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'
      >
        <AnalyticsMetricCard
          icon={Activity}
          label={t('platformAdmin.overview.requests')}
          value={formatCompact(data.totals.requests, locale)}
          meta={formatPercent(data.totals.success_rate, locale)}
          points={data.timeline}
          dataKey='requests'
          color='var(--chart-1)'
        />
        <AnalyticsMetricCard
          icon={Coins}
          label={t('platformAdmin.overview.tokens')}
          value={formatCompact(data.totals.total_tokens, locale)}
          meta={formatCompact(data.totals.cached_tokens, locale)}
          points={data.timeline}
          dataKey='total_tokens'
          color='var(--chart-2)'
        />
        <AnalyticsMetricCard
          icon={Clock3}
          label={t('platformAdmin.overview.averageDuration')}
          value={formatDuration(data.totals.average_duration_ms, locale)}
          meta={`P95 ${formatDuration(data.totals.p95_duration_ms, locale)}`}
          points={data.timeline}
          dataKey='average_duration_ms'
          color='var(--chart-4)'
        />
        <AnalyticsMetricCard
          icon={BadgeDollarSign}
          label={t('platformAdmin.overview.cost')}
          value={formatCurrency(data.totals.cost_usd, locale)}
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
      <CardContent className='grid grid-cols-[1fr_7rem] items-end gap-3 px-4'>
        <div className='min-w-0'>
          <div className='truncate text-2xl font-semibold tracking-tight tabular-nums'>
            {value}
          </div>
          <div className='mt-1 truncate text-xs text-muted-foreground'>
            {meta}
          </div>
        </div>
        <ChartContainer config={config} className='h-14 w-full'>
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
  const [metric, setMetric] = useState<TrendMetric>('requests')
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
        <CardTitle>{t('platformAdmin.overview.usageTrend')}</CardTitle>
        <CardAction className='col-start-1 row-start-2 w-full min-w-0 justify-self-stretch sm:col-start-2 sm:row-start-1 sm:w-auto sm:justify-self-end'>
          <Tabs
            className='w-full min-w-0'
            value={metric}
            onValueChange={(value) => setMetric(value as TrendMetric)}
          >
            <TabsList className='grid h-8 w-full min-w-0 grid-cols-4 sm:flex sm:w-auto'>
              {(['requests', 'tokens', 'latency', 'cost'] as const).map(
                (value) => (
                  <TabsTrigger
                    key={value}
                    value={value}
                    className='min-w-0 px-1 text-xs sm:px-2.5'
                  >
                    {t(`platformAdmin.overview.trends.${value}`)}
                  </TabsTrigger>
                )
              )}
            </TabsList>
          </Tabs>
        </CardAction>
      </CardHeader>
      <CardContent>
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
                  <stop offset='8%' stopColor={entry.color} stopOpacity={0.3} />
                  <stop offset='95%' stopColor={entry.color} stopOpacity={0} />
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
        <CardTitle>{t('platformAdmin.overview.providerCost')}</CardTitle>
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
                className='mx-auto h-[220px] w-full max-w-[280px]'
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
              {providerData.slice(0, 6).map((provider) => (
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
        <CardTitle>{t('platformAdmin.overview.modelPerformance')}</CardTitle>
      </CardHeader>
      <CardContent className='overflow-x-auto px-0'>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className='ps-6'>
                {t('platformAdmin.overview.model')}
              </TableHead>
              <TableHead>{t('platformAdmin.overview.requests')}</TableHead>
              <TableHead>{t('platformAdmin.overview.successRate')}</TableHead>
              <TableHead>P50</TableHead>
              <TableHead>P95</TableHead>
              <TableHead>{t('platformAdmin.overview.throughput')}</TableHead>
              <TableHead>{t('platformAdmin.overview.cost')}</TableHead>
              <TableHead className='pe-6 text-right'>
                {t('platformAdmin.overview.state')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.models.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={8}
                  className='h-32 text-center text-muted-foreground'
                >
                  {t('platformAdmin.overview.noData')}
                </TableCell>
              </TableRow>
            ) : (
              data.models.map((model) => (
                <TableRow key={`${model.provider}:${model.model}`}>
                  <TableCell className='ps-6'>
                    <div className='font-medium'>{model.model}</div>
                    <div className='text-xs text-muted-foreground'>
                      {model.provider}
                    </div>
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatCompact(model.requests, locale)}
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatPercent(model.success_rate, locale)}
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatDuration(model.p50_duration_ms, locale)}
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatDuration(model.p95_duration_ms, locale)}
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatCompact(model.throughput_per_minute, locale)}/m
                  </TableCell>
                  <TableCell className='tabular-nums'>
                    {formatCurrency(model.cost_usd, locale)}
                  </TableCell>
                  <TableCell className='pe-6 text-right'>
                    <StatusBadge status={model.status} />
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
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
      <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'>
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className='h-32 rounded-xl' />
        ))}
      </div>
      <div className='grid gap-4 xl:grid-cols-7'>
        <Skeleton className='h-[390px] rounded-xl xl:col-span-5' />
        <Skeleton className='h-[390px] rounded-xl xl:col-span-2' />
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
