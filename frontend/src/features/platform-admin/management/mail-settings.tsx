import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  mailConfigurationQueryOptions,
  saveMailConfiguration,
  sendTestEmail,
  type MailConfiguration,
} from '@/api/mail'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { PasswordInput } from '@/components/password-input'
import { PlatformSectionHeading } from '../page-heading'

export function PlatformMailSettings() {
  const { t } = useTranslation()
  const query = useQuery(mailConfigurationQueryOptions())
  if (query.isPending) return <Skeleton className='h-96 w-full' />
  if (query.isError)
    return (
      <Alert variant='destructive'>
        <AlertDescription>
          {query.error.message}
          <Button variant='outline' onClick={() => void query.refetch()}>
            {t('users.retry')}
          </Button>
        </AlertDescription>
      </Alert>
    )
  return (
    <MailSettingsForm key={query.dataUpdatedAt} configuration={query.data} />
  )
}

function MailSettingsForm({
  configuration,
}: {
  configuration: MailConfiguration
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(configuration)
  const [password, setPassword] = useState('')
  const [clearPassword, setClearPassword] = useState(false)
  const [recipient, setRecipient] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const errorRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (error) {
      errorRef.current?.scrollIntoView({ block: 'nearest' })
      errorRef.current?.focus({ preventScroll: true })
    }
  }, [error])
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(configuration) ||
    Boolean(password) ||
    clearPassword
  const set = <Key extends keyof MailConfiguration>(
    key: Key,
    value: MailConfiguration[Key]
  ) => setDraft((current) => ({ ...current, [key]: value }))
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setPending(true)
    setError('')
    try {
      const { password_configured: _configured, ...values } = draft
      const saved = await saveMailConfiguration({
        ...values,
        password: password || undefined,
        clear_password: clearPassword,
      })
      setPassword('')
      queryClient.setQueryData(mailConfigurationQueryOptions().queryKey, saved)
      toast.success(t('mail.saved'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('common.error'))
    } finally {
      setPending(false)
    }
  }
  const test = async (event: React.FormEvent) => {
    event.preventDefault()
    setPending(true)
    setError('')
    try {
      await sendTestEmail(recipient)
      toast.success(t('mail.test_sent'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('common.error'))
    } finally {
      setPending(false)
    }
  }

  return (
    <section className='min-w-0 space-y-6 p-1'>
      <PlatformSectionHeading title={t('mail.title')} />
      {error && (
        <Alert ref={errorRef} tabIndex={-1} variant='destructive'>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <form onSubmit={save} className='space-y-5'>
        <Field
          orientation='horizontal'
          className='justify-between rounded-md border p-3'
        >
          <FieldLabel htmlFor='mail-enabled'>{t('mail.enabled')}</FieldLabel>
          <Switch
            id='mail-enabled'
            checked={draft.enabled}
            onCheckedChange={(value) => set('enabled', value)}
            disabled={pending}
          />
        </Field>
        <FieldGroup className='grid gap-5 sm:grid-cols-2'>
          <Field>
            <FieldLabel htmlFor='smtp-host'>{t('mail.host')}</FieldLabel>
            <Input
              id='smtp-host'
              value={draft.host}
              onChange={(event) => set('host', event.target.value)}
              required={draft.enabled}
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-port'>{t('mail.port')}</FieldLabel>
            <Input
              id='smtp-port'
              type='number'
              min={1}
              max={65535}
              value={draft.port}
              onChange={(event) => set('port', Number(event.target.value))}
              required
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-security'>
              {t('mail.security')}
            </FieldLabel>
            <Select
              value={draft.security}
              onValueChange={(value) =>
                set('security', value as MailConfiguration['security'])
              }
              disabled={pending}
            >
              <SelectTrigger id='smtp-security' className='w-full'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='starttls'>STARTTLS</SelectItem>
                <SelectItem value='tls'>TLS / SSL</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-username'>
              {t('mail.username')}
            </FieldLabel>
            <Input
              id='smtp-username'
              value={draft.username}
              onChange={(event) => set('username', event.target.value)}
              autoComplete='off'
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-password'>
              {t('mail.password')}
            </FieldLabel>
            <PasswordInput
              id='smtp-password'
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t(
                configuration.password_configured
                  ? 'mail.password_keep'
                  : 'mail.password_empty'
              )}
              autoComplete='new-password'
              disabled={pending || clearPassword}
            />
          </Field>
          <Field
            orientation='horizontal'
            className='items-center justify-between'
          >
            <FieldLabel htmlFor='smtp-clear-password'>
              {t('mail.clear_password')}
            </FieldLabel>
            <Switch
              id='smtp-clear-password'
              checked={clearPassword}
              onCheckedChange={(value) => {
                setClearPassword(value)
                if (value) setPassword('')
              }}
              disabled={pending || !configuration.password_configured}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-from-email'>
              {t('mail.from_email')}
            </FieldLabel>
            <Input
              id='smtp-from-email'
              type='email'
              value={draft.from_email}
              onChange={(event) => set('from_email', event.target.value)}
              required={draft.enabled}
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-from-name'>
              {t('mail.from_name')}
            </FieldLabel>
            <Input
              id='smtp-from-name'
              value={draft.from_name}
              onChange={(event) => set('from_name', event.target.value)}
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-public-url'>
              {t('mail.public_url')}
            </FieldLabel>
            <Input
              id='smtp-public-url'
              type='url'
              value={draft.public_base_url}
              onChange={(event) => set('public_base_url', event.target.value)}
              required={draft.enabled}
              disabled={pending}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='smtp-expiry'>{t('mail.expiry')}</FieldLabel>
            <Input
              id='smtp-expiry'
              type='number'
              min={1}
              max={168}
              value={draft.invitation_expiry_hours}
              onChange={(event) =>
                set('invitation_expiry_hours', Number(event.target.value))
              }
              required
              disabled={pending}
            />
          </Field>
        </FieldGroup>
        <Button type='submit' disabled={pending || !dirty}>
          {t('common.save')}
        </Button>
      </form>
      <form onSubmit={test} className='space-y-4 border-t pt-5'>
        <Field>
          <FieldLabel htmlFor='smtp-test-recipient'>
            {t('mail.test_recipient')}
          </FieldLabel>
          <Input
            id='smtp-test-recipient'
            type='email'
            required
            value={recipient}
            onChange={(event) => setRecipient(event.target.value)}
            disabled={pending}
          />
        </Field>
        <Button
          type='submit'
          variant='outline'
          disabled={pending || dirty || !configuration.enabled}
        >
          {t('mail.test_send')}
        </Button>
      </form>
    </section>
  )
}
