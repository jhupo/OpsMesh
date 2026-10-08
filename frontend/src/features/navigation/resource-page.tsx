import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from '@tanstack/react-router'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  readNavigationResource,
  decideNavigationApproval,
  saveNotificationPreference,
} from '@/api/navigation-resources'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Empty, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { Main } from '@/components/layout/main'
import {
  adminDestinations,
  workspaceDestinations,
  workspaceDatasetAction,
} from '@/features/navigation/catalog'
import { PlatformPageHeading } from '@/features/platform-admin/page-heading'

type Row = Record<string, unknown>
const isRow = (value: unknown): value is Row =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
function rowsOf(value: unknown): Row[] {
  if (Array.isArray(value)) return value.filter(isRow)
  if (!isRow(value)) return []
  for (const key of ['items', 'runs', 'groups', 'workers', 'entries'])
    if (Array.isArray(value[key])) return value[key].filter(isRow)
  return Object.keys(value).length ? [value] : []
}
// Never render nested secrets in evidence/details, even if a future endpoint adds a field.
function safe(value: unknown, key = ''): unknown {
  if (/(password|secret|token$|api.?key|private_key|authorization)/i.test(key))
    return '••••'
  if (Array.isArray(value)) return value.map((item) => safe(item))
  if (isRow(value))
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, safe(v, k)])
    )
  return value
}
function display(value: unknown, key: string) {
  const clean = safe(value, key)
  return clean === null || clean === undefined
    ? '—'
    : typeof clean === 'object'
      ? JSON.stringify(clean)
      : String(clean)
}

export function NavigationResourcePage({
  view,
  admin = false,
}: {
  view: string
  admin?: boolean
}) {
  const { t } = useTranslation()
  const config = (admin ? adminDestinations : workspaceDestinations)[view]
  const workspace = useWorkspace()
  if (!config)
    return (
      <Main>
        <Empty>
          <EmptyHeader>
            <EmptyTitle>{t('common.not_found')}</EmptyTitle>
          </EmptyHeader>
        </Empty>
      </Main>
    )
  if (!admin && !workspace.activeWorkspace)
    return (
      <Main>
        <Empty className='min-h-48 flex-none'>
          <EmptyHeader>
            <EmptyTitle>{t('workspaceConsole.noWorkspace')}</EmptyTitle>
          </EmptyHeader>
        </Empty>
      </Main>
    )
  if (!admin && workspace.activeWorkspace && workspace.accessPending)
    return (
      <Main>
        <Skeleton className='h-64 w-full' />
      </Main>
    )
  if (
    !admin &&
    workspace.activeWorkspace &&
    (workspace.accessError ||
      !workspace.access?.allowed_actions.includes(config.action))
  )
    return (
      <Main>
        <Alert variant='destructive'>
          <AlertDescription>
            {workspace.accessError?.message ?? t('navigation.denied')}
          </AlertDescription>
        </Alert>
      </Main>
    )
  return (
    <ResourceContent
      key={`${admin}-${view}-${workspace.activeWorkspace?.id}`}
      view={view}
      admin={admin}
    />
  )
}

function ResourceContent({ view, admin }: { view: string; admin: boolean }) {
  const { t } = useTranslation()
  const { activeWorkspace, access } = useWorkspace()
  const config = (admin ? adminDestinations : workspaceDestinations)[view]
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const search = new URLSearchParams(location.searchStr)
  const choices = [config.path, ...config.tabs].filter(
    (path) =>
      admin || access?.allowed_actions.includes(workspaceDatasetAction(path))
  )
  const tab = Math.max(
    0,
    Math.min(choices.length - 1, Number(search.get('tab')) || 0)
  )
  const page = Math.max(0, Math.floor(Number(search.get('offsetPage')) || 0))
  const selected = search.get('selected') ?? ''
  const [detail, setDetail] = useState<Row | null>(null)
  const [decision, setDecision] = useState<{
    row: Row
    action: 'approve' | 'reject'
  } | null>(null)
  const [reason, setReason] = useState('')
  const artifactHistory = !admin && view === 'artifacts' && tab > 0
  const needsSelection =
    view === 'sessions' || (admin && view === 'scheduler') || artifactHistory
  const selectionLabel = artifactHistory
    ? 'navigation.selectTask'
    : admin
      ? 'navigation.selectWorkspace'
      : 'navigation.selectAgent'
  const workPackage = search.get('workPackage') ?? ''
  const pickerPath = admin
    ? '/admin/workspaces?limit=100'
    : `/workspaces/${activeWorkspace?.id}/${artifactHistory ? 'tasks' : 'agents'}?limit=100`
  const picker = useQuery({
    queryKey: ['navigation-picker', pickerPath],
    queryFn: ({ signal }) => readNavigationResource(pickerPath, signal),
    enabled: needsSelection && (admin || Boolean(activeWorkspace)),
    retry: false,
  })
  const pickerRows = rowsOf(picker.data)
  let relative = choices[tab]
  if (view === 'sessions')
    relative = `agents/${encodeURIComponent(selected)}/sessions`
  if (admin)
    relative = relative.replace('{workspace}', encodeURIComponent(selected))
  const base = admin
    ? `/admin/${relative}`
    : relative.startsWith('/')
      ? relative
      : `/workspaces/${activeWorkspace?.id}${relative ? `/${relative}` : ''}`
  const params = new URLSearchParams(base.split('?')[1])
  params.set('limit', '20')
  params.set('offset', String(page * 20))
  if (view === 'market' && !admin && tab === 0)
    params.set('listing_type', search.get('listingType') ?? 'skill')
  if (artifactHistory) {
    params.set('task_id', selected)
    if (tab === 1) params.set('work_package_id', workPackage)
  }
  const path = `${base.split('?')[0]}?${params}`
  const enabled =
    (admin || Boolean(activeWorkspace)) &&
    (!needsSelection || Boolean(selected)) &&
    (!artifactHistory || tab !== 1 || Boolean(workPackage.trim()))
  const resource = useQuery({
    queryKey: ['navigation-resource', path],
    queryFn: ({ signal }) => readNavigationResource(path, signal),
    enabled,
    retry: false,
  })
  const rows = rowsOf(resource.data)
  const keys = Array.from(new Set(rows.flatMap((row) => Object.keys(row))))
  const preferred = [
    'name',
    'title',
    'display_name',
    'status',
    'role',
    'workspace_id',
    'created_at',
  ]
  const columns = [
    ...preferred.filter((key) => keys.includes(key)),
    ...keys.filter((key) => !preferred.includes(key)),
  ].slice(0, 5)
  const total =
    isRow(resource.data) && typeof resource.data.total === 'number'
      ? resource.data.total
      : null
  const hasNext =
    total !== null
      ? (page + 1) * 20 < total
      : isRow(resource.data) && resource.data.has_more === true
  const updateSearch = (updates: Record<string, string>) => {
    const next: Record<string, string> = Object.fromEntries(search)
    Object.assign(next, updates)
    void navigate({ to: location.pathname, search: next as never })
  }
  const decide = useMutation({
    mutationFn: () =>
      decideNavigationApproval(
        String(decision?.row.workspace_id ?? activeWorkspace?.id),
        String(decision?.row.id),
        decision!.action,
        reason.trim(),
        admin
      ),
    onSuccess: () => {
      setDecision(null)
      setReason('')
      void queryClient.invalidateQueries({ queryKey: ['navigation-resource'] })
    },
  })
  const preference = useMutation({
    mutationFn: ({ key, value }: { key: string; value: boolean }) =>
      saveNotificationPreference(activeWorkspace!.id, key, value),
    onSuccess: () => {
      void resource.refetch()
    },
  })
  const approvals = view === 'approvals' || (admin && view === 'reviews')
  const label = (key: string) =>
    t(`navigation.fields.${key}`, { defaultValue: key.replace(/_/g, ' ') })
  const tabLabel = (value: string) =>
    t(`navigation.datasets.${value}`, {
      defaultValue: t(`navigation.${config.titleKey}`),
    })
  return (
    <Main className='flex min-w-0 flex-1 flex-col gap-4'>
      <PlatformPageHeading
        title={t(`navigation.${config.titleKey}`)}
        actions={
          <Button
            variant='outline'
            size='icon'
            aria-label={t('workspaceConsole.refresh')}
            disabled={!enabled || resource.isFetching}
            onClick={() => void resource.refetch()}
          >
            <RefreshCw />
          </Button>
        }
      />
      {view === 'market' && !admin && tab === 0 && (
        <Select
          value={search.get('listingType') ?? 'skill'}
          onValueChange={(value) =>
            updateSearch({ listingType: value, offsetPage: '0' })
          }
        >
          <SelectTrigger
            className='w-full sm:w-72'
            aria-label={t('navigation.listingType')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {['agent', 'skill', 'mcp_server', 'plugin'].map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {t(`navigation.fields.${kind}`)}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      )}
      {(choices.length > 1 || needsSelection) && (
        <div className='flex flex-wrap gap-3'>
          {choices.length > 1 && (
            <Select
              value={String(tab)}
              onValueChange={(value) =>
                updateSearch({ tab: value, offsetPage: '0', selected: '' })
              }
            >
              <SelectTrigger
                className='w-full sm:w-72'
                aria-label={t(`navigation.${config.titleKey}`)}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {choices.map((choice, index) => (
                    <SelectItem key={choice} value={String(index)}>
                      {tabLabel(choice)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          )}
          {needsSelection && (
            <Select
              value={selected}
              onValueChange={(value) =>
                updateSearch({ selected: value, offsetPage: '0' })
              }
            >
              <SelectTrigger
                className='w-full sm:w-72'
                aria-label={t(selectionLabel)}
              >
                <SelectValue placeholder={t(selectionLabel)} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {pickerRows.map((row) => (
                    <SelectItem key={String(row.id)} value={String(row.id)}>
                      {String(row.name ?? row.title ?? row.id)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          )}
        </div>
      )}
      {artifactHistory && tab === 1 && (
        <Field className='max-w-sm'>
          <FieldLabel htmlFor='work-package'>
            {t('navigation.workPackage')}
          </FieldLabel>
          <Input
            id='work-package'
            value={workPackage}
            onChange={(event) =>
              updateSearch({ workPackage: event.target.value, offsetPage: '0' })
            }
          />
        </Field>
      )}
      {picker.error && (
        <Alert variant='destructive'>
          <AlertDescription>{picker.error.message}</AlertDescription>
        </Alert>
      )}
      {!enabled ? (
        <Empty className='min-h-48 flex-none'>
          <EmptyHeader>
            <EmptyTitle>
              {t(
                needsSelection ? selectionLabel : 'workspaceConsole.noWorkspace'
              )}
            </EmptyTitle>
          </EmptyHeader>
        </Empty>
      ) : resource.error ? (
        <Alert variant='destructive'>
          <AlertDescription>{resource.error.message}</AlertDescription>
        </Alert>
      ) : resource.isPending ? (
        <Skeleton className='h-64 w-full' />
      ) : view === 'notification-preferences' && isRow(resource.data) ? (
        <FieldGroup className='max-w-xl'>
          {Object.entries(resource.data)
            .filter(([key]) => key.endsWith('_enabled'))
            .map(([key, value]) => (
              <Field key={key} orientation='horizontal'>
                <FieldLabel htmlFor={key}>{label(key)}</FieldLabel>
                <Switch
                  id={key}
                  checked={value === true}
                  disabled={preference.isPending}
                  onCheckedChange={(checked) =>
                    preference.mutate({ key, value: checked })
                  }
                />
              </Field>
            ))}
          {preference.error && (
            <Alert variant='destructive'>
              <AlertDescription>{preference.error.message}</AlertDescription>
            </Alert>
          )}
        </FieldGroup>
      ) : rows.length === 0 ? (
        <Empty className='min-h-48 flex-none border'>
          <EmptyHeader>
            <EmptyTitle>{t('workspaceConsole.noData')}</EmptyTitle>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                {columns.map((key) => (
                  <TableHead key={key}>{label(key)}</TableHead>
                ))}
                <TableHead>
                  <span className='sr-only'>{t('navigation.details')}</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow key={String(row.id ?? index)}>
                  {columns.map((key) => (
                    <TableCell key={key}>
                      <span
                        className='block max-w-40 truncate sm:max-w-64'
                        title={display(row[key], key)}
                      >
                        {display(row[key], key)}
                      </span>
                    </TableCell>
                  ))}
                  <TableCell>
                    <div className='flex gap-2'>
                      <Button
                        variant='ghost'
                        size='sm'
                        onClick={() => setDetail(row)}
                      >
                        {t('navigation.details')}
                      </Button>
                      {approvals &&
                        row.status === 'pending' &&
                        (admin ||
                          access?.allowed_actions.includes('approve')) && (
                          <>
                            <Button
                              size='sm'
                              variant='outline'
                              onClick={() => {
                                decide.reset()
                                setDecision({ row, action: 'approve' })
                              }}
                            >
                              {t('navigation.approve')}
                            </Button>
                            <Button
                              size='sm'
                              variant='outline'
                              onClick={() => {
                                decide.reset()
                                setDecision({ row, action: 'reject' })
                              }}
                            >
                              {t('navigation.reject')}
                            </Button>
                          </>
                        )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className='flex items-center justify-end gap-2'>
            <Button
              variant='outline'
              disabled={page === 0}
              onClick={() => updateSearch({ offsetPage: String(page - 1) })}
            >
              {t('navigation.previous')}
            </Button>
            <span className='tabular-nums'>{page + 1}</span>
            <Button
              variant='outline'
              disabled={!hasNext}
              onClick={() => updateSearch({ offsetPage: String(page + 1) })}
            >
              {t('navigation.next')}
            </Button>
          </div>
        </>
      )}
      <Dialog
        open={detail !== null}
        onOpenChange={(open) => !open && setDetail(null)}
      >
        <DialogContent className='max-h-[85svh] overflow-y-auto'>
          <DialogHeader>
            <DialogTitle>{t('navigation.details')}</DialogTitle>
            <DialogDescription className='sr-only'>
              {t(`navigation.${config.titleKey}`)}
            </DialogDescription>
          </DialogHeader>
          <dl className='flex min-w-0 flex-col gap-3'>
            {detail &&
              Object.entries(detail).map(([key, value]) => (
                <div key={key}>
                  <dt className='text-sm text-muted-foreground'>
                    {label(key)}
                  </dt>
                  <dd className='text-sm break-all whitespace-pre-wrap'>
                    {display(value, key)}
                  </dd>
                </div>
              ))}
          </dl>
        </DialogContent>
      </Dialog>
      <Dialog
        open={decision !== null}
        onOpenChange={(open) => {
          if (!open && !decide.isPending) {
            setDecision(null)
            setReason('')
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t(`navigation.${decision?.action ?? 'approve'}`)}
            </DialogTitle>
            <DialogDescription className='sr-only'>
              {t('navigation.reason')}
            </DialogDescription>
          </DialogHeader>
          <Field>
            <FieldLabel htmlFor='decision-reason'>
              {t('navigation.reason')}
            </FieldLabel>
            <Textarea
              id='decision-reason'
              value={reason}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          {decide.error && (
            <Alert variant='destructive'>
              <AlertDescription>{decide.error.message}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button
              variant='outline'
              disabled={decide.isPending}
              onClick={() => {
                setDecision(null)
                setReason('')
              }}
            >
              {t('navigation.cancel')}
            </Button>
            <Button
              disabled={!reason.trim() || decide.isPending}
              onClick={() => decide.mutate()}
            >
              {t('navigation.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Main>
  )
}
