import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  type PlatformAnalyticsRange,
  platformOverviewQueryOptions,
} from '@/api/platform-admin'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Main } from '@/components/layout/main'
import { PlatformOverviewSystem } from './overview-system'
import { PlatformPageHeading } from './page-heading'

const ranges: PlatformAnalyticsRange[] = ['24h', '7d', '30d']

export function PlatformServiceStatus() {
  const { t } = useTranslation()
  const [range, setRange] = useState<PlatformAnalyticsRange>('24h')
  const overview = useQuery({
    ...platformOverviewQueryOptions(range),
    retry: false,
  })

  return (
    <Main className='flex min-w-0 flex-1 flex-col'>
      <PlatformPageHeading
        title={t('platformAdmin.navigation.serviceStatus')}
        actions={
          <Select
            value={range}
            onValueChange={(value) => setRange(value as PlatformAnalyticsRange)}
          >
            <SelectTrigger
              className='w-32'
              aria-label={t('platformAdmin.overview.timeRange')}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {ranges.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`platformAdmin.overview.ranges.${value}`)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        }
      />
      <PlatformOverviewSystem overview={overview.data} range={range} />
    </Main>
  )
}
