import * as React from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Bell, CircleAlert, Settings, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  changePassword,
  currentUserQueryOptions,
  updateCurrentUser,
} from '@/api/auth'
import { clearAuthSession } from '@/lib/auth-session'
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
import {
  TabsUnderline,
  type AnimatedTab,
} from '@/components/shadcn-space/tabs/tabs-05'
import { NotificationFeed } from '@/features/notifications'

export type SettingsTab = 'profile' | 'notifications' | 'settings'

type SettingsPageProps = {
  activeTab: SettingsTab
  onTabChange: (tab: SettingsTab) => void
}

function MutationError({ error }: { error: Error | null }) {
  if (!error) return null
  return (
    <Alert variant='destructive'>
      <CircleAlert />
      <AlertDescription>{error.message}</AlertDescription>
    </Alert>
  )
}

function ProfileForm() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const schema = React.useMemo(
    () =>
      z.object({
        displayName: z
          .string()
          .trim()
          .min(1, t('settings.profile.nameRequired'))
          .max(120),
      }),
    [t]
  )
  type FormValues = z.infer<typeof schema>
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { displayName: user.display_name },
  })
  const mutation = useMutation({
    mutationFn: ({ displayName }: FormValues) => updateCurrentUser(displayName),
    onSuccess: (updatedUser) => {
      queryClient.setQueryData(currentUserQueryOptions().queryKey, updatedUser)
      form.reset({ displayName: updatedUser.display_name })
    },
  })

  return (
    <form
      className='max-w-xl'
      onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      noValidate
    >
      <FieldGroup>
        <MutationError
          error={mutation.error instanceof Error ? mutation.error : null}
        />
        <Field data-invalid={Boolean(form.formState.errors.displayName)}>
          <FieldLabel htmlFor='profile-display-name'>
            {t('settings.profile.displayName')}
          </FieldLabel>
          <Input
            id='profile-display-name'
            autoComplete='name'
            disabled={mutation.isPending}
            aria-invalid={Boolean(form.formState.errors.displayName)}
            {...form.register('displayName')}
          />
          <FieldError errors={[form.formState.errors.displayName]} />
        </Field>
        <Field>
          <FieldLabel htmlFor='profile-email'>
            {t('settings.profile.email')}
          </FieldLabel>
          <Input
            id='profile-email'
            type='email'
            value={user.email}
            autoComplete='email'
            spellCheck={false}
            readOnly
          />
        </Field>
        <Button
          type='submit'
          className='w-fit'
          disabled={!form.formState.isDirty || mutation.isPending}
          aria-busy={mutation.isPending}
        >
          {mutation.isPending && <Spinner data-icon='inline-start' />}
          {t('common.save')}
        </Button>
      </FieldGroup>
    </form>
  )
}

function PasswordForm() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const schema = React.useMemo(
    () =>
      z
        .object({
          currentPassword: z
            .string()
            .min(1, t('settings.security.currentPasswordRequired')),
          newPassword: z.string().min(8, t('settings.security.passwordLength')),
          confirmPassword: z
            .string()
            .min(1, t('settings.security.confirmPasswordRequired')),
        })
        .refine((values) => values.newPassword === values.confirmPassword, {
          path: ['confirmPassword'],
          message: t('settings.security.passwordMismatch'),
        }),
    [t]
  )
  type FormValues = z.infer<typeof schema>
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      currentPassword: '',
      newPassword: '',
      confirmPassword: '',
    },
  })
  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      changePassword(values.currentPassword, values.newPassword),
    onSuccess: () => {
      clearAuthSession()
      queryClient.clear()
      void navigate({ to: '/login', replace: true })
    },
  })
  const passwordInputLabels = {
    showPasswordLabel: t('auth.login.showPassword'),
    hidePasswordLabel: t('auth.login.hidePassword'),
  }

  return (
    <form
      className='max-w-xl'
      onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
      noValidate
    >
      <FieldGroup>
        <MutationError
          error={mutation.error instanceof Error ? mutation.error : null}
        />
        <Field data-invalid={Boolean(form.formState.errors.currentPassword)}>
          <FieldLabel htmlFor='current-password'>
            {t('settings.security.currentPassword')}
          </FieldLabel>
          <PasswordInput
            id='current-password'
            autoComplete='current-password'
            disabled={mutation.isPending}
            aria-invalid={Boolean(form.formState.errors.currentPassword)}
            {...passwordInputLabels}
            {...form.register('currentPassword')}
          />
          <FieldError errors={[form.formState.errors.currentPassword]} />
        </Field>
        <Field data-invalid={Boolean(form.formState.errors.newPassword)}>
          <FieldLabel htmlFor='new-password'>
            {t('settings.security.newPassword')}
          </FieldLabel>
          <PasswordInput
            id='new-password'
            autoComplete='new-password'
            disabled={mutation.isPending}
            aria-invalid={Boolean(form.formState.errors.newPassword)}
            {...passwordInputLabels}
            {...form.register('newPassword')}
          />
          <FieldError errors={[form.formState.errors.newPassword]} />
        </Field>
        <Field data-invalid={Boolean(form.formState.errors.confirmPassword)}>
          <FieldLabel htmlFor='confirm-password'>
            {t('settings.security.confirmPassword')}
          </FieldLabel>
          <PasswordInput
            id='confirm-password'
            autoComplete='new-password'
            disabled={mutation.isPending}
            aria-invalid={Boolean(form.formState.errors.confirmPassword)}
            {...passwordInputLabels}
            {...form.register('confirmPassword')}
          />
          <FieldError errors={[form.formState.errors.confirmPassword]} />
        </Field>
        <Button
          type='submit'
          className='w-fit'
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
        >
          {mutation.isPending && <Spinner data-icon='inline-start' />}
          {t('common.save')}
        </Button>
      </FieldGroup>
    </form>
  )
}

export function SettingsPage({ activeTab, onTabChange }: SettingsPageProps) {
  const { t } = useTranslation()
  const tabs: AnimatedTab[] = [
    {
      id: 'profile',
      label: t('settings.tabs.profile'),
      icon: UserRound,
      content: <ProfileForm />,
    },
    {
      id: 'notifications',
      label: t('settings.tabs.notifications'),
      icon: Bell,
      content: <NotificationFeed />,
    },
    {
      id: 'settings',
      label: t('settings.tabs.settings'),
      icon: Settings,
      content: <PasswordForm />,
    },
  ]

  return (
    <>
      <h1 className='sr-only'>{t('settings.tabs.settings')}</h1>
      <TabsUnderline
        tabs={tabs}
        value={activeTab}
        onValueChange={(value) => onTabChange(value as SettingsTab)}
        panelClassName={activeTab === 'notifications' ? 'p-0' : undefined}
      />
    </>
  )
}
