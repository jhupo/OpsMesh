import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

export function MaintenanceError() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <main className='grid min-h-svh place-items-center p-6'>
      <div className='flex w-full max-w-md flex-col items-center gap-2 text-center'>
        <h1 className='text-8xl leading-none font-bold tracking-tighter'>
          503
        </h1>
        <h2 className='text-lg font-medium'>{t('errors.maintenance.title')}</h2>
        <Button className='mt-6' onClick={() => void navigate({ to: '/' })}>
          {t('errors.home')}
        </Button>
      </div>
    </main>
  )
}
