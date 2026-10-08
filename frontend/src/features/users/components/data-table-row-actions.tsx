import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { type Row } from '@tanstack/react-table'
import {
  Ellipsis,
  KeyRound,
  LogOut,
  MailPlus,
  UserPen,
  UserRound,
  UserRoundCheck,
  UserRoundX,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { resendUserInvitation } from '@/api/mail'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type User } from '../data/schema'
import { useUsers } from './users-provider'

type DataTableRowActionsProps = {
  row: Row<User>
}

export function DataTableRowActions({ row }: DataTableRowActionsProps) {
  const { setOpen, setCurrentRow, currentUserId } = useUsers()
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [resending, setResending] = useState(false)
  const isInvited = row.original.status === 'invited'
  const isSelf = row.original.id === currentUserId
  const isActive = row.original.status === 'active' || isInvited
  const resend = async () => {
    setResending(true)
    try {
      const invitation = await resendUserInvitation(row.original.id)
      await queryClient.invalidateQueries({
        queryKey: ['platform-admin', 'organization', 'users'],
      })
      if (invitation.delivery_status !== 'sent')
        throw new Error(t('users.invitation_failed'))
      toast.success(t('users.invitation_sent'))
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t('users.error_inviting')
      )
    } finally {
      setResending(false)
    }
  }
  const select = (
    action: 'detail' | 'reset-password' | 'revoke-tokens' | 'status'
  ) => {
    setCurrentRow(row.original)
    setOpen(action)
  }

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant='ghost'
            className='ms-auto flex size-8 p-0 data-[state=open]:bg-muted'
          >
            <Ellipsis />
            <span className='sr-only'>
              {t('users.actions_for', { username: row.original.username })}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align='end' className='w-40'>
          <DropdownMenuGroup>
            <DropdownMenuItem onSelect={() => select('detail')}>
              {t('users.details')}
              <DropdownMenuShortcut>
                <UserRound size={16} />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                setCurrentRow(row.original)
                setOpen('edit')
              }}
            >
              {t('common.edit')}
              <DropdownMenuShortcut>
                <UserPen size={16} />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem
              disabled={isSelf || isInvited}
              title={isSelf ? t('users.self_security') : undefined}
              onSelect={() => select('reset-password')}
            >
              {t('users.reset_password')}
              <DropdownMenuShortcut>
                <KeyRound size={16} />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={isSelf || isInvited}
              title={isSelf ? t('users.self_security') : undefined}
              onSelect={() => select('revoke-tokens')}
            >
              {t('users.revoke_tokens')}
              <DropdownMenuShortcut>
                <LogOut size={16} />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
            {isInvited && (
              <DropdownMenuItem
                disabled={resending}
                onSelect={() => {
                  void resend()
                }}
              >
                {t('users.resend_invitation')}
                <DropdownMenuShortcut>
                  <MailPlus size={16} />
                </DropdownMenuShortcut>
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              disabled={isSelf}
              onSelect={() => select('status')}
            >
              {t(isActive ? 'users.disable_user' : 'users.enable_user')}
              <DropdownMenuShortcut>
                {isActive ? (
                  <UserRoundX size={16} />
                ) : (
                  <UserRoundCheck size={16} />
                )}
              </DropdownMenuShortcut>
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
