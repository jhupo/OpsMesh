import { useMemo } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CircleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions, login } from '@/api/auth'
import { ApiError } from '@/api/errors'
import {
  clearAuthSession,
  safeRedirectPath,
  setAuthSession,
} from '@/lib/auth-session'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { PasswordInput } from '@/components/password-input'

type UserAuthFormProps = {
  redirectTo?: string
}

export function UserAuthForm({ redirectTo }: UserAuthFormProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const schema = useMemo(
    () =>
      z.object({
        identifier: z
          .string()
          .trim()
          .min(1, t('auth.login.identifierRequired')),
        password: z.string().min(1, t('auth.login.passwordRequired')),
      }),
    [t]
  )
  type FormValues = z.infer<typeof schema>

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { identifier: '', password: '' },
  })

  const loginMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const result = await login(values)
      setAuthSession({ token: result.token, tokenId: result.id })
      try {
        await queryClient.fetchQuery(currentUserQueryOptions())
      } catch (error) {
        clearAuthSession()
        throw error
      }
    },
    onSuccess: () => {
      void navigate({ to: safeRedirectPath(redirectTo), replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        const failure = {
          type: 'server',
          message: t('auth.login.invalidCredentials'),
        }
        form.setError('identifier', failure)
        form.setError('password', failure)
        form.setFocus('identifier')
      }
    },
  })

  const errorMessage = loginMutation.error
    ? loginMutation.error instanceof ApiError &&
      loginMutation.error.status === 401
      ? null
      : t('auth.login.failed')
    : null

  return (
    <form
      onSubmit={form.handleSubmit((values) => loginMutation.mutate(values))}
      onChange={() => {
        if (loginMutation.isError) {
          loginMutation.reset()
          form.clearErrors()
        }
      }}
      noValidate
    >
      <FieldGroup className='gap-6'>
        {errorMessage && (
          <Alert variant='destructive'>
            <CircleAlert />
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        )}
        <Field
          className='auth-field'
          data-disabled={loginMutation.isPending}
          data-invalid={Boolean(form.formState.errors.identifier)}
        >
          <FieldLabel htmlFor='identifier'>
            {t('auth.login.identifier')}
          </FieldLabel>
          <Input
            id='identifier'
            placeholder=' '
            autoComplete='username'
            autoCapitalize='none'
            spellCheck={false}
            aria-invalid={Boolean(form.formState.errors.identifier)}
            aria-describedby={
              form.formState.errors.identifier ? 'identifier-error' : undefined
            }
            disabled={loginMutation.isPending}
            {...form.register('identifier')}
          />
          <FieldError
            id='identifier-error'
            className='sr-only'
            errors={[form.formState.errors.identifier]}
          />
        </Field>
        <Field
          className='auth-field'
          data-disabled={loginMutation.isPending}
          data-invalid={Boolean(form.formState.errors.password)}
        >
          <FieldLabel htmlFor='password'>{t('auth.login.password')}</FieldLabel>
          <PasswordInput
            id='password'
            placeholder=' '
            autoComplete='current-password'
            aria-invalid={Boolean(form.formState.errors.password)}
            aria-describedby={
              form.formState.errors.password ? 'password-error' : undefined
            }
            disabled={loginMutation.isPending}
            showPasswordLabel={t('auth.login.showPassword')}
            hidePasswordLabel={t('auth.login.hidePassword')}
            {...form.register('password')}
          />
          <FieldError
            id='password-error'
            className='sr-only'
            errors={[form.formState.errors.password]}
          />
        </Field>
        <Button
          type='submit'
          size='lg'
          disabled={loginMutation.isPending}
          aria-busy={loginMutation.isPending}
        >
          {loginMutation.isPending && <Spinner data-icon='inline-start' />}
          {t('auth.login.submit')}
        </Button>
      </FieldGroup>
    </form>
  )
}
