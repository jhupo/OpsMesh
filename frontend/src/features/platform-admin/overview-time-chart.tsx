import { useTranslation } from 'react-i18next'
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import type { PlatformAnalyticsRange } from '@/api/platform-admin'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'

type TimePoint = { timestamp: string; [key: string]: string | number | null }

// Shared chart composition for the overview, using the source-owned shadcn chart.
export function OverviewTimeChart({
  points,
  config,
  range,
  pending = false,
  height = 'h-44',
  percent = false,
}: {
  points: TimePoint[] | undefined
  config: ChartConfig
  range: PlatformAnalyticsRange
  pending?: boolean
  height?: string
  percent?: boolean
}) {
  const { t, i18n } = useTranslation()
  if (pending) return <Skeleton className={`${height} w-full`} />
  if (!points?.length)
    return (
      <div
        className={`${height} flex items-center justify-center rounded-md border border-dashed text-xs text-muted-foreground`}
        role='status'
      >
        {t(`platformAdmin.overview.${points ? 'noData' : 'notCollected'}`)}
      </div>
    )
  const series = Object.keys(config)
  return (
    <ChartContainer config={config} className={`${height} w-full`}>
      <AreaChart
        accessibilityLayer
        data={points}
        margin={{ top: 5, right: 8, left: -12 }}
      >
        <CartesianGrid vertical={false} strokeDasharray='3 3' />
        <XAxis
          dataKey='timestamp'
          tickLine={false}
          axisLine={false}
          minTickGap={40}
          tickFormatter={(value) =>
            new Intl.DateTimeFormat(
              i18n.language,
              range === '24h'
                ? { hour: '2-digit', minute: '2-digit' }
                : { month: 'short', day: 'numeric' }
            ).format(new Date(value))
          }
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={44}
          allowDecimals={percent}
          domain={percent ? [0, 100] : [0, 'auto']}
          tickFormatter={(value) =>
            `${new Intl.NumberFormat(i18n.language, { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value))}${percent ? '%' : ''}`
          }
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(value) =>
                new Intl.DateTimeFormat(i18n.language, {
                  dateStyle: 'short',
                  timeStyle: 'short',
                }).format(new Date(String(value)))
              }
              formatter={(value, name) => (
                <span className='flex w-full justify-between gap-5'>
                  <span className='text-muted-foreground'>
                    {config[String(name)]?.label}
                  </span>
                  <span className='font-mono tabular-nums'>
                    {new Intl.NumberFormat(i18n.language, {
                      maximumFractionDigits: 1,
                    }).format(Number(value))}
                    {percent ? '%' : ''}
                  </span>
                </span>
              )}
            />
          }
        />
        {series.map((key) => (
          <Area
            key={key}
            dataKey={key}
            type='monotone'
            stroke={config[key].color}
            fill={config[key].color}
            fillOpacity={0.07}
            strokeWidth={1.8}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ChartContainer>
  )
}
