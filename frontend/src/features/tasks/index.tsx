import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type VisibilityState,
} from '@tanstack/react-table'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { tasksQueryOptions, type WorkspaceTask } from '@/api/tasks'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DataTable,
  DataTableColumnHeader,
  DataTablePagination,
  DataTableViewOptions,
} from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '@/features/platform-admin/page-heading'

export function Tasks() {
  const { activeWorkspace } = useWorkspace()
  return (
    <WorkspaceTasks
      key={activeWorkspace?.id ?? 'none'}
      workspaceId={activeWorkspace?.id}
    />
  )
}

function WorkspaceTasks({ workspaceId }: { workspaceId?: string }) {
  const { t, i18n } = useTranslation()
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 20,
  })
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const tasks = useQuery(
    tasksQueryOptions(workspaceId, pagination.pageIndex, pagination.pageSize)
  )
  const rows = useMemo(() => tasks.data?.items ?? [], [tasks.data?.items])
  const labels = useMemo(
    () => ({
      title: t('tasks.title'),
      status: t('users.status'),
      priority: t('tasks.priority'),
      updated_at: t('workspaceConsole.updated'),
    }),
    [t]
  )
  const columns = useMemo<ColumnDef<WorkspaceTask>[]>(
    () => [
      {
        accessorKey: 'title',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.title} />
        ),
        cell: ({ row }) => (
          <div
            className='w-32 truncate font-medium sm:w-72'
            title={row.original.title}
          >
            {row.original.title}
          </div>
        ),
        enableHiding: false,
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.status} />
        ),
        cell: ({ row }) => (
          <Badge variant='outline'>
            {t(`workspaceConsole.taskStatus.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          </Badge>
        ),
      },
      {
        accessorKey: 'priority',
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title={labels.priority} />
        ),
        cell: ({ row }) => (
          <span className='tabular-nums'>{row.original.priority}</span>
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
    ],
    [labels, t, i18n.language]
  )
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    autoResetPageIndex: false,
    manualPagination: true,
    rowCount: tasks.data?.total ?? 0,
    enableSorting: false,
    state: { pagination, columnVisibility },
    onPaginationChange: setPagination,
    onColumnVisibilityChange: setColumnVisibility,
  })
  return (
    <Main className='flex min-w-0 flex-1 flex-col gap-4'>
      <PlatformPageHeading
        title={t('tasks.title')}
        actions={
          <Button
            size='icon'
            variant='outline'
            aria-label={t('usersConsole.refresh')}
            disabled={!workspaceId || tasks.isFetching}
            onClick={() => void tasks.refetch()}
          >
            <RefreshCw />
          </Button>
        }
      />
      {!workspaceId ? (
        <div className='py-16 text-center text-muted-foreground'>
          {t('workspaceConsole.noWorkspace')}
        </div>
      ) : tasks.error ? (
        <Alert variant='destructive'>
          <AlertDescription>{tasks.error.message}</AlertDescription>
        </Alert>
      ) : tasks.isPending ? (
        <Skeleton className='h-80 w-full' />
      ) : (
        <>
          <div className='flex items-center justify-between gap-2'>
            <span className='text-sm text-muted-foreground'>
              {t('workspaceConsole.totalTasks', { count: tasks.data.total })}
            </span>
            <DataTableViewOptions
              table={table}
              columnLabels={(column) =>
                labels[column as keyof typeof labels] ?? column
              }
            />
          </div>
          <DataTable table={table} />
          <DataTablePagination
            table={table}
            className='mt-auto'
            disabled={tasks.isFetching}
          />
        </>
      )}
    </Main>
  )
}
