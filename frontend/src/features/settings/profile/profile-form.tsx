import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from '@tanstack/react-query'
import { Check, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions, updateProfile } from '@/api/auth'
import { getDisplayNameInitials } from '@/lib/utils'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
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
import { PasswordForm } from './password-form'

export function ProfileForm() {
  const { t } = useTranslation()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const queryClient = useQueryClient()
  const schema = z.object({
    name: z.string().trim().min(1, t('validation.name_required')).max(120),
  })
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: user.display_name },
  })
  const save = useMutation({
    mutationFn: (data: z.infer<typeof schema>) => updateProfile(data.name),
    onSuccess: (updated) => {
      queryClient.setQueryData(currentUserQueryOptions().queryKey, updated)
      form.reset({ name: updated.display_name })
    },
    onError: (error) => {
      form.setError('root', { message: error.message })
    },
  })
  return (
    <div className='flex flex-col gap-8'>
      <Form {...form}>
        <form
          className='flex flex-col gap-6'
          onSubmit={form.handleSubmit((data) => save.mutate(data))}
        >
          <div className='flex items-center gap-4'>
            <Avatar className='size-14'>
              <AvatarFallback>
                {getDisplayNameInitials(user.display_name)}
              </AvatarFallback>
            </Avatar>
            <div className='min-w-0'>
              <p className='truncate font-medium'>{user.display_name}</p>
              <p className='truncate text-sm text-muted-foreground'>
                {user.email}
              </p>
            </div>
          </div>
          <FieldGroup className='grid gap-6 md:grid-cols-2'>
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
                value={user.email}
                readOnly
                autoComplete='email'
              />
            </Field>
            <Field className='md:col-span-2' data-disabled>
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
          {form.formState.errors.root && (
            <p role='alert' className='text-sm text-destructive'>
              {form.formState.errors.root.message}
            </p>
          )}
          <div className='flex justify-end'>
            <Button
              type='submit'
              disabled={save.isPending || !form.formState.isDirty}
            >
              {save.isPending ? (
                <Loader2 className='animate-spin' />
              ) : save.isSuccess && !form.formState.isDirty ? (
                <Check />
              ) : null}
              {t('settings_form.update_profile')}
            </Button>
          </div>
        </form>
      </Form>
      <PasswordForm />
    </div>
  )
}
