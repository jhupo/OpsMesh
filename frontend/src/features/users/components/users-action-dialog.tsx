'use client'

import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Check, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
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
import { PasswordInput } from '@/components/password-input'
import { SelectDropdown } from '@/components/select-dropdown'
import { type User } from '../data/schema'
import { useUsersData } from './use-users-data'

export type UserActionFormValues = {
  firstName: string
  lastName: string
  username: string
  phoneNumber: string
  email: string
  password: string
  role: string
  displayName?: string
  platformAdmin?: boolean
}

export type UserActionResult = { initialPassword: string } | void

type UserActionDialogProps = {
  currentRow?: User
  open: boolean
  onOpenChange: (open: boolean) => void
  platformOnly?: boolean
  phoneEnabled?: boolean
  onSubmitValues?: (
    values: UserActionFormValues,
    currentRow?: User
  ) => Promise<UserActionResult>
}

export function UsersActionDialog({
  currentRow,
  open,
  onOpenChange,
  platformOnly = false,
  phoneEnabled = true,
  onSubmitValues,
}: UserActionDialogProps) {
  const isEdit = !!currentRow
  const [createdUser, setCreatedUser] = useState<{
    email: string
    password: string
  } | null>(null)
  const { t } = useTranslation()
  const { roles: roleOptions } = useUsersData({ platformOnly })

  const formSchema = z
    .object({
      firstName: platformOnly
        ? z.string()
        : z.string().min(1, t('validation.first_name_required')),
      lastName: platformOnly
        ? z.string()
        : z.string().min(1, t('validation.last_name_required')),
      username:
        platformOnly && !currentRow?.loginName
          ? z.string().max(80)
          : z.string().min(1, t('validation.username_required')).max(80),
      phoneNumber:
        phoneEnabled && !platformOnly
          ? z.string().min(1, t('validation.phone_required'))
          : z.string(),
      email: z.email({
        error: (iss) =>
          iss.input === '' ? t('validation.email_field_required') : undefined,
      }),
      password: z.string(),
      role: platformOnly
        ? z.string()
        : z.string().min(1, t('validation.role_required')),
      displayName: platformOnly
        ? z.string().trim().min(1, t('users.name_required')).max(120)
        : z.string(),
      platformAdmin: z.boolean(),
      confirmPassword: z.string(),
      isEdit: z.boolean(),
    })
    .refine(
      (data) => {
        if ((data.isEdit || platformOnly) && !data.password) return true
        return data.password.length > 0
      },
      {
        message: t('validation.password_field_required'),
        path: ['password'],
      }
    )
    .refine(
      ({ isEdit, password }) => {
        if ((isEdit || platformOnly) && !password) return true
        return password.length >= 8
      },
      {
        message: t('validation.password_min_8'),
        path: ['password'],
      }
    )
    .refine(
      ({ isEdit, password }) => {
        if (isEdit && !password) return true
        return platformOnly || /[a-z]/.test(password)
      },
      {
        message: t('validation.password_lowercase'),
        path: ['password'],
      }
    )
    .refine(
      ({ isEdit, password }) => {
        if (isEdit && !password) return true
        return platformOnly || /\d/.test(password)
      },
      {
        message: t('validation.password_number'),
        path: ['password'],
      }
    )
    .refine(
      ({ isEdit, password, confirmPassword }) => {
        if (isEdit && !password) return true
        return platformOnly || password === confirmPassword
      },
      {
        message: t('validation.passwords_not_match'),
        path: ['confirmPassword'],
      }
    )
  type UserForm = z.infer<typeof formSchema>

  const form = useForm<UserForm>({
    resolver: zodResolver(formSchema),
    defaultValues: isEdit
      ? {
          ...currentRow,
          username: platformOnly
            ? (currentRow.loginName ?? '')
            : currentRow.username,
          displayName: currentRow.displayName ?? '',
          platformAdmin: currentRow.platformAdmin ?? false,
          password: '',
          confirmPassword: '',
          isEdit,
        }
      : {
          firstName: '',
          lastName: '',
          displayName: '',
          platformAdmin: false,
          username: '',
          email: '',
          role: '',
          phoneNumber: '',
          password: '',
          confirmPassword: '',
          isEdit,
        },
  })

  const onSubmit = async (values: UserForm) => {
    try {
      if (!onSubmitValues) return
      const result = await onSubmitValues(values, currentRow)
      form.reset()
      if (result?.initialPassword) {
        setCreatedUser({
          email: values.email,
          password: result.initialPassword,
        })
      } else {
        onOpenChange(false)
      }
    } catch (error) {
      // Keep the dialog open when the API rejects the request.
      toast.error(
        error instanceof Error ? error.message : t('users.save_error')
      )
    }
  }

  const isPasswordTouched = !!form.formState.dirtyFields.password
  if (createdUser) {
    return (
      <Dialog
        open={open}
        onOpenChange={(state) => {
          if (!state) setCreatedUser(null)
          onOpenChange(state)
        }}
      >
        <DialogContent className='sm:max-w-lg'>
          <DialogHeader className='text-start'>
            <DialogTitle className='flex items-center gap-2'>
              <Check className='size-5' />
              {t('users.created_success')}
            </DialogTitle>
            <DialogDescription className='sr-only'>
              {t('users.initial_password')}
            </DialogDescription>
          </DialogHeader>
          <p className='text-sm break-all text-muted-foreground'>
            {createdUser.email}
          </p>
          <div className='space-y-2'>
            <label htmlFor='initial-password' className='text-sm font-medium'>
              {t('users.initial_password')}
            </label>
            <div className='flex gap-2'>
              <PasswordInput
                id='initial-password'
                readOnly
                value={createdUser.password}
                className='min-w-0 flex-1'
              />
              <Button
                variant='outline'
                aria-label={t('users.copy_password')}
                onClick={() => {
                  void navigator.clipboard
                    .writeText(createdUser.password)
                    .then(() => toast.success(t('users.password_copied')))
                    .catch(() => toast.error(t('users.copy_failed')))
                }}
              >
                <Copy />
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button
              onClick={() => {
                setCreatedUser(null)
                onOpenChange(false)
              }}
            >
              {t('common.close')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        if (form.formState.isSubmitting) return
        form.reset()
        onOpenChange(state)
      }}
    >
      <DialogContent className='max-h-[90dvh] overflow-y-auto sm:max-w-lg'>
        <DialogHeader className='text-start'>
          <DialogTitle>
            {isEdit ? t('users.edit_user') : t('users.add_new_user')}
          </DialogTitle>
          <DialogDescription className='sr-only'>
            {isEdit ? t('users.edit_user') : t('users.add_new_user')}
          </DialogDescription>
        </DialogHeader>
        <div className='min-w-0 py-1'>
          <Form {...form}>
            <form
              id='user-form'
              onSubmit={form.handleSubmit(onSubmit)}
              className='space-y-4 px-0.5'
            >
              {platformOnly && (
                <>
                  <FormField
                    control={form.control}
                    name='displayName'
                    render={({ field }) => (
                      <FormItem className='space-y-2'>
                        <FormLabel>{t('users.name')}</FormLabel>
                        <FormControl>
                          <Input
                            className='w-full'
                            autoComplete='off'
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}
              {!platformOnly && (
                <FormField
                  control={form.control}
                  name='firstName'
                  render={({ field }) => (
                    <FormItem className='space-y-2'>
                      <FormLabel>{t('users.first_name')}</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='John'
                          className='w-full'
                          autoComplete='off'
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {!platformOnly && (
                <FormField
                  control={form.control}
                  name='lastName'
                  render={({ field }) => (
                    <FormItem className='space-y-2'>
                      <FormLabel>{t('users.last_name')}</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='Doe'
                          className='w-full'
                          autoComplete='off'
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              <FormField
                control={form.control}
                name='username'
                render={({ field }) => (
                  <FormItem className='space-y-2'>
                    <FormLabel>{t('users.username')}</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='john_doe'
                        className='w-full'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='email'
                render={({ field }) => (
                  <FormItem className='space-y-2'>
                    <FormLabel>{t('users.email')}</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='john.doe@gmail.com'
                        className='w-full'
                        disabled={isEdit && Boolean(onSubmitValues)}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {phoneEnabled && !platformOnly && (
                <FormField
                  control={form.control}
                  name='phoneNumber'
                  render={({ field }) => (
                    <FormItem className='space-y-2'>
                      <FormLabel>{t('users.phone_number')}</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='+123456789'
                          className='w-full'
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {!platformOnly && (
                <FormField
                  control={form.control}
                  name='role'
                  render={({ field }) => (
                    <FormItem className='space-y-2'>
                      <FormLabel>{t('users.role')}</FormLabel>
                      <SelectDropdown
                        defaultValue={field.value}
                        onValueChange={field.onChange}
                        placeholder={t('users.select_role')}
                        className='w-full'
                        items={roleOptions.map(({ label, value }) => ({
                          label,
                          value,
                        }))}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {(!platformOnly || !isEdit) && (
                <>
                  <FormField
                    control={form.control}
                    name='password'
                    render={({ field }) => (
                      <FormItem className='space-y-2'>
                        <FormLabel>{t('users.password')}</FormLabel>
                        <FormControl>
                          <PasswordInput
                            placeholder={
                              platformOnly
                                ? t('users.password_auto')
                                : undefined
                            }
                            className='w-full'
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {!platformOnly && (
                    <FormField
                      control={form.control}
                      name='confirmPassword'
                      render={({ field }) => (
                        <FormItem className='space-y-2'>
                          <FormLabel>{t('users.confirm_password')}</FormLabel>
                          <FormControl>
                            <PasswordInput
                              disabled={!isPasswordTouched}
                              placeholder='e.g., S3cur3P@ssw0rd'
                              className='w-full'
                              {...field}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}
                </>
              )}
              {platformOnly && (
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
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
            </form>
          </Form>
        </div>
        <DialogFooter>
          <Button
            type='button'
            variant='outline'
            disabled={form.formState.isSubmitting}
            onClick={() => {
              form.reset()
              onOpenChange(false)
            }}
          >
            {t('common.cancel')}
          </Button>
          <Button
            type='submit'
            form='user-form'
            disabled={form.formState.isSubmitting}
          >
            {isEdit ? t('users.save_changes') : t('users.add_user')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
