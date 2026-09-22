import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useLocation } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { revokeToken } from '@/api/auth'
import {
  clearAuthSession,
  getAuthSession,
  safeRedirectPath,
} from '@/lib/auth-session'
import { ConfirmDialog } from '@/components/confirm-dialog'

interface SignOutDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SignOutDialog({ open, onOpenChange }: SignOutDialogProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { t } = useTranslation()

  const handleSignOut = async () => {
    await queryClient.cancelQueries()
    const session = getAuthSession()
    if (session) await revokeToken(session.tokenId).catch(() => undefined)
    clearAuthSession()
    // Preserve current location for redirect after sign-in
    const currentPath = safeRedirectPath(location.href)
    await navigate({
      to: '/sign-in',
      search: { redirect: currentPath },
      replace: true,
    })
    queryClient.clear()
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('sign_out.title')}
      desc={<span className='sr-only'>{t('sign_out.title')}</span>}
      confirmText={t('sign_out.confirm')}
      destructive
      handleConfirm={handleSignOut}
      className='sm:max-w-sm'
    />
  )
}
