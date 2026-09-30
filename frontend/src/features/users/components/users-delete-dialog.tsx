'use client'

import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { type User } from '../data/schema'

type UserDeleteDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentRow: User
  platformOnly?: boolean
}

export function UsersDeleteDialog({
  open,
  onOpenChange,
  currentRow,
  platformOnly = false,
}: UserDeleteDialogProps) {
  const [value, setValue] = useState('')
  const { t } = useTranslation()

  const handleDelete = () => {
    if (value.trim() !== currentRow.username) return

    toast.error(t('users.delete_unavailable'))
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      form='users-delete-form'
      disabled={value.trim() !== currentRow.username}
      title={
        <span className='text-destructive'>
          <AlertTriangle
            className='me-1 inline-block stroke-destructive'
            size={18}
          />{' '}
          {t('users.delete_user')}
        </span>
      }
      desc={
        <form
          id='users-delete-form'
          onSubmit={(e) => {
            e.preventDefault()
            handleDelete()
          }}
          className='space-y-4'
        >
          <p className='mb-2'>
            {t('users.delete_confirm', { username: currentRow.username })}
            {!platformOnly && (
              <>
                <br />
                {t('users.delete_warning', {
                  role: currentRow.role.toUpperCase(),
                })}
              </>
            )}
          </p>

          <Label className='my-2'>
            {t('users.username')}:
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={t('users.enter_username_confirm')}
              autoFocus
            />
          </Label>

          <Alert variant='destructive'>
            <AlertTitle>{t('users.warning')}</AlertTitle>
            <AlertDescription>{t('users.warning_desc')}</AlertDescription>
          </Alert>
        </form>
      }
      confirmText={t('common.delete')}
      destructive
    />
  )
}
