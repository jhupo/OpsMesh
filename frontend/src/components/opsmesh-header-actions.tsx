import { LanguageSwitch } from '@/components/language-switch'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { MessageCenter } from '@/features/messages'
import { NotificationCenter } from '@/features/notifications'

export function OpsMeshHeaderActions() {
  return (
    <div className='ms-auto flex items-center gap-1'>
      <ThemeSwitch />
      <LanguageSwitch />
      <NotificationCenter />
      <MessageCenter />
      <ProfileDropdown />
    </div>
  )
}
