import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery } from '@tanstack/react-query'
import { MailPlus, Send } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { mailConfigurationQueryOptions } from '@/api/mail'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'

export type UserInviteFormValues = {
  email: string
  displayName: string
  platformAdmin: boolean
}

export function UsersInviteDialog({
  open,
  onOpenChange,
  onSubmitValues,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  platformOnly?: boolean
  onSubmitValues?: (values: UserInviteFormValues) => Promise<void>
}) {
  const { t } = useTranslation()
  const mail = useQuery({ ...mailConfigurationQueryOptions(), enabled: open })
  const formSchema = z.object({
    email: z.email({ error: t('validation.invite_email_required') }),
    displayName: z.string().trim().max(120),
    platformAdmin: z.boolean(),
  })
  const form = useForm<UserInviteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: { email: '', displayName: '', platformAdmin: false },
  })
  const onSubmit = async (values: UserInviteFormValues) => {
    if (!onSubmitValues) return
    try {
      await onSubmitValues(values)
      form.reset()
      onOpenChange(false)
    } catch (error) {
      form.setError('root', {
        message:
          error instanceof Error ? error.message : t('users.error_inviting'),
      })
      toast.error(t('users.error_inviting'))
    }
  }
  const close = () => {
    form.reset()
    onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        if (!state && !form.formState.isSubmitting) close()
      }}
    >
      <DialogContent className='max-h-[90dvh] overflow-y-auto sm:max-w-md'>
        <DialogHeader className='text-start'>
          <DialogTitle className='flex items-center gap-2'>
            <MailPlus />
            {t('users.invite_title')}
          </DialogTitle>
          <DialogDescription className='sr-only'>
            {t('users.invite_title')}
          </DialogDescription>
        </DialogHeader>
        {mail.isError && (
          <Alert variant='destructive'>
            <AlertDescription>
              {mail.error.message}
              <Button variant='outline' onClick={() => void mail.refetch()}>
                {t('users.retry')}
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {mail.data && !mail.data.enabled && (
          <Alert>
            <AlertDescription>
              {t('mail.not_configured')}
              <Button
                asChild
                variant='link'
                className='h-auto justify-start p-0'
              >
                <a href='/admin/system/configuration'>{t('mail.configure')}</a>
              </Button>
            </AlertDescription>
          </Alert>
        )}
        <Form {...form}>
          <form
            id='user-invite-form'
            onSubmit={form.handleSubmit(onSubmit)}
            className='space-y-4'
          >
            {form.formState.errors.root && (
              <Alert variant='destructive'>
                <AlertDescription>
                  {form.formState.errors.root.message}
                </AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name='email'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('users.email')}</FormLabel>
                  <FormControl>
                    <Input
                      type='email'
                      placeholder={t('users.invite_email_placeholder')}
                      {...field}
                      disabled={form.formState.isSubmitting}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='displayName'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('users.name')}</FormLabel>
                  <FormControl>
                    <Input {...field} disabled={form.formState.isSubmitting} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name='platformAdmin'
              render={({ field }) => (
                <FormItem className='flex items-center justify-between gap-4 rounded-md border p-3'>
                  <FormLabel>{t('users.platform_admin')}</FormLabel>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      disabled={form.formState.isSubmitting}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>
        <DialogFooter>
          <Button
            variant='outline'
            onClick={close}
            disabled={form.formState.isSubmitting}
          >
            {t('common.cancel')}
          </Button>
          <Button
            type='submit'
            form='user-invite-form'
            disabled={form.formState.isSubmitting || !mail.data?.enabled}
          >
            {t('users.invite_btn')}
            <Send />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
