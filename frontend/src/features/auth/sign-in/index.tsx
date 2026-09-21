import { useSearch } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { AuthLayout } from '../auth-layout'
import { UserAuthForm } from './user-auth-form'

export function SignIn() {
  const { t } = useTranslation()
  const { redirect } = useSearch({ from: '/(auth)/login' })

  return (
    <AuthLayout>
      <Card className='auth-card gap-8 py-10 sm:py-12'>
        <CardHeader className='items-center px-8 text-center sm:px-10'>
          <CardTitle className='text-xl tracking-tight'>
            {t('auth.login.title')}
          </CardTitle>
        </CardHeader>
        <CardContent className='px-8 sm:px-10'>
          <UserAuthForm redirectTo={redirect} />
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
