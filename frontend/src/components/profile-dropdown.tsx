import { useSuspenseQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Bell, LogOut, Settings, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { currentUserQueryOptions } from '@/api/auth'
import useDialogState from '@/hooks/use-dialog-state'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SignOutDialog } from '@/components/sign-out-dialog'
import { UserAvatar } from '@/components/user-avatar'

export function ProfileDropdown() {
  const [open, setOpen] = useDialogState()
  const { t } = useTranslation()
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant='ghost'
            className='relative size-8 rounded-full'
            aria-label={t('profile_dropdown.profile')}
          >
            <UserAvatar user={user} className='size-8' />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className='w-56' align='end'>
          <DropdownMenuLabel className='font-normal'>
            <div className='flex flex-col gap-1.5'>
              <p className='truncate text-sm leading-none font-medium'>
                {user.display_name}
              </p>
              <p className='truncate text-xs leading-none text-muted-foreground'>
                {user.email}
              </p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem asChild>
              <Link to='/settings'>
                <UserRound />
                {t('profile_dropdown.profile')}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to='/settings/notifications'>
                <Bell />
                {t('settings.notifications')}
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link
                to={
                  import.meta.env.DEV && user.platform_admin === true
                    ? '/settings/display'
                    : '/settings'
                }
              >
                <Settings />
                {t('profile_dropdown.settings')}
              </Link>
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem
              variant='destructive'
              onClick={() => setOpen(true)}
            >
              <LogOut />
              {t('profile_dropdown.sign_out')}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}
