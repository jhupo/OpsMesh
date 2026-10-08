import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  type PlatformAnalyticsRange,
  platformAnalyticsOverviewQueryOptions,
  platformOverviewQueryOptions,
} from '@/api/platform-admin'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Main } from '@/components/layout/main'
import { PlatformOverviewAnalytics } from './overview-analytics'
import { PlatformOverviewExecution } from './overview-execution'
import { PlatformOverviewSystem } from './overview-system'
import { PlatformPageHeading } from './page-heading'

export function PlatformAdminOverview() {
  const { t } = useTranslation()
  const [range, setRange] = useState<PlatformAnalyticsRange>('24h')
  const client = useQueryClient()
  const overview = useQuery({
    ...platformOverviewQueryOptions(range),
    retry: false,
  })
  const analytics = useQuery(platformAnalyticsOverviewQueryOptions(range))
  const analyticsData =
    analytics.data?.status && analytics.data?.totals
      ? analytics.data
      : undefined

  return (
    <Main className='flex min-w-0 flex-1 flex-col [&_[data-slot=card]]:shadow-none'>
      <PlatformPageHeading
        title={t('platformAdmin.navigation.overview')}
        actions={
          <div className='flex flex-wrap items-center gap-2'>
            <Select
              value={range}
              onValueChange={(value) =>
                setRange(value as PlatformAnalyticsRange)
              }
            >
              <SelectTrigger
                className='w-28'
                aria-label={t('platformAdmin.overview.timeRange')}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align='end'>
                <SelectGroup>
                  {(['24h', '7d', '30d'] as const).map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`platformAdmin.overview.ranges.${value}`)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <Button
              variant='outline'
              size='icon'
              disabled={overview.isFetching || analytics.isFetching}
              onClick={() =>
                void client.invalidateQueries({ queryKey: ['platform-admin'] })
              }
              aria-label={t('platformAdmin.overview.refresh')}
            >
              <RefreshCw
                className={
                  overview.isFetching || analytics.isFetching
                    ? 'motion-safe:animate-spin'
                    : ''
                }
                aria-hidden='true'
              />
            </Button>
          </div>
        }
      />

      <div className='flex min-w-0 flex-col gap-5'>
        <PlatformOverviewExecution
          data={
            overview.data?.execution?.range === range
              ? overview.data.execution
              : undefined
          }
          pending={overview.isPending}
          range={range}
        />
        <PlatformOverviewSystem overview={overview.data} range={range} />
        <PlatformOverviewAnalytics
          data={analyticsData}
          pending={analytics.isPending}
          error={analytics.isError || (!!analytics.data && !analyticsData)}
          range={range}
        />
      </div>
    </Main>
  )
}
