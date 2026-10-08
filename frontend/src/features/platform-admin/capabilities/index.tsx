import { useEffect, useMemo, useRef, useState } from 'react'
import { useSuspenseQuery } from '@tanstack/react-query'
import {
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
} from '@tanstack/react-table'
import {
  Bot,
  ArrowDownAZ,
  ArrowUpAZ,
  Download,
  MoreHorizontal,
  Plus,
  PlugZap,
  Search,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Wrench,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { currentUserQueryOptions } from '@/api/auth'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from '@/components/ui/sheet'
import { DataTablePagination } from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '../page-heading'
import {
  capabilityDraftSchema,
  createDraft,
  createNode,
  exportDraft,
  type CapabilityDraft,
  type CapabilityKind,
} from './drafts'
import { ExpertCanvas } from './expert-canvas'
import { ResourceForm } from './resource-form'

const icons = { expert: Bot, mcp: PlugZap, tool: Wrench, skill: Sparkles }
const capabilityColumns: ColumnDef<CapabilityDraft>[] = []

export function CapabilityCenter({ kind }: { kind: CapabilityKind }) {
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  return (
    <Management
      key={`${user.user_id}-${kind}`}
      kind={kind}
      userId={user.user_id}
    />
  )
}

function Management({
  kind,
  userId,
}: {
  kind: CapabilityKind
  userId: string
}) {
  const { t, i18n } = useTranslation()
  const tr = (key: string) => t(`capabilityCenter.${key}`)
  const storageKey = `opsmesh.capability-drafts.${userId}`
  const [drafts, setDrafts] = useState<CapabilityDraft[]>(() => {
    try {
      const raw: unknown = JSON.parse(
        sessionStorage.getItem(storageKey) ?? '[]'
      )
      const result = capabilityDraftSchema.array().max(100).safeParse(raw)
      return result.success ? result.data : []
    } catch {
      return []
    }
  })
  const [search, setSearch] = useState('')
  const [scope, setScope] = useState('all')
  const [sort, setSort] = useState('asc')
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  })
  const [editing, setEditing] = useState<CapabilityDraft | null>(null)
  const [detail, setDetail] = useState<CapabilityDraft | null>(null)
  const [deleting, setDeleting] = useState<CapabilityDraft | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const Icon = icons[kind]
  const rows = useMemo(
    () =>
      drafts
        .filter(
          (item) =>
            item.kind === kind &&
            (scope === 'all' || item.scope === scope) &&
            `${item.name} ${item.description} ${item.workspace?.name ?? ''} ${item.owner?.name ?? ''}`
              .toLocaleLowerCase()
              .includes(search.toLocaleLowerCase())
        )
        .sort((a, b) =>
          sort === 'asc'
            ? a.name.localeCompare(b.name, i18n.language)
            : b.name.localeCompare(a.name, i18n.language)
        ),
    [drafts, kind, scope, search, sort, i18n.language]
  )
  const pageCount = Math.max(1, Math.ceil(rows.length / pagination.pageSize))
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: capabilityColumns,
    state: { pagination },
    onPaginationChange: setPagination,
    autoResetPageIndex: false,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })
  const visibleRows = table.getRowModel().rows.map((row) => row.original)
  useEffect(() => {
    if (pagination.pageIndex >= pageCount) {
      setPagination((current) => ({
        ...current,
        pageIndex: Math.max(0, pageCount - 1),
      }))
    }
  }, [pageCount, pagination.pageIndex])
  const resetPagination = () =>
    setPagination((current) =>
      current.pageIndex === 0 ? current : { ...current, pageIndex: 0 }
    )
  const persist = (next: CapabilityDraft[]) => {
    try {
      if (next.length > 100) throw new Error('draft limit')
      sessionStorage.setItem(storageKey, JSON.stringify(next))
      setDrafts(next)
      return true
    } catch {
      toast.error(tr('storageFailed'))
      return false
    }
  }
  const save = (draft: CapabilityDraft) => {
    const result = capabilityDraftSchema.safeParse(draft)
    if (!result.success) {
      toast.error(tr('invalidDraft'))
      return false
    }
    const next = [result.data, ...drafts.filter((item) => item.id !== draft.id)]
    return persist(next)
  }
  const create = () => {
    const draft = createDraft(kind)
    if (kind === 'expert') {
      const start = createNode('start', tr('nodes.start'), { x: 0, y: 100 })
      const agent = createNode('agent', tr('nodes.agent'), { x: 300, y: 100 })
      const end = createNode('end', tr('nodes.end'), { x: 600, y: 100 })
      draft.graph = {
        nodes: [start, agent, end],
        edges: [
          {
            id: crypto.randomUUID(),
            source: start.id,
            target: agent.id,
            sourceHandle: 'out',
            targetHandle: 'in',
            data: { kind: 'flow' },
          },
          {
            id: crypto.randomUUID(),
            source: agent.id,
            target: end.id,
            sourceHandle: 'out',
            targetHandle: 'in',
            data: { kind: 'flow' },
          },
        ],
      }
    }
    setEditing(draft)
  }
  const importFile = async (file?: File) => {
    if (!file) return
    try {
      if (file.size > 1_000_000) throw new Error('file limit')
      const draft = capabilityDraftSchema.parse(JSON.parse(await file.text()))
      if (draft.kind !== kind) throw new Error('wrong kind')
      // Imports never overwrite another draft or imply ownership of server resources.
      const next = {
        ...draft,
        id: crypto.randomUUID(),
        updatedAt: new Date().toISOString(),
      }
      if (save(next)) setEditing(next)
    } catch {
      toast.error(tr('invalidDraft'))
    }
  }
  const content = (
    <div className='flex h-full min-h-0 flex-1 flex-col gap-4'>
      <div className='flex flex-wrap items-center gap-3'>
        <div className='relative min-w-44 flex-1 sm:max-w-80'>
          <Search
            className='pointer-events-none absolute start-3 top-2.5 size-4 text-muted-foreground'
            aria-hidden
          />
          <Input
            aria-label={tr('search')}
            placeholder={tr('search')}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              resetPagination()
            }}
            className='ps-9'
          />
        </div>
        <Select
          value={scope}
          onValueChange={(value) => {
            setScope(value)
            resetPagination()
          }}
        >
          <SelectTrigger aria-label={tr('scope')} className='w-36'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {['all', 'private', 'team', 'workspace', 'public'].map(
                (value) => (
                  <SelectItem key={value} value={value}>
                    {tr(value)}
                  </SelectItem>
                )
              )}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          value={sort}
          onValueChange={(value) => {
            setSort(value)
            resetPagination()
          }}
        >
          <SelectTrigger className='ms-auto w-16' aria-label={tr('sort')}>
            <SelectValue>
              <SlidersHorizontal className='size-4' aria-hidden />
            </SelectValue>
          </SelectTrigger>
          <SelectContent align='end'>
            <SelectGroup>
              <SelectItem value='asc'>
                <ArrowUpAZ className='size-4' aria-hidden />
                {t('apps.ascending')}
              </SelectItem>
              <SelectItem value='desc'>
                <ArrowDownAZ className='size-4' aria-hidden />
                {t('apps.descending')}
              </SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        <div className='flex shrink-0 items-center gap-2'>
          <input
            ref={fileInput}
            type='file'
            accept='.json,application/json'
            className='hidden'
            aria-label={tr('import')}
            onChange={(event) => {
              void importFile(event.target.files?.[0])
              event.target.value = ''
            }}
          />
          <Button variant='outline' onClick={() => fileInput.current?.click()}>
            <Upload data-icon='inline-start' />
            {tr('import')}
          </Button>
          <Button onClick={create}>
            <Plus data-icon='inline-start' />
            {t('capabilityCenter.create', { kind: tr(kind) })}
          </Button>
        </div>
      </div>
      <Separator />
      <ul className='grid min-h-0 flex-1 content-start gap-4 overflow-auto md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4'>
        {visibleRows.map((row) => (
          <li key={row.id} className='min-w-0'>
            <Card className='relative h-full gap-0 overflow-hidden rounded-xl py-0 shadow-none transition-colors hover:border-foreground/25'>
              <Button
                variant='ghost'
                className='absolute inset-0 h-full w-full rounded-xl focus-visible:ring-inset'
                aria-label={`${tr('details')}: ${row.name}`}
                onClick={() => setDetail(row)}
              />
              <CardHeader className='pointer-events-none relative gap-4 p-5 pb-3'>
                <div className='flex items-center justify-between gap-2'>
                  <div className='flex size-10 items-center justify-center rounded-lg bg-muted p-2'>
                    <Icon className='size-6' aria-hidden />
                  </div>
                  <div className='pointer-events-auto flex items-center gap-1'>
                    <Button
                      variant='outline'
                      size='sm'
                      onClick={() => setEditing(structuredClone(row))}
                    >
                      {tr('edit')}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant='ghost'
                          size='icon'
                          aria-label={`${tr('actions')}: ${row.name}`}
                        >
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align='end'>
                        <DropdownMenuGroup>
                          <DropdownMenuItem onSelect={() => exportDraft(row)}>
                            <Download />
                            {tr('export')}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant='destructive'
                            onSelect={() => setDeleting(row)}
                          >
                            <Trash2 />
                            {tr('deleteDraft')}
                          </DropdownMenuItem>
                        </DropdownMenuGroup>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
                <CardTitle className='truncate text-base'>{row.name}</CardTitle>
              </CardHeader>
              <CardContent className='pointer-events-none relative flex flex-col gap-3 px-5 pb-4'>
                <CardDescription className='line-clamp-2 min-h-10 leading-5'>
                  {row.description}
                </CardDescription>
                <Badge variant='secondary' className='w-fit'>
                  {tr(row.scope)}
                </Badge>
                {(row.workspace?.name || row.owner?.name) && (
                  <dl className='grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground'>
                    {row.workspace?.name && (
                      <>
                        <dt>{tr('workspace')}</dt>
                        <dd className='truncate'>{row.workspace?.name}</dd>
                      </>
                    )}
                    {row.owner?.name && (
                      <>
                        <dt>{tr('owner')}</dt>
                        <dd className='truncate'>{row.owner?.name}</dd>
                      </>
                    )}
                  </dl>
                )}
              </CardContent>
              <CardFooter className='pointer-events-none relative mt-auto border-t px-5 py-3 text-xs text-muted-foreground'>
                <time dateTime={row.updatedAt}>
                  {new Intl.DateTimeFormat(i18n.language, {
                    dateStyle: 'short',
                    timeStyle: 'short',
                  }).format(new Date(row.updatedAt))}
                </time>
              </CardFooter>
            </Card>
          </li>
        ))}
      </ul>
      {!rows.length && (
        <Empty className='min-h-48'>
          <EmptyHeader>
            <EmptyMedia variant='icon'>
              <Icon />
            </EmptyMedia>
            <EmptyTitle className='sr-only'>
              {tr(search || scope !== 'all' ? 'noMatches' : 'noDrafts')}
            </EmptyTitle>
          </EmptyHeader>
        </Empty>
      )}
      <DataTablePagination table={table} className='mt-auto border-t pt-4' />
    </div>
  )
  return (
    <Main fixed fluid={editing?.kind === 'expert'}>
      {editing?.kind === 'expert' ? (
        <ExpertCanvas
          key={editing.id}
          initial={editing}
          onSave={save}
          onClose={() => setEditing(null)}
        />
      ) : (
        <>
          <PlatformPageHeading title={tr(kind)} />
          {content}
          {editing && (
            <ResourceForm
              key={editing.id}
              initial={editing}
              onClose={() => setEditing(null)}
              onSave={(draft) => {
                if (save(draft)) setEditing(null)
              }}
            />
          )}
        </>
      )}
      <Sheet open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <SheetContent
          className='w-full overflow-y-auto sm:max-w-lg'
          aria-describedby={undefined}
        >
          <SheetHeader>
            <SheetTitle>{detail?.name}</SheetTitle>
            {detail?.description && (
              <SheetDescription className='whitespace-pre-wrap'>
                {detail.description}
              </SheetDescription>
            )}
          </SheetHeader>
          {detail && (
            <div className='flex flex-col gap-6 px-4 pb-6'>
              <dl className='grid grid-cols-[auto_1fr] gap-x-8 gap-y-3 text-sm'>
                <dt className='text-muted-foreground'>{tr('type')}</dt>
                <dd>{tr(detail.kind)}</dd>
                <dt className='text-muted-foreground'>{tr('scope')}</dt>
                <dd>{tr(detail.scope)}</dd>
                <dt className='text-muted-foreground'>{tr('workspace')}</dt>
                <dd>{detail.workspace?.name ?? '—'}</dd>
                <dt className='text-muted-foreground'>{tr('owner')}</dt>
                <dd>{detail.owner?.name ?? '—'}</dd>
                {detail.kind === 'expert' && (
                  <>
                    <dt className='text-muted-foreground'>
                      {tr('nodes.agent')}
                    </dt>
                    <dd>
                      {
                        detail.graph.nodes.filter(
                          (node) => node.data.kind === 'agent'
                        ).length
                      }
                    </dd>
                    <dt className='text-muted-foreground'>
                      {tr('connections')}
                    </dt>
                    <dd>{detail.graph.edges.length}</dd>
                  </>
                )}
                {detail.kind === 'mcp' && (
                  <>
                    <dt className='text-muted-foreground'>{tr('transport')}</dt>
                    <dd>{tr(detail.configuration.transport)}</dd>
                  </>
                )}
                {detail.configuration.endpoint && (
                  <>
                    <dt className='text-muted-foreground'>{tr('endpoint')}</dt>
                    <dd className='break-all'>
                      {detail.configuration.endpoint}
                    </dd>
                  </>
                )}
                <dt className='text-muted-foreground'>{tr('risk')}</dt>
                <dd>{tr(detail.configuration.risk)}</dd>
                <dt className='text-muted-foreground'>{tr('updatedAt')}</dt>
                <dd>
                  {new Intl.DateTimeFormat(i18n.language, {
                    dateStyle: 'medium',
                    timeStyle: 'short',
                  }).format(new Date(detail.updatedAt))}
                </dd>
              </dl>
              {detail.configuration.instructions && (
                <section className='flex flex-col gap-2'>
                  <h3 className='text-sm font-medium'>{tr('skillContent')}</h3>
                  <p className='text-sm whitespace-pre-wrap text-muted-foreground'>
                    {detail.configuration.instructions}
                  </p>
                </section>
              )}
              {(['inputs', 'outputs'] as const).map(
                (direction) =>
                  detail.configuration[direction].length > 0 && (
                    <section key={direction} className='flex flex-col gap-2'>
                      <h3 className='text-sm font-medium'>{tr(direction)}</h3>
                      {detail.configuration[direction].map((parameter) => (
                        <div
                          key={parameter.id}
                          className='flex flex-col gap-1 rounded-md border p-3 text-sm'
                        >
                          <div className='flex items-center justify-between gap-2'>
                            <span>{parameter.name}</span>
                            <Badge variant='secondary'>
                              {tr(parameter.type)}
                            </Badge>
                          </div>
                          {parameter.description && (
                            <p className='text-muted-foreground'>
                              {parameter.description}
                            </p>
                          )}
                        </div>
                      ))}
                    </section>
                  )
              )}
            </div>
          )}
          <SheetFooter>
            <Button
              onClick={() => {
                if (detail) setEditing(structuredClone(detail))
                setDetail(null)
              }}
            >
              {tr('edit')}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
      <AlertDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
      >
        <AlertDialogContent aria-describedby={undefined}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {tr('deleteDraft')} · {deleting?.name}
            </AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tr('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                deleting &&
                persist(drafts.filter((item) => item.id !== deleting.id))
              }
            >
              {tr('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Main>
  )
}
