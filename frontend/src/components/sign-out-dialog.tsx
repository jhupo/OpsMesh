import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { revokeToken } from '@/api/auth'
import { clearAuthSession, getAuthSession } from '@/lib/auth-session'
import { ConfirmDialog } from '@/components/confirm-dialog'

type SignOutDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SignOutDialog({ open, onOpenChange }: SignOutDialogProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()

  const signOut = useMutation({
    mutationFn: async () => {
      const session = getAuthSession()
      if (session) await revokeToken(session.tokenId)
    },
    onSettled: () => {
      clearAuthSession()
      queryClient.clear()
      onOpenChange(false)
      void navigate({
        to: '/login',
        search: { redirect: location.href },
        replace: true,
      })
    },
  })

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('auth.signOut.title')}
      description={t('auth.signOut.description')}
      cancelText={t('common.cancel')}
      confirmText={t('auth.signOut.confirm')}
      loading={signOut.isPending}
      onConfirm={() => signOut.mutate()}
    />
  )
}
