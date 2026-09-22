import { LanguageSwitch } from '@/components/language-switch'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { MessageCenter } from '@/features/messages'
import { NotificationCenter } from '@/features/notifications'
import { UpdateIndicator } from '@/features/platform-admin/update-indicator'

type OpsMeshHeaderActionsProps = {
  platformAdmin: boolean
}

export function OpsMeshHeaderActions({
  platformAdmin,
}: OpsMeshHeaderActionsProps) {
  return (
    <div className='ms-auto flex items-center gap-1'>
      <UpdateIndicator enabled={platformAdmin} />
      <ThemeSwitch />
      <LanguageSwitch />
      <NotificationCenter />
      <MessageCenter />
      <ProfileDropdown />
    </div>
  )
}
