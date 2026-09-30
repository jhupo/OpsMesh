import type { User } from '../data/schema'
import { UserDetailDialog } from './user-detail-dialog'
import { UserSecurityDialog } from './user-security-dialog'
import {
  UsersActionDialog,
  type UserActionFormValues,
  type UserActionResult,
} from './users-action-dialog'
import { UsersDeleteDialog } from './users-delete-dialog'
import {
  UsersInviteDialog,
  type UserInviteFormValues,
} from './users-invite-dialog'
import { useUsers } from './users-provider'

export function UsersDialogs({
  onSave,
  onInvite,
  platformOnly = false,
}: {
  onSave?: (
    values: UserActionFormValues,
    currentRow?: User
  ) => Promise<UserActionResult>
  onInvite?: (values: UserInviteFormValues) => Promise<void>
  platformOnly?: boolean
}) {
  const { open, setOpen, currentRow, setCurrentRow, currentUserId } = useUsers()
  const closeAction = () => {
    setOpen(null)
    setCurrentRow(null)
  }
  return (
    <>
      {currentRow && open === 'detail' && (
        <UserDetailDialog
          user={currentRow}
          onClose={closeAction}
          onAction={setOpen}
          isSelf={currentRow.id === currentUserId}
        />
      )}
      {currentRow &&
        (open === 'reset-password' ||
          open === 'revoke-tokens' ||
          open === 'status') && (
          <UserSecurityDialog
            key={`${currentRow.id}-${open}`}
            user={currentRow}
            action={open}
            onClose={closeAction}
          />
        )}
      <UsersActionDialog
        key='user-add'
        open={open === 'add'}
        onOpenChange={() => setOpen('add')}
        platformOnly={platformOnly}
        phoneEnabled={!onSave}
        onSubmitValues={onSave}
      />

      <UsersInviteDialog
        key='user-invite'
        open={open === 'invite'}
        onOpenChange={() => setOpen('invite')}
        platformOnly={platformOnly}
        onSubmitValues={onInvite}
      />

      {currentRow && (
        <>
          <UsersActionDialog
            key={`user-edit-${currentRow.id}`}
            open={open === 'edit'}
            onOpenChange={() => {
              setOpen('edit')
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
            platformOnly={platformOnly}
            phoneEnabled={!onSave}
            onSubmitValues={onSave}
          />

          <UsersDeleteDialog
            key={`user-delete-${currentRow.id}`}
            open={open === 'delete'}
            onOpenChange={() => {
              setOpen('delete')
              setTimeout(() => {
                setCurrentRow(null)
              }, 500)
            }}
            currentRow={currentRow}
            platformOnly={platformOnly}
          />
        </>
      )}
    </>
  )
}
