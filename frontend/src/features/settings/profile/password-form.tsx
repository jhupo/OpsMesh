import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { changePassword } from '@/api/auth'
import { clearAuthSession } from '@/lib/auth-session'
import { Button } from '@/components/ui/button'
import { FieldGroup, FieldLegend, FieldSet } from '@/components/ui/field'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Separator } from '@/components/ui/separator'
import { PasswordInput } from '@/components/password-input'

export function PasswordForm() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const schema = z
    .object({
      current: z.string().min(1, t('validation.password_required')),
      next: z.string().min(8, t('validation.password_min_length')),
      confirmation: z.string().min(1, t('validation.password_required')),
    })
    .refine((data) => data.next === data.confirmation, {
      path: ['confirmation'],
      message: t('opsmesh.passwordMismatch'),
    })
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { current: '', next: '', confirmation: '' },
  })
  const change = useMutation({
    mutationFn: (data: z.infer<typeof schema>) =>
      changePassword(data.current, data.next),
    onSuccess: () => {
      form.reset()
      clearAuthSession()
      queryClient.clear()
      void navigate({
        to: '/sign-in',
        search: { redirect: '/' },
        replace: true,
      })
    },
    onError: (error) => {
      form.setError('root', { message: error.message })
    },
  })
  return (
    <>
      <Separator />
      <Form {...form}>
        <form
          className='flex flex-col gap-6'
          onSubmit={form.handleSubmit((data) => change.mutate(data))}
        >
          <FieldSet>
            <FieldLegend>{t('auth.password')}</FieldLegend>
            <FieldGroup className='grid gap-6 md:grid-cols-2'>
              {(['current', 'next', 'confirmation'] as const).map((name) => (
                <FormField
                  key={name}
                  control={form.control}
                  name={name}
                  render={({ field }) => (
                    <FormItem
                      className={
                        name === 'current' ? 'md:col-span-2' : undefined
                      }
                    >
                      <FormLabel>{t('opsmesh.password.' + name)}</FormLabel>
                      <FormControl>
                        <PasswordInput
                          {...field}
                          autoComplete={
                            name === 'current'
                              ? 'current-password'
                              : 'new-password'
                          }
                          disabled={change.isPending}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ))}
            </FieldGroup>
          </FieldSet>
          {form.formState.errors.root && (
            <p role='alert' className='text-sm text-destructive'>
              {form.formState.errors.root.message}
            </p>
          )}
          <div className='flex justify-end'>
            <Button
              type='submit'
              disabled={change.isPending || !form.formState.isDirty}
            >
              {change.isPending && <Loader2 className='animate-spin' />}
              {t('opsmesh.changePassword')}
            </Button>
          </div>
        </form>
      </Form>
    </>
  )
}
