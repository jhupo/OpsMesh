import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Gauge,
  MoreHorizontal,
  Plus,
  Power,
  PowerOff,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  addAdminWorkspaceMember,
  adminProjectQuotasQueryOptions,
  adminUsersQueryOptions,
  adminWorkspaceMembersQueryOptions,
  adminWorkspaceProjectsQueryOptions,
  adminWorkspaceQueryOptions,
  disableAdminProjectQuota,
  removeAdminWorkspaceMember,
  updateAdminProjectStatus,
  updateAdminWorkspaceMember,
  upsertAdminProjectQuotas,
  type AdminProject,
  type AdminProjectQuota,
  type AdminUserListItem,
  type AdminWorkspaceMember,
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
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
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ReadOnlyDataTable } from '@/components/data-table'
import { WorkspaceStatusBadge } from './shared'
import { errorMessage } from './utils'

const memberRoles = ['owner', 'admin', 'operator', 'viewer'] as const
const projectStatuses = ['active', 'disabled', 'archived'] as const

export function WorkspaceDetailSheet({
  workspaceId,
  onOpenChange,
}: {
  workspaceId: string | undefined
  onOpenChange: (open: boolean) => void
}) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const workspace = useQuery(adminWorkspaceQueryOptions(workspaceId))
  const members = useQuery(adminWorkspaceMembersQueryOptions(workspaceId))
  const projects = useQuery(adminWorkspaceProjectsQueryOptions(workspaceId))
  const users = useQuery(adminUsersQueryOptions())
  const [addMemberOpen, setAddMemberOpen] = useState(false)
  const [removeMember, setRemoveMember] = useState<AdminWorkspaceMember>()
  const [quotaProject, setQuotaProject] = useState<AdminProject>()

  const refreshWorkspace = async () => {
    if (!workspaceId) return
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: ['platform-admin', 'organization', 'workspace', workspaceId],
      }),
      queryClient.invalidateQueries({
        queryKey: [
          'platform-admin',
          'organization',
          'workspace-members',
          workspaceId,
        ],
      }),
      queryClient.invalidateQueries({
        queryKey: [
          'platform-admin',
          'organization',
          'workspace-projects',
          workspaceId,
        ],
      }),
      queryClient.invalidateQueries({
        queryKey: ['platform-admin', 'organization', 'workspaces'],
      }),
    ])
  }

  const memberMutation = useMutation({
    mutationFn: ({
      member,
      payload,
    }: {
      member: AdminWorkspaceMember
      payload: Partial<Pick<AdminWorkspaceMember, 'role' | 'status'>>
    }) => updateAdminWorkspaceMember(workspaceId!, member.id, payload),
    onSuccess: async () => {
      await refreshWorkspace()
      toast.success(t('platformAdmin.workspaces.memberUpdated'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const removeMutation = useMutation({
    mutationFn: (member: AdminWorkspaceMember) =>
      removeAdminWorkspaceMember(workspaceId!, member.id),
    onSuccess: async () => {
      await refreshWorkspace()
      setRemoveMember(undefined)
      toast.success(t('platformAdmin.workspaces.memberRemoved'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const projectMutation = useMutation({
    mutationFn: ({
      project,
      status,
    }: {
      project: AdminProject
      status: string
    }) =>
      updateAdminProjectStatus(
        workspaceId!,
        project.id,
        status as 'active' | 'disabled' | 'archived'
      ),
    onSuccess: async () => {
      await refreshWorkspace()
      toast.success(t('platformAdmin.workspaces.projectUpdated'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const formatDate = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' }).format(
      new Date(value)
    )

  const detail = workspace.data
  const hasError = workspace.isError || members.isError || projects.isError
  const loading = workspace.isPending || members.isPending || projects.isPending

  return (
    <>
      <Sheet open={Boolean(workspaceId)} onOpenChange={onOpenChange}>
        <SheetContent className='w-full overflow-y-auto overscroll-contain sm:max-w-3xl'>
          <SheetHeader className='border-b px-5 py-5 pe-12 text-start sm:px-6'>
            <SheetTitle className='text-xl'>
              {detail?.name ?? t('platformAdmin.workspaces.detailsTitle')}
            </SheetTitle>
            <SheetDescription className='sr-only'>
              {detail ? (
                <span translate='no'>{detail.slug}</span>
              ) : (
                t('platformAdmin.workspaces.detailsDescription')
              )}
            </SheetDescription>
          </SheetHeader>

          <div className='flex flex-col gap-8 px-5 pb-8 sm:px-6'>
            {hasError ? (
              <Alert variant='destructive'>
                <AlertTitle>
                  {t('platformAdmin.workspaces.loadError')}
                </AlertTitle>
                <AlertDescription>
                  {errorMessage(
                    workspace.error ?? members.error ?? projects.error,
                    t('common.error')
                  )}
                </AlertDescription>
              </Alert>
            ) : loading ? (
              <DetailSkeleton />
            ) : detail ? (
              <>
                <dl className='grid grid-cols-2 gap-x-6 gap-y-5 border-b pb-6 sm:grid-cols-4'>
                  <DetailValue
                    label={t('platformAdmin.workspaces.statusLabel')}
                    value={<WorkspaceStatusBadge status={detail.status} />}
                  />
                  <DetailValue
                    label={t('platformAdmin.workspaces.members')}
                    value={String(detail.member_count)}
                  />
                  <DetailValue
                    label={t('platformAdmin.workspaces.projects')}
                    value={String(detail.project_count)}
                  />
                  <DetailValue
                    label={t('platformAdmin.workspaces.created')}
                    value={formatDate(detail.created_at)}
                  />
                  <DetailValue
                    className='col-span-2'
                    label={t('platformAdmin.workspaces.ownerId')}
                    value={<span translate='no'>{detail.owner_user_id}</span>}
                  />
                  <DetailValue
                    className='col-span-2'
                    label={t('platformAdmin.workspaces.workspaceId')}
                    value={<span translate='no'>{detail.id}</span>}
                  />
                </dl>

                <section aria-labelledby='workspace-members-title'>
                  <div className='mb-4 flex flex-wrap items-start justify-between gap-3'>
                    <div>
                      <h2
                        id='workspace-members-title'
                        className='font-semibold'
                      >
                        {t('platformAdmin.workspaces.membersTitle')}
                      </h2>
                    </div>
                    <Button size='sm' onClick={() => setAddMemberOpen(true)}>
                      <UserPlus aria-hidden='true' />
                      {t('platformAdmin.workspaces.addMember')}
                    </Button>
                  </div>
                  {(members.data?.items.length ?? 0) === 0 ? (
                    <Empty className='min-h-40 border'>
                      <EmptyHeader>
                        <EmptyMedia variant='icon'>
                          <Users aria-hidden='true' />
                        </EmptyMedia>
                        <EmptyTitle className='text-base'>
                          {t('platformAdmin.workspaces.noMembers')}
                        </EmptyTitle>
                      </EmptyHeader>
                    </Empty>
                  ) : (
                    <ReadOnlyDataTable
                      data={members.data?.items ?? []}
                      columns={[
                        {
                          accessorKey: 'display_name',
                          header: t('platformAdmin.workspaces.member'),
                          cell: ({ row }) => (
                            <div className='w-28 sm:w-52'>
                              <div
                                className='truncate font-medium'
                                title={row.original.display_name}
                              >
                                {row.original.display_name}
                              </div>
                              <div
                                className='truncate text-xs text-muted-foreground'
                                title={row.original.email}
                              >
                                {row.original.email}
                              </div>
                            </div>
                          ),
                        },
                        {
                          accessorKey: 'role',
                          header: t('platformAdmin.workspaces.role'),
                          cell: ({ row }) => (
                            <Badge variant='secondary'>
                              {t(
                                `platformAdmin.workspaces.roles.${row.original.role}`
                              )}
                            </Badge>
                          ),
                        },
                        {
                          accessorKey: 'status',
                          header: t('platformAdmin.workspaces.statusLabel'),
                          cell: ({ row }) => (
                            <WorkspaceStatusBadge
                              status={row.original.status}
                            />
                          ),
                        },
                        {
                          id: 'actions',
                          cell: ({ row }) => (
                            <MemberActions
                              member={row.original}
                              disabled={memberMutation.isPending}
                              onUpdate={(payload) =>
                                memberMutation.mutate({
                                  member: row.original,
                                  payload,
                                })
                              }
                              onRemove={() => setRemoveMember(row.original)}
                            />
                          ),
                        },
                      ]}
                    />
                  )}
                </section>

                <Separator />

                <section aria-labelledby='workspace-projects-title'>
                  <div className='mb-4'>
                    <h2 id='workspace-projects-title' className='font-semibold'>
                      {t('platformAdmin.workspaces.projectsTitle')}
                    </h2>
                  </div>
                  {(projects.data?.items.length ?? 0) === 0 ? (
                    <Empty className='min-h-40 border'>
                      <EmptyHeader>
                        <EmptyMedia variant='icon'>
                          <Gauge aria-hidden='true' />
                        </EmptyMedia>
                        <EmptyTitle className='text-base'>
                          {t('platformAdmin.workspaces.noProjects')}
                        </EmptyTitle>
                        <EmptyDescription>
                          {t('platformAdmin.workspaces.noProjectsDescription')}
                        </EmptyDescription>
                      </EmptyHeader>
                    </Empty>
                  ) : (
                    <ReadOnlyDataTable
                      data={projects.data?.items ?? []}
                      columns={[
                        {
                          accessorKey: 'name',
                          header: t('platformAdmin.workspaces.project'),
                          cell: ({ row }) => (
                            <div className='w-28 sm:w-52'>
                              <div
                                className='truncate font-medium'
                                title={row.original.name}
                              >
                                {row.original.name}
                              </div>
                              <div
                                className='truncate text-xs text-muted-foreground'
                                translate='no'
                              >
                                {row.original.slug}
                              </div>
                            </div>
                          ),
                        },
                        {
                          accessorKey: 'status',
                          header: t('platformAdmin.workspaces.statusLabel'),
                          cell: ({ row }) => (
                            <WorkspaceStatusBadge
                              status={row.original.status}
                            />
                          ),
                        },
                        {
                          accessorKey: 'configuration_version',
                          header: t(
                            'platformAdmin.workspaces.configurationVersion'
                          ),
                          cell: ({ row }) => (
                            <span className='tabular-nums'>
                              v{row.original.configuration_version}
                            </span>
                          ),
                        },
                        {
                          id: 'actions',
                          cell: ({ row }) => (
                            <ProjectActions
                              project={row.original}
                              disabled={projectMutation.isPending}
                              onStatusChange={(status) =>
                                projectMutation.mutate({
                                  project: row.original,
                                  status,
                                })
                              }
                              onQuotas={() => setQuotaProject(row.original)}
                            />
                          ),
                        },
                      ]}
                    />
                  )}
                </section>
              </>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>

      {workspaceId && (
        <AddMemberDialog
          open={addMemberOpen}
          onOpenChange={setAddMemberOpen}
          workspaceId={workspaceId}
          users={users.data?.items ?? []}
          members={members.data?.items ?? []}
          onAdded={refreshWorkspace}
        />
      )}

      <AlertDialog
        open={Boolean(removeMember)}
        onOpenChange={(open) => {
          if (!open && !removeMutation.isPending) setRemoveMember(undefined)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('platformAdmin.workspaces.removeMemberTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('platformAdmin.workspaces.removeMemberDescription', {
                name: removeMember?.display_name,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeMutation.isPending}>
              {t('common.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className='bg-destructive text-white hover:bg-destructive/90'
              disabled={removeMutation.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (removeMember) removeMutation.mutate(removeMember)
              }}
            >
              {removeMutation.isPending
                ? t('platformAdmin.workspaces.loading')
                : t('platformAdmin.workspaces.removeMember')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {workspaceId && (
        <ProjectQuotaDialog
          project={quotaProject}
          workspaceId={workspaceId}
          onOpenChange={(open) => {
            if (!open) setQuotaProject(undefined)
          }}
        />
      )}
    </>
  )
}

function DetailValue({
  label,
  value,
  className,
}: {
  label: string
  value: React.ReactNode
  className?: string
}) {
  return (
    <div className={className}>
      <dt className='text-xs font-medium text-muted-foreground'>{label}</dt>
      <dd className='mt-1 min-w-0 text-sm break-all'>{value}</dd>
    </div>
  )
}

function MemberActions({
  member,
  disabled,
  onUpdate,
  onRemove,
}: {
  member: AdminWorkspaceMember
  disabled: boolean
  onUpdate: (
    payload: Partial<Pick<AdminWorkspaceMember, 'role' | 'status'>>
  ) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='size-8'
          disabled={disabled}
          aria-label={t('platformAdmin.workspaces.memberActions', {
            name: member.display_name,
          })}
        >
          <MoreHorizontal aria-hidden='true' />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end' className='w-48'>
        <DropdownMenuLabel>
          {t('platformAdmin.workspaces.changeRole')}
        </DropdownMenuLabel>
        {memberRoles.map((role) => (
          <DropdownMenuItem
            key={role}
            disabled={member.role === role}
            onSelect={() => onUpdate({ role })}
          >
            {t(`platformAdmin.workspaces.roles.${role}`)}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() =>
            onUpdate({
              status: member.status === 'active' ? 'disabled' : 'active',
            })
          }
        >
          {member.status === 'active' ? (
            <PowerOff aria-hidden='true' />
          ) : (
            <Power aria-hidden='true' />
          )}
          {member.status === 'active'
            ? t('platformAdmin.workspaces.disableMember')
            : t('platformAdmin.workspaces.enableMember')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant='destructive'
          disabled={member.role === 'owner'}
          onSelect={onRemove}
        >
          <Trash2 aria-hidden='true' />
          {t('platformAdmin.workspaces.removeMember')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ProjectActions({
  project,
  disabled,
  onStatusChange,
  onQuotas,
}: {
  project: AdminProject
  disabled: boolean
  onStatusChange: (status: (typeof projectStatuses)[number]) => void
  onQuotas: () => void
}) {
  const { t } = useTranslation()
  const icons = { active: Power, disabled: PowerOff, archived: Archive }
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='size-8'
          disabled={disabled}
          aria-label={t('platformAdmin.workspaces.projectActions', {
            name: project.name,
          })}
        >
          <MoreHorizontal aria-hidden='true' />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end' className='w-48'>
        <DropdownMenuItem onSelect={onQuotas}>
          <Gauge aria-hidden='true' />
          {t('platformAdmin.workspaces.manageQuotas')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>
          {t('platformAdmin.workspaces.changeStatus')}
        </DropdownMenuLabel>
        {projectStatuses.map((status) => {
          const Icon = icons[status]
          return (
            <DropdownMenuItem
              key={status}
              disabled={project.status === status}
              onSelect={() => onStatusChange(status)}
            >
              <Icon aria-hidden='true' />
              {t(`platformAdmin.workspaces.status.${status}`)}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function AddMemberDialog({
  open,
  onOpenChange,
  workspaceId,
  users,
  members,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  users: AdminUserListItem[]
  members: AdminWorkspaceMember[]
  onAdded: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [userId, setUserId] = useState('')
  const [role, setRole] = useState<AdminWorkspaceMember['role']>('viewer')
  const existingUserIds = new Set(members.map((member) => member.user_id))
  const eligibleUsers = users.filter(
    (user) => user.status === 'active' && !existingUserIds.has(user.id)
  )
  const mutation = useMutation({
    mutationFn: () => addAdminWorkspaceMember(workspaceId, userId, role),
    onSuccess: async () => {
      await onAdded()
      setUserId('')
      setRole('viewer')
      onOpenChange(false)
      toast.success(t('platformAdmin.workspaces.memberAdded'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!mutation.isPending) onOpenChange(nextOpen)
      }}
    >
      <DialogContent className='overscroll-contain sm:max-w-md'>
        <DialogHeader className='text-start'>
          <DialogTitle>
            {t('platformAdmin.workspaces.addMemberTitle')}
          </DialogTitle>
          <DialogDescription className='sr-only'>
            {t('platformAdmin.workspaces.addMemberDescription')}
          </DialogDescription>
        </DialogHeader>
        <div className='grid gap-4 py-2'>
          <div className='grid gap-2'>
            <Label htmlFor='workspace-member-user'>
              {t('platformAdmin.workspaces.user')}
            </Label>
            <Select value={userId} onValueChange={setUserId}>
              <SelectTrigger id='workspace-member-user' className='w-full'>
                <SelectValue
                  placeholder={`${t('platformAdmin.workspaces.selectUser')}…`}
                />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {eligibleUsers.map((user) => (
                    <SelectItem key={user.id} value={user.id}>
                      {user.display_name} ({user.email})
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            {eligibleUsers.length === 0 && (
              <p className='text-sm text-muted-foreground'>
                {t('platformAdmin.workspaces.noEligibleUsers')}
              </p>
            )}
          </div>
          <div className='grid gap-2'>
            <Label htmlFor='workspace-member-role'>
              {t('platformAdmin.workspaces.role')}
            </Label>
            <Select
              value={role}
              onValueChange={(value) =>
                setRole(value as AdminWorkspaceMember['role'])
              }
            >
              <SelectTrigger id='workspace-member-role' className='w-full'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {memberRoles.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`platformAdmin.workspaces.roles.${value}`)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button
            variant='outline'
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!userId || mutation.isPending}
          >
            {mutation.isPending
              ? t('platformAdmin.workspaces.loading')
              : t('platformAdmin.workspaces.addMember')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type QuotaDraft = {
  quota_key: string
  limit_value: string
  unit: string
  existing: boolean
}

function ProjectQuotaDialog({
  project,
  workspaceId,
  onOpenChange,
}: {
  project: AdminProject | undefined
  workspaceId: string
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const quotas = useQuery(
    adminProjectQuotasQueryOptions(workspaceId, project?.id)
  )
  const [drafts, setDrafts] = useState<QuotaDraft[]>()
  const serverDrafts = useMemo(
    () =>
      (quotas.data ?? [])
        .filter((quota) => quota.status !== 'disabled')
        .map((quota) => ({
          quota_key: quota.quota_key,
          limit_value: String(quota.limit_value),
          unit: quota.unit,
          existing: true,
        })),
    [quotas.data]
  )
  const visibleDrafts = drafts ?? serverDrafts

  const refreshQuotas = () =>
    queryClient.invalidateQueries({
      queryKey: [
        'platform-admin',
        'organization',
        'project-quotas',
        workspaceId,
        project?.id,
      ],
    })

  const saveMutation = useMutation({
    mutationFn: () =>
      upsertAdminProjectQuotas(
        workspaceId,
        project!.id,
        visibleDrafts.map((quota) => ({
          quota_key: quota.quota_key.trim(),
          limit_value: Number(quota.limit_value),
          unit: quota.unit.trim(),
        }))
      ),
    onSuccess: async () => {
      await refreshQuotas()
      setDrafts(undefined)
      toast.success(t('platformAdmin.workspaces.quotasSaved'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const disableMutation = useMutation({
    mutationFn: (quotaKey: string) =>
      disableAdminProjectQuota(workspaceId, project!.id, quotaKey),
    onSuccess: async () => {
      await refreshQuotas()
      setDrafts(undefined)
      toast.success(t('platformAdmin.workspaces.quotaDisabled'))
    },
    onError: (error) => toast.error(errorMessage(error, t('common.error'))),
  })

  const validDrafts =
    visibleDrafts.length > 0 &&
    visibleDrafts.every(
      (quota) =>
        /^[a-zA-Z0-9_.-]+$/.test(quota.quota_key.trim()) &&
        quota.unit.trim().length > 0 &&
        Number.isInteger(Number(quota.limit_value)) &&
        Number(quota.limit_value) >= 0
    ) &&
    new Set(visibleDrafts.map((quota) => quota.quota_key.trim())).size ===
      visibleDrafts.length

  const disabledQuotas = (quotas.data ?? []).filter(
    (quota) => quota.status === 'disabled'
  )

  return (
    <Dialog open={Boolean(project)} onOpenChange={onOpenChange}>
      <DialogContent className='max-h-[calc(100vh-2rem)] overflow-y-auto overscroll-contain sm:max-w-2xl'>
        <DialogHeader className='text-start'>
          <DialogTitle>{t('platformAdmin.workspaces.quotasTitle')}</DialogTitle>
          <DialogDescription className='sr-only'>
            {t('platformAdmin.workspaces.quotasDescription', {
              name: project?.name,
            })}
          </DialogDescription>
        </DialogHeader>

        {quotas.isError ? (
          <Alert variant='destructive'>
            <AlertTitle>{t('platformAdmin.workspaces.loadError')}</AlertTitle>
            <AlertDescription>
              {errorMessage(quotas.error, t('common.error'))}
            </AlertDescription>
          </Alert>
        ) : quotas.isPending ? (
          <div className='space-y-3 py-4'>
            <Skeleton className='h-14 w-full' />
            <Skeleton className='h-14 w-full' />
          </div>
        ) : (
          <div className='flex flex-col gap-5 py-2'>
            <div className='flex items-center justify-between gap-3'>
              <p className='text-sm font-medium'>
                {t('platformAdmin.workspaces.activeQuotas')}
              </p>
              <Button
                type='button'
                variant='outline'
                size='sm'
                onClick={() =>
                  setDrafts([
                    ...visibleDrafts,
                    {
                      quota_key: '',
                      limit_value: '0',
                      unit: 'count',
                      existing: false,
                    },
                  ])
                }
              >
                <Plus aria-hidden='true' />
                {t('platformAdmin.workspaces.addQuota')}
              </Button>
            </div>

            {visibleDrafts.length === 0 ? (
              <Empty className='min-h-32 border'>
                <EmptyHeader>
                  <EmptyTitle className='text-base'>
                    {t('platformAdmin.workspaces.noQuotas')}
                  </EmptyTitle>
                  <EmptyDescription>
                    {t('platformAdmin.workspaces.noQuotasDescription')}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <div className='divide-y rounded-md border'>
                {visibleDrafts.map((draft, index) => {
                  const source = quotas.data?.find(
                    (quota) => quota.quota_key === draft.quota_key
                  )
                  return (
                    <div key={`${draft.quota_key}-${index}`} className='p-4'>
                      <div className='grid gap-3 sm:grid-cols-[1fr_8rem_7rem_auto] sm:items-end'>
                        <div className='grid gap-1.5'>
                          <Label htmlFor={`quota-key-${index}`}>
                            {t('platformAdmin.workspaces.quotaKey')}
                          </Label>
                          <Input
                            id={`quota-key-${index}`}
                            name={`quota_key_${index}`}
                            value={draft.quota_key}
                            disabled={draft.existing}
                            onChange={(event) =>
                              updateDraft(
                                setDrafts,
                                index,
                                'quota_key',
                                event.target.value
                              )
                            }
                          />
                        </div>
                        <div className='grid gap-1.5'>
                          <Label htmlFor={`quota-limit-${index}`}>
                            {t('platformAdmin.workspaces.limit')}
                          </Label>
                          <Input
                            id={`quota-limit-${index}`}
                            name={`quota_limit_${index}`}
                            type='number'
                            min={0}
                            step={1}
                            value={draft.limit_value}
                            onChange={(event) =>
                              updateDraft(
                                setDrafts,
                                index,
                                'limit_value',
                                event.target.value
                              )
                            }
                          />
                        </div>
                        <div className='grid gap-1.5'>
                          <Label htmlFor={`quota-unit-${index}`}>
                            {t('platformAdmin.workspaces.unit')}
                          </Label>
                          <Input
                            id={`quota-unit-${index}`}
                            name={`quota_unit_${index}`}
                            value={draft.unit}
                            onChange={(event) =>
                              updateDraft(
                                setDrafts,
                                index,
                                'unit',
                                event.target.value
                              )
                            }
                          />
                        </div>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              type='button'
                              variant='ghost'
                              size='icon'
                              aria-label={t(
                                'platformAdmin.workspaces.disableQuota'
                              )}
                              disabled={disableMutation.isPending}
                              onClick={() => {
                                if (draft.existing) {
                                  disableMutation.mutate(draft.quota_key)
                                } else {
                                  setDrafts(
                                    visibleDrafts.filter(
                                      (_, itemIndex) => itemIndex !== index
                                    )
                                  )
                                }
                              }}
                            >
                              <Trash2 aria-hidden='true' />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            {draft.existing
                              ? t('platformAdmin.workspaces.disableQuota')
                              : t('platformAdmin.workspaces.removeQuotaDraft')}
                          </TooltipContent>
                        </Tooltip>
                      </div>
                      {source && <QuotaUsage quota={source} />}
                    </div>
                  )
                })}
              </div>
            )}

            {disabledQuotas.length > 0 && (
              <div className='border-t pt-4'>
                <p className='mb-3 text-sm font-medium'>
                  {t('platformAdmin.workspaces.disabledQuotas')}
                </p>
                <div className='flex flex-wrap gap-2'>
                  {disabledQuotas.map((quota) => (
                    <Badge
                      key={quota.quota_key}
                      variant='outline'
                      className='text-muted-foreground'
                    >
                      <span translate='no'>{quota.quota_key}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            type='button'
            variant='outline'
            onClick={() => onOpenChange(false)}
          >
            {t('common.close')}
          </Button>
          <Button
            type='button'
            disabled={!validDrafts || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending
              ? t('platformAdmin.workspaces.loading')
              : t('platformAdmin.workspaces.saveQuotas')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QuotaUsage({ quota }: { quota: AdminProjectQuota }) {
  const { t } = useTranslation()
  const percentage = Math.min(100, Math.round(quota.utilization * 100))
  return (
    <div className='mt-3 grid gap-1.5'>
      <div className='flex items-center justify-between gap-4 text-xs text-muted-foreground'>
        <span>
          {t('platformAdmin.workspaces.quotaUsage', {
            reserved: quota.reserved_value,
            limit: quota.limit_value,
            unit: quota.unit,
          })}
        </span>
        <span className='tabular-nums'>{percentage}%</span>
      </div>
      <Progress
        value={percentage}
        aria-label={`${quota.quota_key} ${percentage}%`}
      />
    </div>
  )
}

function updateDraft(
  setDrafts: React.Dispatch<React.SetStateAction<QuotaDraft[] | undefined>>,
  index: number,
  key: keyof Pick<QuotaDraft, 'quota_key' | 'limit_value' | 'unit'>,
  value: string
) {
  setDrafts((current) =>
    (current ?? []).map((draft, itemIndex) =>
      itemIndex === index ? { ...draft, [key]: value } : draft
    )
  )
}

function DetailSkeleton() {
  return (
    <div className='space-y-8 pt-2' aria-hidden='true'>
      <div className='grid grid-cols-2 gap-4 sm:grid-cols-4'>
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className='h-12 w-full' />
        ))}
      </div>
      <Skeleton className='h-52 w-full' />
      <Skeleton className='h-52 w-full' />
    </div>
  )
}
