import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Main } from '@/components/layout/main'

const metricKeys = [
  'overview.workspaces',
  'overview.agents',
  'overview.activeRuns',
  'overview.pendingApprovals',
] as const

export function Overview() {
  const { t } = useTranslation()

  return (
    <>
      <Main>
        <h1 className='mb-6 text-2xl font-semibold tracking-tight'>
          {t('overview.title')}
        </h1>
        <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-4'>
          {metricKeys.map((key) => (
            <Card key={key}>
              <CardHeader>
                <CardTitle className='text-sm font-medium'>{t(key)}</CardTitle>
              </CardHeader>
              <CardContent>
                <span className='text-2xl font-semibold tabular-nums'>—</span>
              </CardContent>
            </Card>
          ))}
        </div>
      </Main>
    </>
  )
}
