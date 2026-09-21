import { useNavigate, useRouter } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

type GeneralErrorProps = {
  minimal?: boolean
}

export function GeneralError({ minimal = false }: GeneralErrorProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { history } = useRouter()

  return (
    <main className='grid min-h-svh place-items-center p-6'>
      <div className='flex w-full max-w-md flex-col items-center gap-2 text-center'>
        {!minimal && (
          <h1 className='text-8xl leading-none font-bold tracking-tighter'>
            500
          </h1>
        )}
        <h2 className='text-lg font-medium'>{t('errors.general.title')}</h2>
        {!minimal && (
          <div className='mt-6 flex gap-3'>
            <Button variant='outline' onClick={() => history.go(-1)}>
              {t('errors.goBack')}
            </Button>
            <Button onClick={() => void navigate({ to: '/' })}>
              {t('errors.home')}
            </Button>
          </div>
        )}
      </div>
    </main>
  )
}
