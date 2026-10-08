import { useEffect, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { ApiError } from '@/api/errors'
import { acceptUserInvitation } from '@/api/mail'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'
import { AuthLayout } from '../auth-layout'

export function AcceptInvitation() {
  const { t } = useTranslation()
  const [token, setToken] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? ''
  )
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [pending, setPending] = useState(false)
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search
    )
  }, [])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (password !== confirmation) {
      setError(t('validation.passwords_not_match'))
      return
    }
    setPending(true)
    setError('')
    try {
      await acceptUserInvitation({
        token,
        password,
        display_name: displayName,
        username,
      })
      setToken('')
      setPassword('')
      setConfirmation('')
      setComplete(true)
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === 'invalid_invitation'
          ? t('users.invalid_invitation')
          : caught instanceof Error
            ? caught.message
            : t('common.error')
      )
    } finally {
      setPending(false)
    }
  }
  return (
    <AuthLayout>
      <meta name='referrer' content='no-referrer' />
      <Card>
        <CardHeader>
          <CardTitle>
            {t(
              complete ? 'users.invitation_accepted' : 'users.accept_invitation'
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {complete ? (
            <Button asChild className='w-full'>
              <Link to='/sign-in'>{t('users.go_sign_in')}</Link>
            </Button>
          ) : !token ? (
            <Alert variant='destructive'>
              <AlertDescription>
                {t('users.invalid_invitation')}
              </AlertDescription>
            </Alert>
          ) : (
            <form onSubmit={submit} className='space-y-4'>
              {error && (
                <Alert variant='destructive'>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Field>
                <FieldLabel htmlFor='invite-display-name'>
                  {t('users.name')}
                </FieldLabel>
                <Input
                  id='invite-display-name'
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  required
                  maxLength={120}
                  autoComplete='name'
                  disabled={pending}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor='invite-username'>
                  {t('users.username')}
                </FieldLabel>
                <Input
                  id='invite-username'
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  maxLength={80}
                  autoComplete='username'
                  disabled={pending}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor='invite-password'>
                  {t('users.password')}
                </FieldLabel>
                <PasswordInput
                  id='invite-password'
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  minLength={8}
                  autoComplete='new-password'
                  disabled={pending}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor='invite-confirm-password'>
                  {t('users.confirm_password')}
                </FieldLabel>
                <PasswordInput
                  id='invite-confirm-password'
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  required
                  minLength={8}
                  autoComplete='new-password'
                  disabled={pending}
                />
              </Field>
              <Button type='submit' className='w-full' disabled={pending}>
                {t('users.activate_account')}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
