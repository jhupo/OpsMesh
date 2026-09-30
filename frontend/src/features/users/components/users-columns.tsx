import { useMemo } from 'react'
import { type ColumnDef } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Progress } from '@/components/ui/progress'
import { DataTableColumnHeader } from '@/components/data-table'
import { callTypes } from '../data/data'
import { type User } from '../data/schema'
import { DataTableRowActions } from './data-table-row-actions'

export function useUsersColumns(): ColumnDef<User>[] {
  const { t, i18n } = useTranslation()

  return useMemo<ColumnDef<User>[]>(
    () => [
      {
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && 'indeterminate')
            }
            onCheckedChange={(value) =>
              table.toggleAllPageRowsSelected(!!value)
            }
            aria-label='Select all'
            className='translate-y-0.5'
          />
        ),
        meta: {
          className: 'w-8 px-1 sm:w-10 sm:px-2',
        },
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label='Select row'
            className='translate-y-0.5'
          />
        ),
        enableSorting: false,
        enableHiding: false,
      },
      {
        accessorKey: 'username',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={t('users.user')} />
        ),
        cell: ({ row }) => {
          const { username, displayName, email, firstName, lastName } =
            row.original
          const initials =
            `${firstName.charAt(0)}${lastName.charAt(0)}`.toUpperCase()
          return (
            <div className='flex w-28 items-center gap-2 sm:w-48 sm:gap-3'>
              <Avatar className='hidden size-9 shrink-0 sm:flex'>
                <AvatarFallback className='bg-primary/10 text-xs font-semibold text-primary'>
                  {initials || username.slice(0, 2).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <div className='min-w-0 leading-tight'>
                <div className='truncate font-medium text-foreground'>
                  {displayName || username}
                </div>
                <div className='truncate text-xs text-muted-foreground'>
                  {email}
                </div>
              </div>
            </div>
          )
        },
        meta: {
          className: 'ps-1',
        },
        filterFn: (row, _columnId, value) =>
          [
            row.original.displayName,
            row.original.username,
            row.original.email,
          ].some((field) =>
            field
              ?.toLocaleLowerCase()
              .includes(String(value).toLocaleLowerCase())
          ),
        enableHiding: false,
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={t('users.status')} />
        ),
        cell: ({ row }) => {
          const { status } = row.original
          const badgeColor = callTypes.get(status)
          const statusLabel =
            status === 'active'
              ? t('users.status_active')
              : status === 'inactive'
                ? t('users.status_inactive')
                : status === 'invited'
                  ? t('users.status_invited')
                  : t('users.status_suspended')
          return (
            <div className='flex flex-col items-start gap-1'>
              <Badge variant='outline' className={cn('capitalize', badgeColor)}>
                {statusLabel}
              </Badge>
              {status === 'invited' &&
                row.original.invitationDeliveryStatus === 'failed' && (
                  <span className='text-xs text-destructive'>
                    {t('users.email_failed')}
                  </span>
                )}
            </div>
          )
        },
        filterFn: (row, id, value) => {
          return value.includes(row.getValue(id))
        },
        enableHiding: false,
        enableSorting: false,
      },
      {
        id: 'workspaces',
        accessorFn: (row) => row.workspaceCount ?? 0,
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('users.workspaces')}
          />
        ),
        cell: ({ row }) => {
          const { workspaceCount = 0, activeWorkspaceCount = 0 } = row.original
          return (
            <span
              className='text-sm tabular-nums'
              title={t('users.active_workspace_count')}
            >
              {activeWorkspaceCount} / {workspaceCount}
            </span>
          )
        },
        enableSorting: true,
      },
      {
        id: 'resourceUsageRate',
        accessorFn: (row) => row.resourceUsageRate ?? 0,
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('users.quota_usage')}
          />
        ),
        cell: ({ row }) => {
          const rate = Math.max(row.original.resourceUsageRate ?? 0, 0)
          return (
            <div className='flex w-20 items-center gap-2 sm:w-32'>
              <Progress
                value={Math.min(rate * 100, 100)}
                aria-label={t('users.quota_usage')}
              />
              <span className='w-10 text-end text-xs text-muted-foreground tabular-nums'>
                {Math.round(rate * 100)}%
              </span>
            </div>
          )
        },
        enableSorting: true,
      },
      {
        id: 'loginName',
        accessorFn: (row) => row.loginName ?? '',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={t('users.username')} />
        ),
        cell: ({ row }) => (
          <span
            className='block max-w-40 truncate'
            title={row.original.loginName ?? undefined}
          >
            {row.original.loginName || '—'}
          </span>
        ),
      },
      {
        accessorKey: 'platformAdmin',
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('users.access_level')}
          />
        ),
        cell: ({ row }) => (
          <Badge variant='outline'>
            {t(
              row.original.platformAdmin
                ? 'users.platform_admin'
                : 'users.role_user'
            )}
          </Badge>
        ),
      },
      ...(['createdAt', 'updatedAt'] as const).map(
        (field): ColumnDef<User> => ({
          accessorKey: field,
          header: ({ column }) => (
            <DataTableColumnHeader
              column={column}
              title={t(
                field === 'createdAt' ? 'users.created_at' : 'users.updated_at'
              )}
            />
          ),
          cell: ({ row }) =>
            new Intl.DateTimeFormat(i18n.language, {
              dateStyle: 'short',
              timeStyle: 'short',
            }).format(row.original[field]),
        })
      ),
      {
        id: 'actions',
        cell: DataTableRowActions,
        meta: { className: 'sticky end-0 z-10 w-10 text-end sm:w-16' },
      },
    ],
    [t, i18n.language]
  )
}
