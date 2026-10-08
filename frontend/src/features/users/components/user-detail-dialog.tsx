import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { Copy, KeyRound, UserPen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  adminUserDetailQueryOptions,
  adminWorkspaceQueryOptions,
} from '@/api/platform-organization'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Empty, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { ReadOnlyDataTable } from '@/components/data-table'
import { callTypes } from '../data/data'
import { type User } from '../data/schema'

type MembershipRow = {
  id: string
  name: string
  role: string
  status: string
  createdAt: string
}

export function UserDetailDialog({
  user,
  onClose,
  onAction,
  isSelf = false,
}: {
  user: User
  onClose: () => void
  onAction: (action: 'edit' | 'reset-password') => void
  isSelf?: boolean
}) {
  const { t, i18n } = useTranslation()
  const query = useQuery(adminUserDetailQueryOptions(user.id))
  const detail = query.data
  const workspaceQueries = useQueries({
    queries: (detail?.workspace_memberships ?? []).map((membership) =>
      adminWorkspaceQueryOptions(membership.workspace_id)
    ),
  })
  const memberships = (detail?.workspace_memberships ?? []).map(
    (membership, index) => ({
      id: membership.id,
      name: workspaceQueries[index]?.data?.name ?? membership.workspace_id,
      role: membership.role,
      status: membership.status,
      createdAt: membership.created_at,
    })
  )
  const columns = useMemo<ColumnDef<MembershipRow>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('users.workspaces'),
        cell: ({ row }) => (
          <span className='block max-w-48 truncate' title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'role',
        header: t('users.membership_role'),
        cell: ({ row }) => t('users.membership_role_' + row.original.role),
      },
      {
        accessorKey: 'status',
        header: t('users.status'),
        cell: ({ row }) => (
          <Badge
            variant='outline'
            className={callTypes.get(
              row.original.status === 'active' ? 'active' : 'inactive'
            )}
          >
            {t(
              row.original.status === 'active'
                ? 'users.status_active'
                : 'users.status_inactive'
            )}
          </Badge>
        ),
      },
      {
        accessorKey: 'createdAt',
        header: t('users.joined_at'),
        cell: ({ row }) =>
          new Date(row.original.createdAt).toLocaleDateString(i18n.language),
      },
    ],
    [t, i18n.language]
  )
  const usagePercent =
    user.resourceUsageRate === undefined
      ? undefined
      : Math.max(0, user.resourceUsageRate * 100)
  const fields = detail
    ? [
        [t('users.username'), detail.username ?? '—'],
        [t('users.user_id'), detail.id],
        [
          t('users.created_at'),
          new Date(detail.created_at).toLocaleString(i18n.language),
        ],
        [
          t('users.updated_at'),
          new Date(detail.updated_at).toLocaleString(i18n.language),
        ],
      ]
    : []

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className='flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl'>
        <DialogHeader className='shrink-0 border-b p-5 text-start sm:px-6'>
          <DialogTitle>{t('users.details')}</DialogTitle>
          <DialogDescription className='sr-only'>
            {user.username}
          </DialogDescription>
        </DialogHeader>
        <div className='min-h-0 space-y-5 overflow-y-auto p-5 sm:space-y-6 sm:p-6'>
          {query.isPending && <Skeleton className='h-64 w-full' />}
          {query.isError && (
            <Alert variant='destructive'>
              <AlertDescription>
                {query.error.message}
                <Button variant='outline' onClick={() => void query.refetch()}>
                  {t('users.retry')}
                </Button>
              </AlertDescription>
            </Alert>
          )}
          {detail && (
            <>
              <div className='grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 sm:flex'>
                <Avatar className='size-12 shrink-0'>
                  <AvatarFallback className='bg-primary/10 font-semibold text-primary'>
                    {detail.display_name.slice(0, 2).toUpperCase()}
                  </AvatarFallback>
                </Avatar>
                <div className='min-w-0 flex-1 space-y-2'>
                  <h3 className='text-lg font-semibold break-words'>
                    {detail.display_name}
                  </h3>
                  <p className='text-sm break-all text-muted-foreground'>
                    {detail.email}
                  </p>
                  <div className='flex flex-wrap gap-2'>
                    <Badge
                      variant='outline'
                      className={callTypes.get(
                        detail.status === 'invited'
                          ? 'invited'
                          : detail.status === 'active'
                            ? 'active'
                            : 'inactive'
                      )}
                    >
                      {t(
                        detail.status === 'invited'
                          ? 'users.status_invited'
                          : detail.status === 'active'
                            ? 'users.status_active'
                            : 'users.status_inactive'
                      )}
                    </Badge>
                    <Badge variant='secondary'>
                      {t(
                        detail.platform_admin
                          ? 'users.platform_admin'
                          : 'users.role_user'
                      )}
                    </Badge>
                  </div>
                </div>
                <Button
                  variant='outline'
                  size='sm'
                  className='col-start-2 justify-self-start sm:ms-auto'
                  onClick={() => onAction('edit')}
                >
                  <UserPen />
                  {t('users.edit_user')}
                </Button>
              </div>
              <Separator />
              <section
                className='space-y-4'
                aria-label={t('users.basic_information')}
              >
                <h3 className='text-sm font-semibold'>
                  {t('users.basic_information')}
                </h3>
                <dl className='grid grid-cols-2 gap-4 text-sm'>
                  {fields.map(([label, value]) => (
                    <div key={label} className='min-w-0 space-y-1.5'>
                      <dt className='text-muted-foreground'>{label}</dt>
                      <dd className='flex items-start gap-2 break-all'>
                        <span
                          className={
                            value === detail.id ? 'min-w-0 truncate' : 'min-w-0'
                          }
                          title={value}
                        >
                          {value}
                        </span>
                        {value === detail.id && (
                          <Button
                            variant='ghost'
                            size='icon'
                            className='size-6 shrink-0'
                            aria-label={t('users.copy_id')}
                            onClick={() => {
                              void navigator.clipboard
                                .writeText(detail.id)
                                .then(() => toast.success(t('users.id_copied')))
                                .catch(() =>
                                  toast.error(t('users.copy_failed'))
                                )
                            }}
                          >
                            <Copy className='size-3.5' />
                          </Button>
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
              <Separator />
              <section
                className='space-y-4'
                aria-label={t('users.resource_usage')}
              >
                <h3 className='text-sm font-semibold'>
                  {t('users.resource_usage')}
                </h3>
                <dl className='grid grid-cols-2 gap-4 sm:grid-cols-3'>
                  <div className='space-y-2'>
                    <dt className='text-xs text-muted-foreground'>
                      {t('users.workspaces')}
                    </dt>
                    <dd className='text-2xl font-semibold tabular-nums'>
                      {user.workspaceCount ?? memberships.length}
                    </dd>
                  </div>
                  <div className='space-y-2'>
                    <dt className='text-xs text-muted-foreground'>
                      {t('users.active_workspaces')}
                    </dt>
                    <dd className='text-2xl font-semibold tabular-nums'>
                      {user.activeWorkspaceCount ??
                        memberships.filter(
                          (membership) => membership.status === 'active'
                        ).length}
                    </dd>
                  </div>
                  <div className='col-span-2 space-y-2 sm:col-span-1'>
                    <dt className='text-xs text-muted-foreground'>
                      {t('users.quota_usage')}
                    </dt>
                    <dd className='space-y-2'>
                      <span className='text-2xl font-semibold tabular-nums'>
                        {usagePercent === undefined
                          ? '—'
                          : Math.round(usagePercent) + '%'}
                      </span>
                      {usagePercent !== undefined && (
                        <Progress
                          className='h-1.5'
                          value={Math.min(100, usagePercent)}
                          aria-label={t('users.quota_usage')}
                        />
                      )}
                    </dd>
                  </div>
                </dl>
              </section>
              <Separator />
              <section className='space-y-4' aria-label={t('users.workspaces')}>
                <h3 className='text-sm font-semibold'>
                  {t('users.workspaces')}
                </h3>
                {memberships.length === 0 ? (
                  <Empty>
                    <EmptyHeader>
                      <EmptyTitle>{t('users.no_memberships')}</EmptyTitle>
                    </EmptyHeader>
                  </Empty>
                ) : (
                  <div className='overflow-hidden rounded-md border'>
                    <ReadOnlyDataTable columns={columns} data={memberships} />
                  </div>
                )}
              </section>
            </>
          )}
        </div>
        <DialogFooter className='shrink-0 flex-row justify-between border-t p-4 sm:justify-between sm:px-6'>
          <Button
            variant='outline'
            disabled={isSelf || !detail || detail.status === 'invited'}
            title={isSelf ? t('users.self_security') : undefined}
            onClick={() => onAction('reset-password')}
          >
            <KeyRound />
            {t('users.reset_password')}
          </Button>
          <Button onClick={onClose}>{t('common.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
