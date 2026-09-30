import {
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
  type ReactNode,
} from 'react'
import {
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
  type SortingState,
  type VisibilityState,
  type PaginationState,
  type OnChangeFn,
} from '@tanstack/react-table'
import type { TFunction } from 'i18next'
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  MoreHorizontal,
  Plus,
  Search,
  SlidersHorizontal,
  Download,
  KeyRound,
  Power,
  UserRoundCheck,
  UserRoundX,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { cn, getPageNumbers } from '@/lib/utils'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  DataTableBulkActions,
  DataTableColumnHeader,
  DataTablePagination,
  DataTable,
  DataTableViewOptions,
} from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '../page-heading'
import {
  definitions,
  routes,
  type Action,
  type Collection,
  type Definition,
  type FieldDefinition,
} from './catalog'
import {
  availableActions,
  downloadRows,
  quotaMetrics,
  type QuotaUsage,
  useManagement,
  type ManagementRow,
  type Membership,
  type ManagementState,
  type ManagementStore,
} from './state'

const titleKey: Partial<Record<Collection, string>> = {
  users: 'userAccess',
  teams: 'teams',
  orchestrations: 'orchestrations',
  approvals: 'approvals',
  deadletters: 'deadLetters',
  lifecycle: 'dataLifecycle',
  knowledge: 'knowledgeBases',
  allocations: 'allocations',
  leases: 'runtimeLeases',
  schedules: 'scheduledJobs',
  runtimeSpaces: 'runtimeSpaces',
  nodes: 'workers',
  selfHosted: 'selfHosted',
  runs: 'runs',
  calls: 'calls',
  logs: 'logs',
  traces: 'traces',
  costs: 'costAnalysis',
  policies: 'policyCenter',
  reviews: 'publicationReview',
  security: 'securityEvents',
  audit: 'auditEvidence',
  models: 'modelServices',
  integrations: 'integrations',
}
const titleFor = (collection: Collection) => titleKey[collection] ?? collection
const formatColumn = (value: string) =>
  value
    .replace(/Id$/, '')
    .replace(/([A-Z])/g, ' $1')
    .replace(/^./, (char) => char.toUpperCase())
const valueFor = (row: ManagementRow, key: string) =>
  row.values[key] ??
  (key === 'name'
    ? row.name
    : key === 'status'
      ? row.status
      : key === 'updatedAt'
        ? row.updatedAt
        : '')
const displayValue = (value: string) => (value ? value.replace(/_/g, ' ') : '—')
const translatedValue = (value: string, t: TFunction) =>
  t(`platformAdmin.options.${value}`, { defaultValue: displayValue(value) })

function initialRow(kind: Collection, currentUser: string): ManagementRow {
  const now = new Date().toISOString()
  return {
    id: crypto.randomUUID(),
    name: '',
    status: definitions[kind].statuses[0] ?? 'active',
    updatedAt: now,
    values:
      kind === 'users'
        ? { email: '', role: 'user', createdAt: now }
        : kind === 'workspaces'
          ? { ownerId: currentUser }
          : {},
  }
}

export function AdminManagementPage({
  collection,
  title,
}: {
  collection: Collection
  title?: string
}) {
  const { t } = useTranslation()
  const { state, store, user } = useManagement()
  const definition = definitions[collection]
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [selected, setSelected] = useState<string[]>([])
  const [editing, setEditing] = useState<ManagementRow | null>(null)
  const [projectEditing, setProjectEditing] = useState<ManagementRow | null>(
    null
  )
  const [detail, setDetail] = useState<ManagementRow | null>(null)
  const [pageIndex, setPageIndex] = useState(0)
  const [pageSize, setPageSize] = useState(10)
  const rows = useMemo(
    () => state.rows[collection] ?? [],
    [state.rows, collection]
  )
  const filtered = useMemo(
    () =>
      rows.filter(
        (row) =>
          (!query ||
            `${row.name} ${Object.values(row.values).join(' ')}`
              .toLowerCase()
              .includes(query.toLowerCase())) &&
          (status === 'all' || row.status === status)
      ),
    [rows, query, status]
  )
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(pageIndex, pageCount - 1)
  const pageRows = filtered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize
  )
  const start = filtered.length === 0 ? 0 : currentPage * pageSize + 1
  const end = Math.min((currentPage + 1) * pageSize, filtered.length)
  const pageTitleKey = titleFor(collection)
  const columnLabel = (column: string) =>
    t(`platformAdmin.table.columns.${column}`, {
      defaultValue: formatColumn(column),
    })
  const updateQuery = (value: string) => {
    setQuery(value)
    setPageIndex(0)
  }
  const updateStatus = (value: string) => {
    setStatus(value)
    setPageIndex(0)
  }
  const save = (row: ManagementRow) => {
    try {
      const normalized = { ...row, name: row.name.trim() }
      store.save(collection, normalized)
      if (collection === 'allocations')
        store.saveAllocation({
          workspaceId: normalized.values.workspaceId ?? '',
          projectId: normalized.values.projectId ?? '',
          resources: (normalized.values.resources ?? '')
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
          concurrency: Number(normalized.values.concurrency ?? 0),
          tokens: Number(normalized.values.tokens ?? 0),
          storage: Number(normalized.values.storage ?? 0),
          budget: Number(normalized.values.budget ?? 0),
        })
      setEditing(null)
      toast.success(t('platformAdmin.ui.saved'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToSave')
      )
    }
  }
  const act = (action: Parameters<typeof store.action>[2], ids = selected) => {
    if (!ids.length) return
    try {
      store.action(collection, ids, action)
      setSelected([])
      toast.success(t('platformAdmin.ui.updatedToast'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToUpdate')
      )
    }
  }
  const saveProject = (project: ManagementRow) => {
    try {
      store.save('projects', project)
      setProjectEditing(null)
      toast.success(t('platformAdmin.ui.saved'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToSave')
      )
    }
  }
  const presentation = presentationFor(collection)
  const sharedViewProps = {
    collection,
    definition,
    state,
    rows: presentation === 'table' ? filtered : pageRows,
    selected,
    setSelected,
    setDetail,
    t,
    columnLabel,
  }
  const pageActions = (
    <div className='flex shrink-0 items-center gap-2'>
      <Button
        variant='outline'
        size='sm'
        className='max-md:size-9 max-md:px-0'
        onClick={() => downloadRows(collection, rows)}
        aria-label={t('platformAdmin.ui.export')}
      >
        <Download data-icon='inline-start' />
        <span className='max-md:sr-only'>{t('platformAdmin.ui.export')}</span>
      </Button>
      {definition.create && (
        <Button
          size='sm'
          className='max-md:size-9 max-md:px-0'
          onClick={() => setEditing(initialRow(collection, user.user_id))}
          aria-label={t('platformAdmin.ui.create')}
        >
          <Plus data-icon='inline-start' />
          <span className='max-md:sr-only'>{t('platformAdmin.ui.create')}</span>
        </Button>
      )}
    </div>
  )
  const content = (
    <section className='flex h-full min-h-0 flex-1 flex-col gap-4'>
      <ManagementToolbar
        definition={definition}
        query={query}
        status={status}
        selected={selected}
        setQuery={updateQuery}
        setStatus={updateStatus}
        clearSelection={() => setSelected([])}
        act={act}
        t={t}
        actions={collection === 'users' ? pageActions : undefined}
      />
      {presentation === 'table' &&
        (collection === 'users' ? (
          <UsersManagementTable {...sharedViewProps} store={store} />
        ) : (
          <ManagementTable
            {...sharedViewProps}
            pagination={{ pageIndex: currentPage, pageSize }}
            onPaginationChange={(updater) => {
              const current = { pageIndex: currentPage, pageSize }
              const next =
                typeof updater === 'function' ? updater(current) : updater
              setPageIndex(next.pageSize === pageSize ? next.pageIndex : 0)
              setPageSize(next.pageSize)
            }}
          />
        ))}
      {presentation === 'cards' && <ManagementCards {...sharedViewProps} />}
      {presentation === 'queue' && <ManagementQueue {...sharedViewProps} />}
      {presentation === 'allocation' && <AllocationGrid {...sharedViewProps} />}
      {presentation !== 'table' && (
        <ManagementPagination
          pageIndex={currentPage}
          pageCount={pageCount}
          pageSize={pageSize}
          start={start}
          end={end}
          total={filtered.length}
          setPageIndex={setPageIndex}
          setPageSize={setPageSize}
          t={t}
        />
      )}
    </section>
  )
  return (
    <Main fixed>
      <PlatformPageHeading
        title={title ?? t(`platformAdmin.navigation.${pageTitleKey}`)}
        actions={collection !== 'users' ? pageActions : undefined}
      />
      <div className='min-h-0 flex-1 overflow-auto p-1'>{content}</div>
      <ManagementForm
        key={editing?.id ?? 'closed'}
        open={!!editing}
        row={editing}
        collection={collection}
        onClose={() => setEditing(null)}
        onSave={save}
        currentUser={user.user_id}
        state={state}
      />
      <ManagementForm
        key={projectEditing?.id ?? 'project-closed'}
        open={!!projectEditing}
        row={projectEditing}
        collection='projects'
        onClose={() => setProjectEditing(null)}
        onSave={saveProject}
        currentUser={user.user_id}
        state={state}
      />
      <DetailSheet
        row={detail ? (rows.find((row) => row.id === detail.id) ?? null) : null}
        collection={collection}
        state={state}
        store={store}
        onClose={() => setDetail(null)}
        onEdit={() => {
          setEditing(detail)
          setDetail(null)
        }}
        onAction={(action) => act(action, detail ? [detail.id] : [])}
        onProjectEdit={(project) => {
          setProjectEditing(project)
          setDetail(null)
        }}
      />
    </Main>
  )
}

type Presentation = 'table' | 'cards' | 'queue' | 'allocation'
type ManagementViewProps = {
  collection: Collection
  definition: Definition
  state: ManagementState
  rows: ManagementRow[]
  selected: string[]
  setSelected: Dispatch<SetStateAction<string[]>>
  setDetail: Dispatch<SetStateAction<ManagementRow | null>>
  t: TFunction
  columnLabel: (column: string) => string
}

const tableCollections = new Set<Collection>([
  'users',
  'tasks',
  'audit',
  'files',
  'artifacts',
  'runs',
  'calls',
  'logs',
  'traces',
  'costs',
])
const queueCollections = new Set<Collection>([
  'approvals',
  'reviews',
  'security',
  'leases',
  'deadletters',
])

function presentationFor(collection: Collection): Presentation {
  if (collection === 'allocations') return 'allocation'
  if (queueCollections.has(collection)) return 'queue'
  if (tableCollections.has(collection)) return 'table'
  return 'cards'
}

function UsersManagementTable({
  state,
  rows,
  setDetail,
  t,
  store,
}: ManagementViewProps & { store: ManagementStore }) {
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [sorting, setSorting] = useState<SortingState>([])
  const columns = useMemo<ColumnDef<ManagementRow>[]>(
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
            aria-label={t('platformAdmin.ui.selectAll')}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            disabled={!row.getCanSelect()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label={`${t('platformAdmin.ui.select')} ${row.original.name}`}
          />
        ),
        enableSorting: false,
        enableHiding: false,
        meta: { className: 'w-12' },
      },
      {
        id: 'name',
        accessorFn: (row) => row.name,
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('platformAdmin.table.columns.name')}
          />
        ),
        cell: ({ row }) => {
          const user = row.original
          const initials = user.name
            .split(/\s+/)
            .map((part) => part[0])
            .slice(0, 2)
            .join('')
            .toUpperCase()
          return (
            <div className='flex min-w-56 items-center gap-3 py-1'>
              <Avatar className='size-9'>
                <AvatarImage src={user.values.avatarUrl} alt='' />
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              <button
                type='button'
                className='flex min-w-0 flex-col text-start focus-visible:outline-ring'
                onClick={() => setDetail(user)}
              >
                <span className='truncate font-medium hover:underline'>
                  {user.name}
                </span>
                <span className='max-w-56 truncate text-xs text-muted-foreground'>
                  {user.values.email || '—'}
                </span>
              </button>
            </div>
          )
        },
        enableHiding: false,
      },
      {
        id: 'role',
        accessorFn: (row) => row.values.role ?? '',
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('platformAdmin.table.columns.role')}
          />
        ),
        cell: ({ row }) => (
          <span className='whitespace-nowrap'>
            {translatedValue(row.original.values.role ?? '', t)}
          </span>
        ),
      },
      {
        id: 'status',
        accessorKey: 'status',
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('platformAdmin.table.columns.status')}
          />
        ),
        cell: ({ row }) => <StatusBadge status={row.original.status} t={t} />,
      },
      {
        id: 'quotaUsage',
        header: t('platformAdmin.table.columns.quotaUsage'),
        enableSorting: false,
        cell: ({ row }) => (
          <UserQuotaUsage row={row.original} state={state} t={t} />
        ),
      },
      {
        id: 'ownership',
        header: t('platformAdmin.table.columns.ownership'),
        enableSorting: false,
        cell: ({ row }) => (
          <button
            type='button'
            className='max-w-48 truncate text-start text-muted-foreground hover:underline focus-visible:outline-ring'
            onClick={() => setDetail(row.original)}
          >
            {displayCellValue(row.original, 'ownership', state, t)}
          </button>
        ),
      },
      {
        id: 'createdAt',
        accessorFn: (row) => row.updatedAt,
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('platformAdmin.table.columns.createdAt')}
          />
        ),
        cell: ({ row }) =>
          displayCellValue(row.original, 'createdAt', state, t),
      },
      {
        id: 'lastLoginAt',
        accessorFn: (row) => row.values.lastLoginAt ?? '',
        header: ({ column }) => (
          <DataTableColumnHeader
            column={column}
            title={t('platformAdmin.table.columns.lastLoginAt')}
          />
        ),
        cell: ({ row }) =>
          displayCellValue(row.original, 'lastLoginAt', state, t),
      },
      {
        id: 'actions',
        header: t('platformAdmin.ui.actions'),
        cell: ({ row }) => (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant='ghost'
                size='icon'
                className='size-8'
                onClick={() => setDetail(row.original)}
                aria-label={t('platformAdmin.ui.actions')}
              >
                <SlidersHorizontal />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t('platformAdmin.ui.actions')}</TooltipContent>
          </Tooltip>
        ),
        enableSorting: false,
        enableHiding: false,
        meta: { className: 'w-16' },
      },
    ],
    [setDetail, state, t]
  )
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    state: { rowSelection, sorting },
    enableRowSelection: (row) => row.original.id !== store.userId,
    onRowSelectionChange: setRowSelection,
    onSortingChange: setSorting,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    autoResetPageIndex: false,
    getSortedRowModel: getSortedRowModel(),
    initialState: { pagination: { pageSize: 10 } },
  })
  const selectedRows = table.getSelectedRowModel().rows
  const selectedIds = selectedRows.map((item) => item.original.id)
  const runBulkAction = (action: Action) => {
    const eligibleIds = selectedRows
      .filter((item) =>
        availableActions('users', item.original).includes(action)
      )
      .map((item) => item.original.id)
    if (!eligibleIds.length) return
    try {
      store.action('users', eligibleIds, action)
      table.resetRowSelection()
      toast.success(t('platformAdmin.ui.updatedToast'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToUpdate')
      )
    }
  }
  const bulkButton = (action: Action, icon: ReactNode) => {
    const count = selectedIds.length
    return (
      <Tooltip key={action}>
        <TooltipTrigger asChild>
          <Button
            variant={action === 'disable' ? 'destructive' : 'outline'}
            size='icon'
            className='size-8'
            disabled={count === 0}
            onClick={() => runBulkAction(action)}
            aria-label={t(`platformAdmin.ui.action.${action}`, {
              defaultValue: action,
            })}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          {t(`platformAdmin.ui.action.${action}`, { defaultValue: action })}
        </TooltipContent>
      </Tooltip>
    )
  }
  return (
    <div className='@container/content flex h-full min-h-0 flex-1 flex-col gap-4 max-sm:has-[div[role="toolbar"]]:mb-16'>
      <div className='min-h-0 flex-1 overflow-auto rounded-md bg-card'>
        <DataTable table={table} />
      </div>
      <DataTablePagination table={table} className='mt-auto pt-4' />
      <DataTableBulkActions
        table={table}
        entityName={t('platformAdmin.navigation.users')}
      >
        {bulkButton('enable', <UserRoundCheck />)}
        {bulkButton('disable', <UserRoundX />)}
        {bulkButton('revoke', <Power />)}
        {bulkButton('reset', <KeyRound />)}
      </DataTableBulkActions>
    </div>
  )
}

function ManagementToolbar({
  definition,
  query,
  status,
  selected,
  setQuery,
  setStatus,
  clearSelection,
  act,
  t,
  actions,
}: {
  definition: Definition
  query: string
  status: string
  selected: string[]
  setQuery: (value: string) => void
  setStatus: (value: string) => void
  clearSelection: () => void
  act: (action: Action) => void
  t: TFunction
  actions?: ReactNode
}) {
  const bulkActions = definition.actions
    .filter((action) =>
      [
        'enable',
        'disable',
        'delete',
        'retry',
        'approve',
        'reject',
        'publish',
        'withdraw',
      ].includes(action)
    )
    .slice(0, 2)
  return (
    <div className='flex flex-col gap-3'>
      <div className='flex flex-wrap items-center gap-2'>
        <div className='relative min-w-32 flex-1 sm:max-w-72'>
          <Search
            className='absolute start-2.5 top-2.5 size-4 text-muted-foreground'
            aria-hidden='true'
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('platformAdmin.ui.search')}
            className='h-9 ps-8'
          />
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger
            className='h-9 w-32 sm:w-40'
            aria-label={t('platformAdmin.ui.filter')}
          >
            <SlidersHorizontal className='size-3.5 text-muted-foreground' />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>
              {t('platformAdmin.ui.allStatuses')}
            </SelectItem>
            {definition.statuses.map((value) => (
              <SelectItem key={value} value={value}>
                {t(`platformAdmin.ui.status.${value}`, { defaultValue: value })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {actions && (
          <div className='flex shrink-0 items-center gap-2 sm:ms-auto'>
            {actions}
          </div>
        )}
      </div>
      {selected.length > 0 && (
        <div className='col-span-2 flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-2 py-1.5 text-sm'>
          <span className='px-1 text-muted-foreground'>
            {selected.length} {t('platformAdmin.ui.selected')}
          </span>
          {bulkActions.map((action) => (
            <Button
              key={action}
              size='sm'
              variant='outline'
              onClick={() => act(action)}
            >
              {t(`platformAdmin.ui.action.${action}`, { defaultValue: action })}
            </Button>
          ))}
          <Button
            size='icon'
            variant='ghost'
            className='size-8'
            onClick={clearSelection}
            aria-label={t('platformAdmin.ui.clearSelection')}
          >
            <X className='size-4' />
          </Button>
        </div>
      )}
    </div>
  )
}

function ManagementTable({
  definition,
  state,
  rows,
  selected,
  setSelected,
  setDetail,
  t,
  columnLabel,
  pagination,
  onPaginationChange,
}: ManagementViewProps & {
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
}) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const columns = useMemo<ColumnDef<ManagementRow>[]>(
    () => [
      {
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && 'indeterminate')
            }
            onCheckedChange={(checked) =>
              table.toggleAllPageRowsSelected(!!checked)
            }
            aria-label={t('platformAdmin.ui.selectAll')}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(checked) => row.toggleSelected(!!checked)}
            aria-label={`${t('platformAdmin.ui.select')} ${row.original.name}`}
          />
        ),
        enableSorting: false,
        enableHiding: false,
        meta: { className: 'w-8 px-1 sm:w-10 sm:px-2' },
      },
      ...definition.columns.map(
        (field): ColumnDef<ManagementRow> => ({
          id: field,
          accessorFn: (row) => valueFor(row, field),
          header: ({ column }) => (
            <DataTableColumnHeader column={column} title={columnLabel(field)} />
          ),
          cell: ({ row }) =>
            field === 'name' ? (
              <button
                type='button'
                className='block w-28 truncate text-start font-medium hover:underline focus-visible:outline-ring sm:w-60'
                title={row.original.name}
                onClick={() => setDetail(row.original)}
              >
                {row.original.name}
              </button>
            ) : field === 'status' ? (
              <StatusBadge status={row.original.status} t={t} />
            ) : (
              <span
                className='block max-w-32 truncate sm:max-w-64'
                title={displayCellValue(row.original, field, state, t)}
              >
                {displayCellValue(row.original, field, state, t)}
              </span>
            ),
          enableHiding: field !== 'name',
        })
      ),
      {
        id: 'actions',
        cell: ({ row }) => (
          <Button
            variant='ghost'
            size='icon'
            className='size-8'
            aria-label={t('platformAdmin.ui.actions')}
            onClick={() => setDetail(row.original)}
          >
            <MoreHorizontal />
          </Button>
        ),
        enableSorting: false,
        enableHiding: false,
        meta: { className: 'w-10 text-end' },
      },
    ],
    [definition, state, setDetail, t, columnLabel]
  )
  const rowSelection = useMemo(
    () => Object.fromEntries(selected.map((id) => [id, true])),
    [selected]
  )
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    autoResetPageIndex: false,
    state: { pagination, sorting, columnVisibility, rowSelection },
    onPaginationChange,
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onRowSelectionChange: (updater) => {
      const next =
        typeof updater === 'function' ? updater(rowSelection) : updater
      setSelected(Object.keys(next).filter((id) => next[id]))
    },
  })
  return (
    <div className='flex min-w-0 flex-1 flex-col gap-4'>
      <DataTableViewOptions table={table} columnLabels={columnLabel} />
      <DataTable table={table} />
      <DataTablePagination table={table} className='mt-auto' />
    </div>
  )
}

function QuotaMeter({
  metric,
  usage,
  t,
  compact = false,
}: {
  metric: QuotaUsage['metric']
  usage?: QuotaUsage
  t: TFunction
  compact?: boolean
}) {
  const used = usage?.used ?? null
  const limit = usage?.limit ?? null
  const percentage =
    used === null || limit === null
      ? null
      : limit === 0
        ? used > 0
          ? 100
          : 0
        : (used / limit) * 100
  const format = new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  })
  const label = t(`platformAdmin.quotaMetrics.${metric}`)
  const unit = {
    tokens: '',
    storage: 'GiB',
    concurrency: '',
    cpu: 'vCPU',
    memory: 'GiB',
    runtimeHours: 'h',
    budget: 'USD',
  }[metric]
  const value = `${used === null ? '—' : format.format(used)} / ${limit === null ? '—' : format.format(limit)}${unit ? ` ${unit}` : ''}`
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', compact && 'gap-1')}>
      <div
        className={cn(
          'flex items-center justify-between gap-1 whitespace-nowrap tabular-nums',
          compact ? 'text-[10px]' : 'text-xs'
        )}
      >
        <span className='min-w-0 truncate text-muted-foreground'>{label}</span>
        <span>
          {compact
            ? percentage === null
              ? '—'
              : `${percentage.toFixed(0)}%`
            : value}
        </span>
      </div>
      <Progress
        value={percentage === null ? null : Math.min(100, percentage)}
        className={cn(
          'h-1.5',
          used !== null &&
            limit !== null &&
            used > limit &&
            '[&>[data-slot=progress-indicator]]:bg-destructive'
        )}
        aria-label={label}
        aria-valuetext={value}
      />
      {!compact && (
        <div className='flex items-center justify-between gap-2 text-xs text-muted-foreground tabular-nums'>
          <span>
            {usage?.periodStart && usage.periodEnd
              ? `${formatDate(usage.periodStart)} – ${formatDate(usage.periodEnd)}`
              : null}
          </span>
          <span>{percentage === null ? '—' : `${percentage.toFixed(1)}%`}</span>
        </div>
      )}
    </div>
  )
}

function UserQuotaUsage({
  row,
  state,
  t,
}: {
  row: ManagementRow
  state: ManagementState
  t: TFunction
}) {
  const scopes = new Map<string, { workspaceId: string; projectId: string }>()
  for (const usage of row.quotaUsage ?? [])
    scopes.set(JSON.stringify([usage.workspaceId, usage.projectId]), usage)
  for (const membership of state.memberships.filter(
    (item) => item.userId === row.id
  )) {
    const key = JSON.stringify([membership.workspaceId, ''])
    if (!scopes.has(key))
      scopes.set(key, { workspaceId: membership.workspaceId, projectId: '' })
  }
  const scopeList = [...scopes.values()]
  const name = (scope: { workspaceId: string; projectId: string }) => {
    const workspace =
      state.rows.workspaces?.find((item) => item.id === scope.workspaceId)
        ?.name ?? scope.workspaceId
    const project =
      state.rows.projects?.find((item) => item.id === scope.projectId)?.name ??
      scope.projectId
    return project ? `${workspace} / ${project}` : workspace
  }
  const usageFor = (
    scope: { workspaceId: string; projectId: string } | undefined,
    metric: QuotaUsage['metric']
  ) =>
    row.quotaUsage?.find(
      (item) =>
        item.workspaceId === scope?.workspaceId &&
        item.projectId === scope?.projectId &&
        item.metric === metric
    )
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type='button'
          className='w-48 rounded-sm text-start focus-visible:outline-ring'
          aria-label={`${row.name} ${t('platformAdmin.table.columns.quotaUsage')}`}
        >
          {scopeList[0] ? (
            <div className='grid grid-cols-3 gap-2 py-1'>
              {(['tokens', 'storage', 'concurrency'] as const).map((metric) => (
                <QuotaMeter
                  key={metric}
                  metric={metric}
                  usage={usageFor(scopeList[0], metric)}
                  t={t}
                  compact
                />
              ))}
            </div>
          ) : (
            <span className='text-xs text-muted-foreground'>—</span>
          )}
        </button>
      </DialogTrigger>
      <DialogContent
        className='max-h-[85dvh] overflow-y-auto sm:max-w-2xl'
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle>
            {row.name} · {t('platformAdmin.table.columns.quotaUsage')}
          </DialogTitle>
        </DialogHeader>
        <div className='flex flex-col gap-6'>
          {(scopeList.length ? scopeList : [undefined]).map((scope, index) => (
            <section key={index} className='flex flex-col gap-4'>
              {scope && <h3 className='text-sm font-medium'>{name(scope)}</h3>}
              <div className='grid gap-x-8 gap-y-5 sm:grid-cols-2'>
                {quotaMetrics.map((metric) => (
                  <QuotaMeter
                    key={metric}
                    metric={metric}
                    usage={usageFor(scope, metric)}
                    t={t}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function ManagementCards({
  definition,
  state,
  rows,
  setDetail,
  t,
  columnLabel,
}: ManagementViewProps) {
  const Icon = definition.icon
  return rows.length > 0 ? (
    <div className='grid gap-4 md:grid-cols-2 2xl:grid-cols-3'>
      {rows.map((row) => {
        const fields = definition.columns
          .filter((column) => !['name', 'status', 'updatedAt'].includes(column))
          .slice(0, 3)
        return (
          <Card
            key={row.id}
            className='group gap-4 py-4 shadow-none transition-colors hover:border-foreground/20'
          >
            <CardHeader className='grid grid-cols-[auto_1fr_auto] items-start gap-3 px-4'>
              <span className='flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground'>
                <Icon className='size-5' aria-hidden='true' />
              </span>
              <button
                type='button'
                className='min-w-0 text-start'
                onClick={() => setDetail(row)}
              >
                <CardTitle className='truncate text-base'>{row.name}</CardTitle>
                <span className='mt-1 block text-xs text-muted-foreground'>
                  {formatDate(row.updatedAt)}
                </span>
              </button>
              <Button
                variant='ghost'
                size='icon'
                className='size-8'
                aria-label={t('platformAdmin.ui.actions')}
                onClick={() => setDetail(row)}
              >
                <MoreHorizontal className='size-4' />
              </Button>
            </CardHeader>
            <CardContent className='grid gap-3 px-4'>
              <StatusBadge status={row.status} t={t} />
              <dl className='grid grid-cols-2 gap-x-4 gap-y-2 text-sm'>
                {fields.map((field) => (
                  <div key={field} className='min-w-0'>
                    <dt className='text-xs text-muted-foreground'>
                      {columnLabel(field)}
                    </dt>
                    <dd className='mt-0.5 truncate font-medium'>
                      {translatedValue(relationValue(row, field, state), t)}
                    </dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        )
      })}
    </div>
  ) : (
    <EmptyRecords t={t} />
  )
}

function ManagementQueue({
  definition,
  state,
  rows,
  setDetail,
  t,
  columnLabel,
}: ManagementViewProps) {
  const Icon = definition.icon
  return rows.length > 0 ? (
    <div className='overflow-hidden rounded-lg border bg-card'>
      {rows.map((row, index) => {
        const fields = definition.columns
          .filter((column) => !['name', 'status', 'updatedAt'].includes(column))
          .slice(0, 3)
        return (
          <button
            key={row.id}
            type='button'
            className={cn(
              'flex w-full items-start gap-3 px-4 py-4 text-start transition-colors hover:bg-muted/40',
              index > 0 && 'border-t'
            )}
            onClick={() => setDetail(row)}
          >
            <span className='mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground'>
              <Icon className='size-4' aria-hidden='true' />
            </span>
            <span className='min-w-0 flex-1'>
              <span className='flex flex-wrap items-center gap-2'>
                <strong className='truncate text-sm'>{row.name}</strong>
                <StatusBadge status={row.status} t={t} />
              </span>
              <span className='mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground'>
                {fields.map((field) => (
                  <span key={field}>
                    {columnLabel(field)}{' '}
                    <b className='font-medium text-foreground'>
                      {translatedValue(relationValue(row, field, state), t)}
                    </b>
                  </span>
                ))}
              </span>
            </span>
            <span className='shrink-0 text-xs text-muted-foreground'>
              {formatDate(row.updatedAt)}
            </span>
          </button>
        )
      })}
    </div>
  ) : (
    <EmptyRecords t={t} />
  )
}

function AllocationGrid({
  rows,
  state,
  setDetail,
  t,
  columnLabel,
}: ManagementViewProps) {
  const metrics = ['concurrency', 'tokens', 'storage', 'budget']
  const maxima = Object.fromEntries(
    metrics.map((metric) => [
      metric,
      Math.max(1, ...rows.map((row) => Number(row.values[metric] ?? 0))),
    ])
  )
  return rows.length > 0 ? (
    <div className='grid gap-4 xl:grid-cols-2'>
      {rows.map((row) => (
        <Card key={row.id} className='gap-5 py-5 shadow-none'>
          <CardHeader className='flex flex-row items-start justify-between gap-3 px-5'>
            <div className='min-w-0'>
              <CardTitle className='truncate text-base'>{row.name}</CardTitle>
              <div className='mt-2 flex flex-wrap gap-1.5'>
                {(row.values.resources ?? '')
                  .split(',')
                  .filter(Boolean)
                  .map((resource) => (
                    <Badge key={resource} variant='outline'>
                      {resource.trim()}
                    </Badge>
                  ))}
              </div>
            </div>
            <Button
              variant='ghost'
              size='icon'
              className='size-8'
              aria-label={t('platformAdmin.ui.actions')}
              onClick={() => setDetail(row)}
            >
              <MoreHorizontal className='size-4' />
            </Button>
          </CardHeader>
          <CardContent className='grid gap-4 px-5'>
            <div className='grid grid-cols-2 gap-3 text-sm'>
              <div>
                <span className='block text-xs text-muted-foreground'>
                  {columnLabel('workspaceId')}
                </span>
                <strong className='font-medium'>
                  {displayValue(relationValue(row, 'workspaceId', state))}
                </strong>
              </div>
              <div>
                <span className='block text-xs text-muted-foreground'>
                  {columnLabel('projectId')}
                </span>
                <strong className='font-medium'>
                  {displayValue(relationValue(row, 'projectId', state))}
                </strong>
              </div>
            </div>
            <div className='grid gap-3 sm:grid-cols-2'>
              {metrics.map((metric) => {
                const value = Number(row.values[metric] ?? 0)
                return (
                  <div key={metric} className='grid gap-1.5'>
                    <div className='flex justify-between gap-2 text-xs'>
                      <span className='text-muted-foreground'>
                        {columnLabel(metric)}
                      </span>
                      <strong className='tabular-nums'>
                        {value.toLocaleString()}
                      </strong>
                    </div>
                    <Progress value={(value / maxima[metric]) * 100} />
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  ) : (
    <EmptyRecords t={t} />
  )
}

function ManagementPagination({
  pageIndex,
  pageCount,
  pageSize,
  start,
  end,
  total,
  setPageIndex,
  setPageSize,
  t,
}: {
  pageIndex: number
  pageCount: number
  pageSize: number
  start: number
  end: number
  total: number
  setPageIndex: Dispatch<SetStateAction<number>>
  setPageSize: Dispatch<SetStateAction<number>>
  t: TFunction
}) {
  const currentPage = pageIndex + 1
  return (
    <div className='mt-auto flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between'>
      <div className='hidden items-center gap-2 text-sm md:flex'>
        <Select
          value={`${pageSize}`}
          onValueChange={(value) => {
            setPageSize(Number(value))
            setPageIndex(0)
          }}
        >
          <SelectTrigger
            className='h-8 w-16'
            aria-label={t('platformAdmin.ui.rowsPerPage')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent side='top'>
            {[10, 20, 30].map((value) => (
              <SelectItem key={value} value={`${value}`}>
                {value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className='text-muted-foreground'>
          {t('platformAdmin.ui.rowsPerPage')}
        </span>
        <span className='hidden text-muted-foreground sm:inline'>
          · {start}–{end} / {total}
        </span>
      </div>
      <div className='flex items-center gap-1'>
        <span className='me-2 hidden text-xs text-muted-foreground md:inline'>
          {t('platformAdmin.ui.pageOf', {
            current: currentPage,
            total: pageCount,
          })}
        </span>
        <Button
          variant='outline'
          size='icon'
          className='size-8 max-sm:hidden'
          onClick={() => setPageIndex(0)}
          disabled={pageIndex === 0}
          aria-label={t('platformAdmin.ui.firstPage')}
        >
          <ChevronsLeft className='size-4' />
        </Button>
        <Button
          variant='outline'
          size='icon'
          className='size-8'
          onClick={() => setPageIndex((value) => Math.max(0, value - 1))}
          disabled={pageIndex === 0}
          aria-label={t('platformAdmin.ui.previousPage')}
        >
          <ChevronLeft className='size-4' />
        </Button>
        {getPageNumbers(currentPage, pageCount).map((page, index) =>
          page === '...' ? (
            <span key={`gap-${index}`} className='px-1 text-muted-foreground'>
              …
            </span>
          ) : (
            <Button
              key={page}
              variant={page === currentPage ? 'default' : 'outline'}
              size='icon'
              className='size-8'
              onClick={() => setPageIndex(Number(page) - 1)}
              aria-current={page === currentPage ? 'page' : undefined}
            >
              {page}
            </Button>
          )
        )}
        <Button
          variant='outline'
          size='icon'
          className='size-8'
          onClick={() =>
            setPageIndex((value) => Math.min(pageCount - 1, value + 1))
          }
          disabled={pageIndex >= pageCount - 1}
          aria-label={t('platformAdmin.ui.nextPage')}
        >
          <ChevronRight className='size-4' />
        </Button>
        <Button
          variant='outline'
          size='icon'
          className='size-8 max-sm:hidden'
          onClick={() => setPageIndex(pageCount - 1)}
          disabled={pageIndex >= pageCount - 1}
          aria-label={t('platformAdmin.ui.lastPage')}
        >
          <ChevronsRight className='size-4' />
        </Button>
      </div>
    </div>
  )
}

function StatusBadge({ status, t }: { status: string; t: TFunction }) {
  const positive = ['active', 'completed', 'approved', 'published'].includes(
    status
  )
  return (
    <Badge variant={positive ? 'secondary' : 'outline'} className='w-fit'>
      {t(`platformAdmin.ui.status.${status}`, { defaultValue: status })}
    </Badge>
  )
}

function EmptyRecords({ t }: { t: TFunction }) {
  return (
    <div className='flex h-40 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground'>
      {t('platformAdmin.ui.noRecords')}
    </div>
  )
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function displayCellValue(
  row: ManagementRow,
  column: string,
  state: ManagementState,
  t: TFunction
) {
  if (column === 'ownership') {
    const memberships = state.memberships.filter(
      (item) => item.userId === row.id
    )
    const workspaceNames = new Map(
      (state.rows.workspaces ?? []).map((item) => [item.id, item.name])
    )
    return (
      memberships
        .map((item) => workspaceNames.get(item.workspaceId) ?? item.workspaceId)
        .join(', ') || '—'
    )
  }
  const value = relationValue(row, column, state)
  if (!value) return '—'
  return ['updatedAt', 'expiresAt', 'createdAt', 'lastLoginAt'].includes(column)
    ? formatDate(value)
    : translatedValue(value, t)
}

const relationCollections: Partial<Record<string, Collection>> = {
  workspaceId: 'workspaces',
  projectId: 'projects',
  ownerId: 'users',
  recipientId: 'users',
  requestedBy: 'users',
  taskId: 'tasks',
  runtimeId: 'runtimes',
  traceId: 'traces',
}

function relationValue(
  row: ManagementRow,
  key: string,
  state: ManagementState
) {
  const value = valueFor(row, key)
  const collection = relationCollections[key]
  return collection
    ? ((state.rows[collection] ?? []).find((item) => item.id === value)?.name ??
        value)
    : value
}

function ManagementForm({
  open,
  row,
  collection,
  currentUser,
  state,
  onClose,
  onSave,
}: {
  open: boolean
  row: ManagementRow | null
  collection: Collection
  currentUser: string
  state: ManagementState
  onClose: () => void
  onSave: (row: ManagementRow) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<ManagementRow | null>(row)
  if (!draft) return null
  const def = definitions[collection]
  const set = (key: string, value: string) =>
    setDraft(
      value === undefined
        ? draft
        : {
            ...draft,
            name: key === 'name' ? value : draft.name,
            values:
              key === 'name' ? draft.values : { ...draft.values, [key]: value },
          }
    )
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent
        className='max-h-[90vh] overflow-y-auto sm:max-w-xl'
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle>
            {draft.name
              ? t('platformAdmin.ui.edit')
              : t('platformAdmin.ui.create')}
          </DialogTitle>
        </DialogHeader>
        <div className='grid gap-4 py-2 sm:grid-cols-2'>
          {def.fields.map((field) => (
            <FieldEditor
              key={field.key}
              field={field}
              value={valueFor(draft, field.key)}
              currentUser={currentUser}
              state={state}
              workspaceId={draft.values.workspaceId ?? ''}
              onChange={(value) => set(field.key, value)}
            />
          ))}
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            {t('platformAdmin.ui.cancel')}
          </Button>
          <Button onClick={() => onSave(draft)}>
            {t('platformAdmin.ui.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
function FieldEditor({
  field,
  value,
  currentUser,
  state,
  workspaceId,
  onChange,
}: {
  field: FieldDefinition
  value: string
  currentUser: string
  state: ManagementState
  workspaceId: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const label = t(`platformAdmin.table.columns.${field.key}`, {
    defaultValue: formatColumn(field.key),
  })
  const relationRows =
    field.type === 'workspace'
      ? (state.rows.workspaces ?? [])
      : field.type === 'project'
        ? (state.rows.projects ?? []).filter(
            (row) => !workspaceId || row.values.workspaceId === workspaceId
          )
        : field.type === 'user'
          ? (state.rows.users ?? [])
          : []
  return (
    <div
      className={cn('grid gap-2', field.type === 'textarea' && 'sm:col-span-2')}
    >
      <Label htmlFor={field.key}>
        {label}
        {field.required && <span aria-hidden='true'> *</span>}
      </Label>
      {field.type === 'textarea' ? (
        <Textarea
          id={field.key}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : ['workspace', 'project', 'user'].includes(field.type ?? '') ? (
        <Select
          value={value || (field.required ? undefined : '__none__')}
          onValueChange={(next) => onChange(next === '__none__' ? '' : next)}
        >
          <SelectTrigger id={field.key} className='w-full'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {!field.required && <SelectItem value='__none__'>—</SelectItem>}
            {relationRows
              .filter((row) => row.status === 'active')
              .map((row) => (
                <SelectItem key={row.id} value={row.id}>
                  {row.name}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      ) : field.type === 'select' ? (
        <Select value={value || field.options?.[0]} onValueChange={onChange}>
          <SelectTrigger id={field.key}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {field.options?.map((option) => (
              <SelectItem key={option} value={option}>
                {translatedValue(option, t)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          id={field.key}
          type={
            field.type === 'number'
              ? 'number'
              : field.type === 'email'
                ? 'email'
                : field.type === 'url'
                  ? 'url'
                  : 'text'
          }
          value={value || (field.key === 'ownerId' ? currentUser : '')}
          onChange={(event) => onChange(event.target.value)}
          min={field.min}
          max={field.max}
        />
      )}
    </div>
  )
}

function DetailSheet({
  row,
  collection,
  state,
  store,
  onClose,
  onEdit,
  onAction,
  onProjectEdit,
}: {
  row: ManagementRow | null
  collection: Collection
  state: ManagementState
  store: ManagementStore
  onClose: () => void
  onEdit: () => void
  onAction: (action: Action) => void
  onProjectEdit: (project: ManagementRow) => void
}) {
  const { t } = useTranslation()
  if (!row) return null
  const actions = availableActions(collection, row).filter(
    () => collection !== 'users' || row.id !== store.userId
  )
  const isUser = collection === 'users'
  const Root = isUser ? Dialog : Sheet
  const Content = isUser ? DialogContent : SheetContent
  const Header = isUser ? DialogHeader : SheetHeader
  const Title = isUser ? DialogTitle : SheetTitle
  const Footer = isUser ? DialogFooter : SheetFooter
  return (
    <Root open={!!row} onOpenChange={(value) => !value && onClose()}>
      <Content
        className={
          isUser
            ? 'max-h-[85dvh] overflow-y-auto sm:max-w-2xl'
            : 'w-full overflow-y-auto sm:max-w-lg'
        }
        aria-describedby={undefined}
      >
        <Header>
          <Title>{row.name || t('platformAdmin.ui.details')}</Title>
          <div>
            <Badge>
              {t(`platformAdmin.ui.status.${row.status}`, {
                defaultValue: row.status,
              })}
            </Badge>
          </div>
        </Header>
        {collection === 'users' ? (
          <UserDetailOverview row={row} state={state} store={store} t={t} />
        ) : (
          <div className='grid gap-3 px-4 text-sm'>
            {Object.entries(row.values).map(([key]) => (
              <div
                key={key}
                className='grid grid-cols-[8rem_1fr] gap-3 border-b py-2'
              >
                <span className='text-muted-foreground'>
                  {t(`platformAdmin.table.columns.${key}`, {
                    defaultValue: formatColumn(key),
                  })}
                </span>
                <span className='break-words'>
                  {translatedValue(relationValue(row, key, state), t)}
                </span>
              </div>
            ))}
            <div className='grid grid-cols-[8rem_1fr] gap-3 border-b py-2'>
              <span className='text-muted-foreground'>
                {t('platformAdmin.ui.updated')}
              </span>
              <span>
                {new Intl.DateTimeFormat(undefined, {
                  dateStyle: 'medium',
                  timeStyle: 'short',
                }).format(new Date(row.updatedAt))}
              </span>
            </div>
            {collection === 'workspaces' && (
              <WorkspacePanel
                workspaceId={row.id}
                state={state}
                store={store}
                onProjectEdit={onProjectEdit}
              />
            )}
          </div>
        )}
        <Footer className='flex-row flex-wrap justify-end'>
          {actions.includes('edit') && (
            <Button variant='outline' onClick={onEdit}>
              {t('platformAdmin.ui.edit')}
            </Button>
          )}
          {actions
            .filter((action) => action !== 'edit')
            .map((action) => (
              <Button
                key={action}
                variant={
                  ['delete', 'disable', 'reject'].includes(action)
                    ? 'destructive'
                    : 'outline'
                }
                onClick={() => onAction(action)}
              >
                {t(`platformAdmin.ui.action.${action}`, {
                  defaultValue: action,
                })}
              </Button>
            ))}
        </Footer>
      </Content>
    </Root>
  )
}

function UserDetailOverview({
  row,
  state,
  store,
  t,
}: {
  row: ManagementRow
  state: ManagementState
  store: ManagementStore
  t: TFunction
}) {
  return (
    <div className='grid gap-4 px-4'>
      <div className='grid gap-4 sm:grid-cols-2'>
        <Card className='gap-3 py-4 shadow-none'>
          <CardHeader className='px-4'>
            <CardTitle className='text-sm'>{row.name}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-2 px-4 text-sm'>
            <div className='flex items-center justify-between gap-3'>
              <span className='text-muted-foreground'>
                {t('platformAdmin.table.columns.email')}
              </span>
              <span className='truncate'>{row.values.email || '—'}</span>
            </div>
            <div className='flex items-center justify-between gap-3'>
              <span className='text-muted-foreground'>
                {t('platformAdmin.table.columns.role')}
              </span>
              <span>{translatedValue(row.values.role ?? '', t)}</span>
            </div>
          </CardContent>
        </Card>
        <Card className='gap-3 py-4 shadow-none'>
          <CardHeader className='px-4'>
            <CardTitle className='text-sm'>
              {t('platformAdmin.table.columns.ownership')}
            </CardTitle>
          </CardHeader>
          <CardContent className='px-4 text-sm text-muted-foreground'>
            {displayCellValue(row, 'ownership', state, t)}
          </CardContent>
        </Card>
      </div>
      <Card className='gap-3 py-4 shadow-none'>
        <CardHeader className='px-4'>
          <CardTitle className='text-sm'>
            {t('platformAdmin.table.columns.quotaUsage')}
          </CardTitle>
        </CardHeader>
        <CardContent className='px-4'>
          <UserQuotaUsage row={row} state={state} t={t} />
        </CardContent>
      </Card>
      <MembershipPanel userId={row.id} state={state} store={store} />
    </div>
  )
}

function MembershipPanel({
  userId,
  state,
  store,
}: {
  userId: string
  state: ManagementState
  store: ManagementStore
}) {
  const { t } = useTranslation()
  const memberships = state.memberships.filter((item) => item.userId === userId)
  const [draft, setDraft] = useState<Membership | null>(null)
  const workspaceName = (id: string) =>
    state.rows.workspaces?.find((row) => row.id === id)?.name ?? id
  const save = () => {
    if (!draft) return
    try {
      store.saveMembership(draft)
      setDraft(null)
      toast.success(t('platformAdmin.ui.saved'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToSave')
      )
    }
  }
  const remove = (id: string) => {
    try {
      store.removeMembership(id)
      toast.success(t('platformAdmin.ui.updatedToast'))
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : t('platformAdmin.ui.unableToUpdate')
      )
    }
  }
  return (
    <section className='mt-4 grid gap-3 border-t pt-4'>
      <div className='flex items-center justify-between'>
        <h3 className='font-medium'>{t('platformAdmin.ui.memberships')}</h3>
        <Button
          variant='outline'
          size='sm'
          onClick={() =>
            setDraft({
              id: crypto.randomUUID(),
              userId,
              workspaceId: state.rows.workspaces?.[0]?.id ?? '',
              role: 'viewer',
            })
          }
        >
          {t('platformAdmin.ui.add')}
        </Button>
      </div>
      {memberships.map((item) => (
        <div key={item.id} className='grid gap-1 rounded-md border p-3'>
          <div className='flex items-center justify-between gap-2'>
            <span>{workspaceName(item.workspaceId)}</span>
            <Badge variant='outline'>{translatedValue(item.role, t)}</Badge>
          </div>
          <div className='flex items-center gap-1'>
            <Button
              variant='ghost'
              size='sm'
              className='w-fit'
              onClick={() => setDraft(item)}
            >
              {t('platformAdmin.ui.edit')}
            </Button>
            <Button
              variant='ghost'
              size='icon'
              className='size-8 text-destructive'
              onClick={() => remove(item.id)}
              aria-label={t('platformAdmin.ui.removeMembership')}
            >
              <X className='size-4' />
            </Button>
          </div>
        </div>
      ))}
      {memberships.length === 0 && (
        <div className='rounded-md border border-dashed p-4 text-muted-foreground'>
          {t('platformAdmin.ui.noRecords')}
        </div>
      )}
      {draft && (
        <div className='grid gap-3 rounded-md bg-muted/40 p-3'>
          <Select
            value={draft.workspaceId}
            onValueChange={(workspaceId) => setDraft({ ...draft, workspaceId })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {state.rows.workspaces?.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={draft.role}
            onValueChange={(role) =>
              setDraft({ ...draft, role: role as Membership['role'] })
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(['owner', 'admin', 'operator', 'viewer'] as const).map(
                (role) => (
                  <SelectItem key={role} value={role}>
                    {translatedValue(role, t)}
                  </SelectItem>
                )
              )}
            </SelectContent>
          </Select>
          <div className='flex justify-end gap-2'>
            <Button variant='ghost' onClick={() => setDraft(null)}>
              {t('platformAdmin.ui.cancel')}
            </Button>
            <Button onClick={save}>{t('platformAdmin.ui.save')}</Button>
          </div>
        </div>
      )}
    </section>
  )
}

function WorkspacePanel({
  workspaceId,
  state,
  store,
  onProjectEdit,
}: {
  workspaceId: string
  state: ManagementState
  store: ManagementStore
  onProjectEdit: (project: ManagementRow) => void
}) {
  const { t } = useTranslation()
  const projects =
    state.rows.projects?.filter(
      (row) => row.values.workspaceId === workspaceId
    ) ?? []
  const members = new Set(
    state.memberships
      .filter((item) => item.workspaceId === workspaceId)
      .map((item) => item.userId)
  )
  const createProject = () => {
    onProjectEdit({
      ...initialRow('projects', store.userId),
      values: { workspaceId, createdByUserId: store.userId },
    })
  }
  return (
    <section className='mt-4 grid gap-3 border-t pt-4'>
      <div className='flex items-center justify-between gap-2'>
        <h3 className='font-medium'>
          {t('platformAdmin.ui.projectsAndMembers')}
        </h3>
        <Button variant='outline' size='sm' onClick={createProject}>
          <Plus className='size-4' aria-hidden='true' />
          {t('platformAdmin.ui.add')}
        </Button>
      </div>
      <div className='grid gap-2 sm:grid-cols-2'>
        {projects.map((project) => (
          <button
            key={project.id}
            type='button'
            className='rounded-md border p-3 text-start transition-colors hover:bg-muted/40'
            onClick={() => onProjectEdit(project)}
          >
            <div className='font-medium'>{project.name}</div>
            <div className='text-xs text-muted-foreground'>
              {t(`platformAdmin.ui.status.${project.status}`, {
                defaultValue: project.status,
              })}
            </div>
          </button>
        ))}
      </div>
      <div className='text-sm text-muted-foreground'>
        {t('platformAdmin.ui.workspaceMemberCount', { count: members.size })}
      </div>
    </section>
  )
}

export function AdminRoutePage({ path }: { path: string }) {
  const collection = routes[path]
  if (!collection)
    return <AdminManagementPage collection='audit' title='Not found' />
  return <AdminManagementPage collection={collection} />
}
