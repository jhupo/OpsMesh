import { useRef, useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Camera, Check, Loader2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  currentUserQueryOptions,
  updateProfile,
  type ProfileUpdate,
} from '@/api/auth'
import { clearAuthSession } from '@/lib/auth-session'
import { Button } from '@/components/ui/button'
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from '@/components/ui/field'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { PasswordInput } from '@/components/password-input'
import { UserAvatar } from '@/components/user-avatar'

export function ProfileForm() {
  const { t } = useTranslation()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const fileInput = useRef<HTMLInputElement>(null)
  const [avatar, setAvatar] = useState<string | null | undefined>()
  const [readingAvatar, setReadingAvatar] = useState(false)
  const schema = z
    .object({
      name: z.string().trim().min(1, t('validation.name_required')).max(120),
      current: z.string().max(4096),
      next: z.string().max(4096),
      confirmation: z.string().max(4096),
    })
    .superRefine((data, ctx) => {
      if (!data.current && !data.next && !data.confirmation) return
      if (!data.current)
        ctx.addIssue({
          code: 'custom',
          path: ['current'],
          message: t('validation.password_required'),
        })
      if (data.next.length < 8)
        ctx.addIssue({
          code: 'custom',
          path: ['next'],
          message: t('validation.password_min_length'),
        })
      if (data.next !== data.confirmation)
        ctx.addIssue({
          code: 'custom',
          path: ['confirmation'],
          message: t('opsmesh.passwordMismatch'),
        })
    })
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: user.display_name,
      current: '',
      next: '',
      confirmation: '',
    },
  })
  const dirty = form.formState.isDirty || avatar !== undefined
  // The combined save requires the new account contract. Never send it to an
  // older server that would silently ignore the avatar and password fields.
  const canSave =
    user.avatar_version === null || typeof user.avatar_version === 'string'
  const save = useMutation({
    mutationFn: (data: z.infer<typeof schema>) => {
      const update: ProfileUpdate = { display_name: data.name }
      if (avatar !== undefined)
        update.avatar_base64 = avatar?.split(',')[1] ?? null
      if (data.next)
        update.password = {
          current_password: data.current,
          new_password: data.next,
        }
      return updateProfile(update)
    },
    onSuccess: async (updated, submitted) => {
      if (submitted.next) {
        clearAuthSession()
        await navigate({
          to: '/sign-in',
          search: { redirect: '/' },
          replace: true,
        })
        queryClient.clear()
        return
      }
      queryClient.setQueryData(currentUserQueryOptions().queryKey, updated)
      setAvatar(undefined)
      form.reset({
        name: updated.display_name,
        current: '',
        next: '',
        confirmation: '',
      })
    },
    onError: (error) => form.setError('root', { message: error.message }),
  })
  async function selectAvatar(file?: File) {
    if (!file) return
    form.clearErrors('root')
    if (
      !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) ||
      file.size > 2 * 1024 * 1024
    ) {
      form.setError('root', { message: t('opsmesh.avatarInvalid') })
      return
    }
    setReadingAvatar(true)
    try {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = reject
        reader.readAsDataURL(file)
      })
      setAvatar(encoded)
    } catch {
      form.setError('root', { message: t('opsmesh.avatarInvalid') })
    } finally {
      setReadingAvatar(false)
    }
  }
  return (
    <Form {...form}>
      <form
        className='flex flex-col gap-6'
        onSubmit={form.handleSubmit((data) => save.mutate(data))}
      >
        <div className='flex min-w-0 items-center gap-4 border-b pb-6'>
          <Input
            ref={fileInput}
            type='file'
            accept='image/png,image/jpeg,image/webp'
            className='hidden'
            aria-label={t('opsmesh.changeAvatar')}
            disabled={save.isPending || readingAvatar}
            onChange={(event) => {
              void selectAvatar(event.target.files?.[0])
              event.target.value = ''
            }}
          />
          <Button
            type='button'
            variant='ghost'
            className='group relative size-16 shrink-0 rounded-full p-0'
            aria-label={t('opsmesh.changeAvatar')}
            disabled={save.isPending || readingAvatar}
            onClick={() => fileInput.current?.click()}
          >
            <UserAvatar
              user={user}
              preview={avatar}
              className='size-16 text-lg'
            />
            <span className='absolute -right-1 bottom-0 flex size-6 items-center justify-center rounded-full border bg-background text-foreground'>
              <Camera className='size-3.5' aria-hidden />
            </span>
          </Button>
          <div className='min-w-0 flex-1'>
            <p className='truncate font-medium'>{user.display_name}</p>
            <p className='truncate text-sm text-muted-foreground'>
              {user.email}
            </p>
          </div>
          {(avatar || (avatar === undefined && user.avatar_version)) && (
            <Button
              type='button'
              variant='ghost'
              size='icon'
              aria-label={t('opsmesh.removeAvatar')}
              disabled={save.isPending}
              onClick={() => setAvatar(null)}
            >
              <X aria-hidden />
            </Button>
          )}
        </div>
        <div className='grid min-w-0 gap-8 @4xl/content:grid-cols-2'>
          <FieldGroup className='gap-5'>
            <FormField
              control={form.control}
              name='name'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('settings_form.name')}</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      autoComplete='name'
                      disabled={save.isPending}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Field>
              <FieldLabel htmlFor='profile-email'>
                {t('settings_form.email')}
              </FieldLabel>
              <Input
                id='profile-email'
                name='email'
                type='email'
                spellCheck={false}
                value={user.email}
                readOnly
                autoComplete='email'
              />
            </Field>
            <Field data-disabled>
              <FieldLabel htmlFor='profile-bio'>
                {t('settings_form.bio')}
              </FieldLabel>
              {/* Reserved until the profile API exposes biography storage. */}
              <Textarea
                id='profile-bio'
                value=''
                disabled
                className='min-h-24 resize-none'
              />
            </Field>
          </FieldGroup>
          <FieldSet className='min-w-0 border-t pt-6 @4xl/content:border-s @4xl/content:border-t-0 @4xl/content:ps-8 @4xl/content:pt-0'>
            <FieldLegend className='text-sm'>{t('auth.password')}</FieldLegend>
            <FieldGroup className='gap-5'>
              {(['current', 'next', 'confirmation'] as const).map((name) => (
                <FormField
                  key={name}
                  control={form.control}
                  name={name}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t('opsmesh.password.' + name)}</FormLabel>
                      <FormControl>
                        <PasswordInput
                          {...field}
                          autoComplete={
                            name === 'current'
                              ? 'current-password'
                              : 'new-password'
                          }
                          disabled={save.isPending}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ))}
            </FieldGroup>
          </FieldSet>
        </div>
        {form.formState.errors.root && (
          <p role='alert' className='text-sm text-destructive'>
            {form.formState.errors.root.message}
          </p>
        )}
        <div className='flex justify-end border-t pt-5'>
          <Button
            type='submit'
            disabled={!canSave || save.isPending || readingAvatar || !dirty}
          >
            {save.isPending ? (
              <Loader2 className='animate-spin' />
            ) : save.isSuccess && !dirty ? (
              <Check />
            ) : null}
            {t('common.save')}
          </Button>
        </div>
      </form>
    </Form>
  )
}
