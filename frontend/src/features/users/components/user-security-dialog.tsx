import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Copy, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  resetAdminUserPassword,
  revokeAdminUserTokens,
  updateAdminUserStatus,
} from '@/api/platform-organization'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { type User } from '../data/schema'

export function UserSecurityDialog({
  user,
  action,
  onClose,
}: {
  user: User
  action: 'reset-password' | 'revoke-tokens' | 'status'
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [password, setPassword] = useState('')
  const [confirming, setConfirming] = useState(true)
  const [acknowledged, setAcknowledged] = useState(false)
  const disabling =
    action === 'status' &&
    (user.status === 'active' || user.status === 'invited')
  const title = t(
    action === 'reset-password'
      ? 'users.reset_password'
      : action === 'revoke-tokens'
        ? 'users.revoke_tokens'
        : disabling
          ? 'users.disable_user'
          : 'users.enable_user'
  )
  const consequence = t(
    action === 'reset-password'
      ? 'users.reset_consequence'
      : action === 'revoke-tokens'
        ? 'users.revoke_consequence'
        : disabling
          ? 'users.disable_consequence'
          : 'users.enable_consequence'
  )

  const submit = async () => {
    if (pending || !acknowledged) return
    setPending(true)
    setError('')
    try {
      if (action === 'reset-password') {
        const result = await resetAdminUserPassword(user.id)
        setPassword(result.temporary_password)
        setConfirming(false)
        setAcknowledged(false)
      } else if (action === 'revoke-tokens') {
        const result = await revokeAdminUserTokens(user.id)
        toast.success(t('users.tokens_revoked', { count: result.revoked }))
        onClose()
      } else {
        await updateAdminUserStatus(user.id, disabling ? 'disabled' : 'active')
        toast.success(t('users.saved_success'))
        onClose()
      }
      void queryClient.invalidateQueries({
        queryKey: ['platform-admin', 'organization', 'users'],
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('users.save_error'))
    } finally {
      setPending(false)
    }
  }

  const copyPassword = async () => {
    try {
      await navigator.clipboard.writeText(password)
      toast.success(t('users.password_copied'))
    } catch {
      setError(t('users.copy_failed'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent
        className='max-h-[90dvh] overflow-y-auto'
        showCloseButton={!pending}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className='sr-only'>
            {title}: {user.username}
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor='security-user'>{t('users.user')}</FieldLabel>
            <Input id='security-user' value={user.email} readOnly />
          </Field>
          {confirming ? (
            <Field orientation='horizontal'>
              <Checkbox
                id='security-confirm'
                checked={acknowledged}
                onCheckedChange={(checked) => setAcknowledged(checked === true)}
                disabled={pending}
              />
              <FieldLabel htmlFor='security-confirm'>{consequence}</FieldLabel>
            </Field>
          ) : (
            <Field>
              <FieldLabel htmlFor='temporary-password'>
                {t('users.temporary_password')}
              </FieldLabel>
              <Input
                id='temporary-password'
                value={password}
                readOnly
                autoComplete='off'
                spellCheck={false}
                onFocus={(event) => event.currentTarget.select()}
              />
              <Button variant='outline' onClick={() => void copyPassword()}>
                <Copy />
                {t('users.copy_password')}
              </Button>
              <span role='status' className='text-sm text-muted-foreground'>
                {t('users.password_reset_done')}
              </span>
            </Field>
          )}
        </FieldGroup>
        {error && (
          <Alert variant='destructive'>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant='outline' disabled={pending} onClick={onClose}>
            {t(confirming ? 'common.cancel' : 'common.close')}
          </Button>
          {confirming ? (
            <Button
              variant={disabling ? 'destructive' : 'default'}
              disabled={pending || !acknowledged}
              onClick={() => void submit()}
            >
              {pending && (
                <Loader2 className='animate-spin motion-reduce:animate-none' />
              )}
              {title}
            </Button>
          ) : (
            <Button
              variant='outline'
              onClick={() => {
                setConfirming(true)
                setError('')
              }}
            >
              {t('users.reset_again')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
