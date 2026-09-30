import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type VisibilityState,
} from '@tanstack/react-table'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  workspaceResourceConfigs,
  workspaceResourceQueryOptions,
  type WorkspaceResourceView,
} from '@/api/workspace-console'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Empty, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DataTable,
  DataTablePagination,
  DataTableViewOptions,
} from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '@/features/platform-admin/page-heading'

type JsonRecord = Record<string, unknown>

function normalizeRows(value: unknown): JsonRecord[] {
  if (Array.isArray(value)) return value.filter(isRecord)
  if (!isRecord(value)) return []
  if (Array.isArray(value.items)) return value.items.filter(isRecord)
  return Object.keys(value).length ? [value] : []
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeValue(key: string, value: unknown): string {
  if (/(secret|token|password|api[_-]?key|private)/i.test(key)) return '••••'
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return '—'
    }
  }
  return String(value)
}

function labelFor(key: string) {
  return key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/^./, (character) => character.toUpperCase())
}

function resourceColumns(rows: JsonRecord[]): string[] {
  const preferred = [
    'name',
    'display_name',
    'title',
    'status',
    'email',
    'role',
    'created_at',
    'updated_at',
    'id',
  ]
  const keys = new Set<string>()
  rows.forEach((row) => Object.keys(row).forEach((key) => keys.add(key)))
  return [
    ...preferred.filter((key) => keys.has(key)),
    ...Array.from(keys).filter((key) => !preferred.includes(key)),
  ].slice(0, 8)
}

export function WorkspaceResourcePage({ view }: { view: string }) {
  const { t } = useTranslation()
  const config = workspaceResourceConfigs[view as WorkspaceResourceView]
  if (!config) return <EmptyResource title={t('common.not_found')} />
  return <WorkspaceResourceContent view={view as WorkspaceResourceView} />
}

function WorkspaceResourceContent({ view }: { view: WorkspaceResourceView }) {
  const { t } = useTranslation()
  const { activeWorkspace } = useWorkspace()
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  })
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const resource = useQuery(
    workspaceResourceQueryOptions(view, activeWorkspace?.id)
  )
  const rows = useMemo(() => normalizeRows(resource.data), [resource.data])
  const keys = useMemo(() => resourceColumns(rows), [rows])
  const columns = useMemo<ColumnDef<JsonRecord>[]>(
    () =>
      keys.map((key) => ({
        accessorFn: (row) => safeValue(key, row[key]),
        id: key,
        header: labelFor(key),
        cell: ({ row }) => (
          <span
            className='block max-w-40 truncate sm:max-w-72'
            title={safeValue(key, row.original[key])}
          >
            {safeValue(key, row.original[key])}
          </span>
        ),
        enableSorting: false,
        enableHiding: key !== keys[0],
      })),
    [keys]
  )
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row, index) => String(row.id ?? index),
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    autoResetPageIndex: false,
    state: { pagination, columnVisibility },
    onPaginationChange: setPagination,
    onColumnVisibilityChange: setColumnVisibility,
  })
  const title = t(
    `workspaceConsole.navigation.${workspaceResourceConfigs[view].titleKey}`
  )

  return (
    <Main className='flex min-w-0 flex-1 flex-col gap-4'>
      <PlatformPageHeading
        title={title}
        actions={
          <Button
            variant='outline'
            size='icon'
            aria-label={t('workspaceConsole.refresh')}
            disabled={!activeWorkspace || resource.isFetching}
            onClick={() => void resource.refetch()}
          >
            <RefreshCw />
          </Button>
        }
      />
      {!activeWorkspace ? (
        <EmptyResource title={t('workspaceConsole.noWorkspace')} />
      ) : resource.error ? (
        <Alert variant='destructive'>
          <AlertDescription>{resource.error.message}</AlertDescription>
        </Alert>
      ) : resource.isPending ? (
        <Skeleton className='h-80 w-full' />
      ) : rows.length === 0 ? (
        <EmptyResource title={t('workspaceConsole.noData')} />
      ) : (
        <>
          <DataTableViewOptions table={table} columnLabels={labelFor} />
          <DataTable table={table} />
          <DataTablePagination table={table} />
        </>
      )}
    </Main>
  )
}

function EmptyResource({ title }: { title: string }) {
  return (
    <Empty className='min-h-48 border'>
      <EmptyHeader>
        <EmptyTitle>{title}</EmptyTitle>
      </EmptyHeader>
    </Empty>
  )
}
