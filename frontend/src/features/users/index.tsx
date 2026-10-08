import { useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { currentUserQueryOptions } from '@/api/auth'
import { inviteUser as sendUserInvitation } from '@/api/mail'
import {
  adminUsersQueryOptions,
  createAdminUser,
  updateAdminUser,
  updateAdminUserStatus,
  type AdminUserListItem,
} from '@/api/platform-organization'
import type { NavigateFn } from '@/hooks/use-table-url-state'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import { Main } from '@/components/layout/main'
import { ForbiddenError } from '@/features/errors/forbidden'
import type { UserActionResult } from './components/users-action-dialog'
import { UsersDialogs } from './components/users-dialogs'
import type { UserInviteFormValues } from './components/users-invite-dialog'
import { UsersPrimaryButtons } from './components/users-primary-buttons'
import { UsersProvider } from './components/users-provider'
import { UsersTable } from './components/users-table'
import { type User } from './data/schema'

export function Users({ admin = false }: { admin?: boolean } = {}) {
  return admin ? <AdminUsers /> : <RouteUsers />
}

function RouteUsers() {
  const currentUserQuery = useQuery(currentUserQueryOptions())

  if (currentUserQuery.isPending) return <UsersLoading />
  if (currentUserQuery.data?.platform_admin) return <AdminUsers />

  return <ForbiddenError />
}

function AdminUsers() {
  const location = useLocation()
  const navigate = useNavigate() as unknown as NavigateFn
  const queryClient = useQueryClient()
  const currentUserQuery = useQuery(currentUserQueryOptions())
  const usersQuery = useQuery(adminUsersQueryOptions())
  const { t } = useTranslation()
  const data = useMemo(
    () => usersQuery.data?.items.map(toUser) ?? [],
    [usersQuery.data]
  )

  if (currentUserQuery.isPending || usersQuery.isPending) {
    return <UsersLoading />
  }

  if (currentUserQuery.isError || usersQuery.isError) {
    const error = currentUserQuery.error ?? usersQuery.error
    return (
      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <Alert variant='destructive'>
          <AlertTitle>{t('common.error')}</AlertTitle>
          <AlertDescription>
            {error?.message ?? t('common.error')}
          </AlertDescription>
        </Alert>
      </Main>
    )
  }

  const refreshUsers = () =>
    queryClient.invalidateQueries({
      queryKey: ['platform-admin', 'organization', 'users'],
    })

  const saveUser = async (values: UserActionFormValues, currentRow?: User) => {
    let initialPassword: string | undefined
    const displayName =
      values.displayName?.trim() ||
      values.username.trim() ||
      values.email.trim()
    if (currentRow) {
      await updateAdminUser(currentRow.id, {
        display_name: displayName,
        username: values.username.trim() || undefined,
        platform_admin:
          values.platformAdmin ?? currentRow.platformAdmin ?? false,
      })
    } else {
      const created = await createAdminUser({
        email: values.email,
        display_name: displayName,
        username: values.username.trim() || undefined,
        password: values.password || undefined,
        platform_admin: values.platformAdmin ?? false,
      })
      initialPassword = created.initial_password
    }
    await refreshUsers()
    toast.success(t('users.saved_success'))
    if (initialPassword) return { initialPassword }
  }

  const updateStatuses = async (
    selectedUsers: User[],
    status: 'active' | 'inactive'
  ) => {
    await Promise.all(
      selectedUsers.map((user) =>
        updateAdminUserStatus(
          user.id,
          status === 'active' ? 'active' : 'disabled'
        )
      )
    )
    await refreshUsers()
  }

  const inviteUser = async (values: UserInviteFormValues) => {
    const invitation = await sendUserInvitation({
      email: values.email.trim(),
      display_name: values.displayName ?? '',
      platform_admin: values.platformAdmin ?? false,
    })
    await refreshUsers()
    if (invitation.delivery_status !== 'sent')
      throw new Error(t('users.invitation_failed'))
    toast.success(t('users.invitation_sent'))
  }

  return (
    <UsersPage
      admin
      currentUserId={currentUserQuery.data?.user_id}
      data={data}
      search={location.search as Record<string, unknown>}
      navigate={navigate}
      onSave={saveUser}
      onInvite={inviteUser}
      onStatusChange={updateStatuses}
    />
  )
}

function UsersPage({
  data,
  currentUserId,
  admin = false,
  search,
  navigate,
  onSave,
  onInvite,
  onStatusChange,
}: {
  data: User[]
  currentUserId?: string
  admin?: boolean
  search: Record<string, unknown>
  navigate: NavigateFn
  onSave?: (
    values: UserActionFormValues,
    currentRow?: User
  ) => Promise<UserActionResult>
  onInvite?: (values: UserInviteFormValues) => Promise<void>
  onStatusChange?: (
    users: User[],
    status: 'active' | 'inactive'
  ) => Promise<void>
}) {
  const { t } = useTranslation()
  return (
    <UsersProvider currentUserId={currentUserId}>
      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>
              {t('users.title')}
            </h2>
          </div>
          <UsersPrimaryButtons />
        </div>
        <UsersTable
          data={data}
          search={search}
          navigate={navigate}
          admin={admin}
          onStatusChange={onStatusChange}
        />
      </Main>

      <UsersDialogs onSave={onSave} onInvite={onInvite} platformOnly={admin} />
    </UsersProvider>
  )
}

type UserActionFormValues = {
  firstName: string
  lastName: string
  username: string
  phoneNumber: string
  email: string
  password: string
  role: string
  displayName?: string
  platformAdmin?: boolean
}

function toUser(user: AdminUserListItem): User {
  const nameParts = user.display_name.trim().split(/\s+/)
  const [firstName = user.display_name, ...lastNameParts] = nameParts
  return {
    id: user.id,
    firstName,
    lastName: lastNameParts.join(' '),
    displayName: user.display_name,
    invitationDeliveryStatus: user.invitation_delivery_status,
    username: user.username || user.email,
    loginName: user.username,
    email: user.email,
    phoneNumber: '',
    status:
      user.status === 'invited'
        ? 'invited'
        : user.status === 'disabled'
          ? 'inactive'
          : 'active',
    role: user.platform_admin ? 'superadmin' : 'user',
    platformAdmin: user.platform_admin,
    workspaceCount: user.workspace_count,
    activeWorkspaceCount: user.active_workspace_count,
    resourceUsageRate: user.resource_usage_rate,
    createdAt: new Date(user.created_at),
    updatedAt: new Date(user.updated_at),
  }
}

function UsersLoading() {
  return (
    <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
      <div className='flex items-end justify-between gap-2'>
        <Skeleton className='h-8 w-36' />
        <div className='flex gap-2'>
          <Skeleton className='h-9 w-28' />
          <Skeleton className='h-9 w-24' />
        </div>
      </div>
      <Skeleton className='h-96 w-full rounded-md' />
    </Main>
  )
}
