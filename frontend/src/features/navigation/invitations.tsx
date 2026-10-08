import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { inviteUser } from '@/api/mail'
import { Button } from '@/components/ui/button'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '@/features/platform-admin/page-heading'
import { UsersInviteDialog } from '@/features/users/components/users-invite-dialog'

export function AccountInvitations() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <Main>
      <PlatformPageHeading
        title={t('navigation.admin_invitations_page')}
        actions={
          <Button onClick={() => setOpen(true)}>
            {t('users.invite_user')}
          </Button>
        }
      />
      <UsersInviteDialog
        open={open}
        onOpenChange={setOpen}
        platformOnly
        onSubmitValues={async (values) => {
          await inviteUser({
            email: values.email,
            display_name: values.displayName,
            platform_admin: values.platformAdmin,
          })
        }}
      />
    </Main>
  )
}
