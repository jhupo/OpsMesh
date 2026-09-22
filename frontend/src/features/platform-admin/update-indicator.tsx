import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { CircleArrowUp } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { releaseUpdateCheckQueryOptions } from '@/api/platform-admin'
import { Button } from '@/components/ui/button'

type UpdateIndicatorProps = {
  enabled: boolean
}

export function UpdateIndicator({ enabled }: UpdateIndicatorProps) {
  const { t } = useTranslation()
  const { data } = useQuery(releaseUpdateCheckQueryOptions(enabled))

  if (!data?.update_available) return null

  return (
    <Button variant='ghost' size='icon' className='rounded-full' asChild>
      <Link
        to='/admin/$section'
        params={{ section: 'system' }}
        aria-label={t('platformAdmin.updateAvailable')}
      >
        <CircleArrowUp aria-hidden='true' />
      </Link>
    </Button>
  )
}
