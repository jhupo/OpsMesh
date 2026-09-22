import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Eye, EyeOff, Loader2, LockKeyhole, Mail } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions, login } from '@/api/auth'
import {
  clearAuthSession,
  safeRedirectPath,
  setAuthSession,
} from '@/lib/auth-session'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { FieldGroup } from '@/components/ui/field'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@/components/ui/form'
import FloatingLabel from '@/components/shadcn-space/label/label-06'

interface UserAuthFormProps extends React.HTMLAttributes<HTMLFormElement> {
  redirectTo?: string
}

export function UserAuthForm({
  className,
  redirectTo,
  ...props
}: UserAuthFormProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const [showPassword, setShowPassword] = useState(false)
  const schema = z.object({
    identifier: z.string().trim().min(1, t('validation.email_required')),
    password: z.string().min(1, t('validation.password_required')),
  })
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { identifier: '', password: '' },
  })
  const mutation = useMutation({
    mutationFn: async (data: z.infer<typeof schema>) => {
      const result = await login(data)
      queryClient.clear()
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
      form.setError('root', { message: error.message })
    },
  })
  return (
    <Form {...form}>
      <form
        {...props}
        noValidate
        onSubmit={form.handleSubmit((data) => mutation.mutate(data))}
        className={cn('flex flex-col gap-6', className)}
      >
        <FieldGroup>
          <FormField
            control={form.control}
            name='identifier'
            render={({ field, fieldState }) => (
              <FormItem>
                <FormControl>
                  <FloatingLabel
                    {...field}
                    label={t('auth.email', { lng: 'en' })}
                    icon={Mail}
                    autoComplete='username'
                    autoCapitalize='none'
                    spellCheck={false}
                    aria-invalid={
                      fieldState.invalid || Boolean(form.formState.errors.root)
                    }
                    disabled={mutation.isPending}
                  />
                </FormControl>
                <FormMessage className='sr-only' />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name='password'
            render={({ field, fieldState }) => (
              <FormItem>
                <FormControl>
                  <FloatingLabel
                    {...field}
                    label={t('auth.password', { lng: 'en' })}
                    icon={LockKeyhole}
                    type={showPassword ? 'text' : 'password'}
                    autoComplete='current-password'
                    aria-invalid={
                      fieldState.invalid || Boolean(form.formState.errors.root)
                    }
                    disabled={mutation.isPending}
                    trailing={
                      <Button
                        type='button'
                        variant='ghost'
                        size='icon'
                        className='size-7 shrink-0'
                        disabled={mutation.isPending}
                        aria-label={t(
                          showPassword
                            ? 'common.hide_password'
                            : 'common.show_password'
                        )}
                        aria-pressed={showPassword}
                        onClick={() => setShowPassword(!showPassword)}
                      >
                        {showPassword ? <EyeOff /> : <Eye />}
                      </Button>
                    }
                  />
                </FormControl>
                <FormMessage className='sr-only' />
              </FormItem>
            )}
          />
        </FieldGroup>
        {form.formState.errors.root?.message && (
          <p role='alert' className='text-sm text-destructive'>
            {form.formState.errors.root.message}
          </p>
        )}
        <Button type='submit' disabled={mutation.isPending}>
          {mutation.isPending && (
            <Loader2 className='animate-spin motion-reduce:animate-none' />
          )}
          {t('auth.sign_in_btn')}
        </Button>
      </form>
    </Form>
  )
}
