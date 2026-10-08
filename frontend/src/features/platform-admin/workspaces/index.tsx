import { useMemo, useState } from 'react'
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type OnChangeFn,
  type VisibilityState,
} from '@tanstack/react-table'
import {
  Archive,
  Building2,
  CirclePause,
  Eye,
  MoreHorizontal,
  Power,
  PowerOff,
  RefreshCw,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  adminUsersQueryOptions,
  adminWorkspacesQueryOptions,
  updateAdminWorkspaceStatus,
  type AdminWorkspace,
} from '@/api/platform-organization'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  DataTable,
  DataTableColumnHeader,
  DataTablePagination,
  DataTableViewOptions,
} from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '../page-heading'
import { WorkspaceStatusBadge } from './shared'
import { errorMessage, shortId } from './utils'
import { WorkspaceDetailSheet } from './workspace-detail-sheet'

const workspaceStatuses = ['active', 'paused', 'disabled', 'archived'] as const
type WorkspaceStatus = (typeof workspaceStatuses)[number]

export function PlatformWorkspaces() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('all')
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>()
  const [statusChange, setStatusChange] = useState<{
    workspace: AdminWorkspace
    status: WorkspaceStatus
  }>()
  const workspaces = useQuery({
    ...adminWorkspacesQueryOptions(status, pageSize, page * pageSize),
    placeholderData: keepPreviousData,
  })
  const rows = workspaces.data?.items ?? []
  const users = useQuery({
    ...adminUsersQueryOptions(),
    enabled: rows.length > 0,
  })
  const ownerNames = useMemo(
    () =>
      new Map(
        (users.data?.items ?? []).map((user) => [
          user.id,
          user.display_name || user.email,
        ])
      ),
    [users.data]
  )
  const total = workspaces.data?.total ?? 0

  const statusMutation = useMutation({
    mutationFn: ({
      workspace,
      status: nextStatus,
    }: {
      workspace: AdminWorkspace
      status: WorkspaceStatus
    }) => updateAdminWorkspaceStatus(workspace.id, nextStatus),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['platform-admin', 'organization', 'workspaces'],
      })
      if (selectedWorkspaceId) {
        await queryClient.invalidateQueries({
          queryKey: [
            'platform-admin',
            'organization',
            'workspace',
            selectedWorkspaceId,
          ],
        })
      }
      setStatusChange(undefined)
      toast.success(t('platformAdmin.workspaces.statusUpdated'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: ['platform-admin', 'organization', 'workspaces'],
    })

  return (
    <Main className='flex min-w-0 flex-1 flex-col text-foreground'>
      <PlatformPageHeading
        title={t('platformAdmin.navigation.workspaces')}
        actions={
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant='outline'
                size='icon'
                onClick={() => void refresh()}
                disabled={workspaces.isFetching}
                aria-label={t('platformAdmin.workspaces.refresh')}
              >
                <RefreshCw
                  className={workspaces.isFetching ? 'animate-spin' : ''}
                  aria-hidden='true'
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {t('platformAdmin.workspaces.refresh')}
            </TooltipContent>
          </Tooltip>
        }
      />

      <section className='flex min-w-0 flex-1 flex-col gap-4'>
        <div className='flex flex-wrap items-center justify-between gap-3'>
          <p className='text-sm text-muted-foreground' aria-live='polite'>
            {t('platformAdmin.workspaces.resultCount', { count: total })}
          </p>
          <Select
            value={status}
            onValueChange={(value) => {
              setStatus(value)
              setPage(0)
            }}
          >
            <SelectTrigger
              className='w-full sm:w-44'
              aria-label={t('platformAdmin.workspaces.statusFilter')}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align='end'>
              <SelectGroup>
                <SelectItem value='all'>
                  {t('platformAdmin.ui.allStatuses')}
                </SelectItem>
                {workspaceStatuses.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`platformAdmin.workspaces.status.${value}`)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>

        {workspaces.isError ? (
          <Alert variant='destructive'>
            <AlertTitle>{t('platformAdmin.workspaces.loadError')}</AlertTitle>
            <AlertDescription>
              {errorMessage(workspaces.error, t('common.error'))}
            </AlertDescription>
          </Alert>
        ) : workspaces.isPending ? (
          <WorkspaceTableSkeleton />
        ) : rows.length === 0 ? (
          <Empty className='h-64 flex-none border'>
            <EmptyHeader>
              <EmptyMedia variant='icon'>
                <Building2 aria-hidden='true' />
              </EmptyMedia>
              <EmptyTitle>
                {t('platformAdmin.workspaces.emptyTitle')}
              </EmptyTitle>
              <EmptyDescription>
                {status === 'all'
                  ? t('platformAdmin.workspaces.emptyDescription')
                  : t('platformAdmin.workspaces.emptyFilteredDescription')}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <WorkspaceListTable
            rows={rows}
            total={total}
            pagination={{ pageIndex: page, pageSize }}
            isFetching={workspaces.isFetching}
            onPaginationChange={(updater) => {
              const current = { pageIndex: page, pageSize }
              const next =
                typeof updater === 'function' ? updater(current) : updater
              setPage(next.pageSize === pageSize ? next.pageIndex : 0)
              setPageSize(next.pageSize)
            }}
            ownerNames={ownerNames}
            onDetails={setSelectedWorkspaceId}
            onStatusChange={(workspace, nextStatus) =>
              setStatusChange({ workspace, status: nextStatus })
            }
          />
        )}
      </section>

      <WorkspaceDetailSheet
        workspaceId={selectedWorkspaceId}
        onOpenChange={(open) => {
          if (!open) setSelectedWorkspaceId(undefined)
        }}
      />

      <AlertDialog
        open={Boolean(statusChange)}
        onOpenChange={(open) => {
          if (!open && !statusMutation.isPending) setStatusChange(undefined)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('platformAdmin.workspaces.changeStatusTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('platformAdmin.workspaces.changeStatusDescription', {
                name: statusChange?.workspace.name,
                status: statusChange
                  ? t(`platformAdmin.workspaces.status.${statusChange.status}`)
                  : '',
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={statusMutation.isPending}>
              {t('common.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={statusMutation.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (statusChange) statusMutation.mutate(statusChange)
              }}
            >
              {statusMutation.isPending
                ? t('platformAdmin.workspaces.loading')
                : t('common.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Main>
  )
}

function WorkspaceListTable({
  rows,
  total,
  pagination,
  onPaginationChange,
  ownerNames,
  onDetails,
  onStatusChange,
  isFetching,
}: {
  rows: AdminWorkspace[]
  total: number
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
  ownerNames: Map<string, string>
  onDetails: (id: string) => void
  onStatusChange: (workspace: AdminWorkspace, status: WorkspaceStatus) => void
  isFetching: boolean
}) {
  const { t, i18n } = useTranslation()
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const labels = useMemo(
    () => ({
      name: t('platformAdmin.workspaces.workspace'),
      status: t('platformAdmin.workspaces.statusLabel'),
      member_count: t('platformAdmin.workspaces.members'),
      project_count: t('platformAdmin.workspaces.projects'),
      owner_user_id: t('platformAdmin.workspaces.owner'),
      updated_at: t('platformAdmin.workspaces.updated'),
    }),
    [t]
  )
  const columns = useMemo<ColumnDef<AdminWorkspace>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.name} />
        ),
        cell: ({ row }) => (
          <button
            type='button'
            className='flex w-28 flex-col text-start outline-none focus-visible:ring-2 focus-visible:ring-ring sm:w-60'
            onClick={() => onDetails(row.original.id)}
          >
            <span
              className='w-full truncate font-medium'
              title={row.original.name}
            >
              {row.original.name}
            </span>
            <span
              className='w-full truncate text-xs text-muted-foreground'
              translate='no'
            >
              {row.original.slug}
            </span>
          </button>
        ),
        enableHiding: false,
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.status} />
        ),
        cell: ({ row }) => (
          <WorkspaceStatusBadge status={row.original.status} />
        ),
      },
      ...(['member_count', 'project_count'] as const).map(
        (field): ColumnDef<AdminWorkspace> => ({
          accessorKey: field,
          header: ({ column }) => (
            <DataTableColumnHeader column={column} title={labels[field]} />
          ),
          cell: ({ row }) => (
            <span className='tabular-nums'>{row.original[field]}</span>
          ),
        })
      ),
      {
        accessorKey: 'owner_user_id',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.owner_user_id} />
        ),
        cell: ({ row }) => (
          <div className='max-w-44 truncate'>
            {ownerNames.get(row.original.owner_user_id) ??
              shortId(row.original.owner_user_id)}
          </div>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.updated_at} />
        ),
        cell: ({ row }) =>
          new Intl.DateTimeFormat(i18n.language, {
            dateStyle: 'short',
            timeStyle: 'short',
          }).format(new Date(row.original.updated_at)),
      },
      {
        id: 'actions',
        cell: ({ row }) => (
          <WorkspaceActions
            workspace={row.original}
            onDetails={() => onDetails(row.original.id)}
            onStatusChange={(status) => onStatusChange(row.original, status)}
          />
        ),
        enableHiding: false,
        meta: { className: 'w-10 text-end' },
      },
    ],
    [labels, i18n.language, onDetails, onStatusChange, ownerNames]
  )
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    autoResetPageIndex: false,
    enableSorting: false,
    manualPagination: true,
    rowCount: total,
    state: { pagination, columnVisibility },
    onPaginationChange,
    onColumnVisibilityChange: setColumnVisibility,
  })
  return (
    <div className='flex min-w-0 flex-1 flex-col gap-4'>
      <DataTableViewOptions
        table={table}
        columnLabels={(column) =>
          labels[column as keyof typeof labels] ?? column
        }
      />
      <DataTable table={table} />
      <DataTablePagination
        table={table}
        className='mt-auto'
        disabled={isFetching}
      />
    </div>
  )
}

function WorkspaceActions({
  workspace,
  onDetails,
  onStatusChange,
}: {
  workspace: AdminWorkspace
  onDetails: () => void
  onStatusChange: (status: WorkspaceStatus) => void
}) {
  const { t } = useTranslation()
  const statusIcons = {
    active: Power,
    paused: CirclePause,
    disabled: PowerOff,
    archived: Archive,
  }

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='size-8'
          aria-label={t('platformAdmin.workspaces.openActions', {
            name: workspace.name,
          })}
        >
          <MoreHorizontal aria-hidden='true' />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end' className='w-48'>
        <DropdownMenuItem onSelect={onDetails}>
          <Eye aria-hidden='true' />
          {t('platformAdmin.workspaces.viewDetails')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>
          {t('platformAdmin.workspaces.changeStatus')}
        </DropdownMenuLabel>
        {workspaceStatuses.map((nextStatus) => {
          const Icon = statusIcons[nextStatus]
          return (
            <DropdownMenuItem
              key={nextStatus}
              disabled={workspace.status === nextStatus}
              onSelect={() => onStatusChange(nextStatus)}
            >
              <Icon aria-hidden='true' />
              {t(`platformAdmin.workspaces.status.${nextStatus}`)}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function WorkspaceTableSkeleton() {
  return (
    <div className='overflow-hidden rounded-md border' aria-hidden='true'>
      <div className='flex h-10 items-center gap-8 border-b px-4'>
        {[160, 80, 64, 64, 120, 144].map((width, index) => (
          <Skeleton key={index} className='h-4' style={{ width }} />
        ))}
      </div>
      {Array.from({ length: 5 }, (_, index) => (
        <div
          key={index}
          className='flex h-14 items-center gap-8 border-b px-4 last:border-0'
        >
          <Skeleton className='h-7 w-40' />
          <Skeleton className='h-5 w-16' />
          <Skeleton className='h-4 w-10' />
          <Skeleton className='h-4 w-10' />
          <Skeleton className='h-4 w-28' />
          <Skeleton className='h-4 w-36' />
        </div>
      ))}
    </div>
  )
}
