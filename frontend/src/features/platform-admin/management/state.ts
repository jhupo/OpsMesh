import { useMemo, useSyncExternalStore } from 'react'
import { z } from 'zod'
import { useSuspenseQuery } from '@tanstack/react-query'
import { currentUserQueryOptions } from '@/api/auth'
import { definitions, type Action, type Collection } from './catalog'

export const quotaMetrics = [
  'tokens',
  'storage',
  'concurrency',
  'cpu',
  'memory',
  'runtimeHours',
  'budget',
] as const
const quotaMetricSchema = z.enum(quotaMetrics)
const quotaUsageSchema = z.object({
  workspaceId: z.string(),
  projectId: z.string(),
  metric: quotaMetricSchema,
  used: z.number().finite().nonnegative().nullable(),
  limit: z.number().finite().nonnegative().nullable(),
  periodStart: z.string().datetime().optional(),
  periodEnd: z.string().datetime().optional(),
})
export type QuotaUsage = z.infer<typeof quotaUsageSchema>
const rowSchema = z.object({
  id: z.string(),
  name: z.string(),
  status: z.string(),
  updatedAt: z.string(),
  values: z.record(z.string(), z.string()),
  quotaUsage: z.array(quotaUsageSchema).optional(),
})
export type ManagementRow = z.infer<typeof rowSchema>
const membershipSchema = z.object({
  id: z.string(),
  userId: z.string(),
  workspaceId: z.string(),
  role: z.enum(['owner', 'admin', 'operator', 'viewer']),
})
export type Membership = z.infer<typeof membershipSchema>
const allocationSchema = z.object({
  workspaceId: z.string(),
  projectId: z.string(),
  resources: z.array(z.string()),
  concurrency: z.number().nonnegative(),
  tokens: z.number().nonnegative(),
  storage: z.number().nonnegative(),
  budget: z.number().nonnegative(),
})
export type Allocation = z.infer<typeof allocationSchema>
const stateSchema = z.object({
  version: z.literal(3),
  rows: z.record(z.string(), z.array(rowSchema)),
  memberships: z.array(membershipSchema),
  allocations: z.array(allocationSchema),
  settings: z.record(z.string(), z.string()),
})
export type ManagementState = z.infer<typeof stateSchema>
export const allocationLimits = [
  'concurrency',
  'tokens',
  'storage',
  'budget',
] as const
export const emptyAllocation = (
  workspaceId: string,
  projectId = ''
): Allocation => ({
  workspaceId,
  projectId,
  resources: [],
  concurrency: 0,
  tokens: 0,
  storage: 0,
  budget: 0,
})
export class ManagementStore {
  private state: ManagementState
  private listeners = new Set<() => void>()
  readonly key: string
  constructor(readonly userId: string) {
    sessionStorage.removeItem(`opsmesh.admin-ui.v2.${userId}`)
    this.key = `opsmesh.admin-ui.v3.${userId}`
    const raw = sessionStorage.getItem(this.key)
    let restored: ManagementState | undefined
    if (raw) {
      try {
        const parsed = stateSchema.safeParse(JSON.parse(raw))
        if (parsed.success) restored = parsed.data
      } catch {
        /* Invalid drafts are not imported. */
      }
    }
    this.state = restored ?? {
      version: 3,
      rows: {},
      memberships: [],
      allocations: [],
      settings: {
        platformName: 'OpsMesh',
        registration: 'closed',
        sessionHours: '24',
        passwordMinLength: '12',
        timezone: 'Asia/Shanghai',
        auditRetention: '365',
        allowPublic: 'false',
        approvalRequired: 'true',
      },
    }
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }
  getSnapshot = () => this.state
  commit(update: (state: ManagementState) => void) {
    const next = structuredClone(this.state)
    update(next)
    // Storage failure must not report a successful save or lose the previous state.
    sessionStorage.setItem(this.key, JSON.stringify(next))
    this.state = next
    this.listeners.forEach((listener) => listener())
  }
  private audit(state: ManagementState, action: string, target: string) {
    state.rows.audit = [
      {
        id: crypto.randomUUID(),
        name: action,
        status: 'completed',
        updatedAt: new Date().toISOString(),
        values: { actor: this.userId, target },
      },
      ...(state.rows.audit ?? []),
    ].slice(0, 500)
  }
  save(kind: Collection, row: ManagementRow) {
    this.commit((state) => {
      const rows = state.rows[kind] ?? []
      if (
        rows.some(
          (r) =>
            r.id !== row.id &&
            (kind === 'users'
              ? r.values.email?.toLowerCase() ===
                row.values.email?.toLowerCase()
              : r.name === row.name &&
                r.values.workspaceId === row.values.workspaceId)
        )
      )
        throw new Error('duplicate')
      if (
        row.values.workspaceId &&
        !state.rows.workspaces?.some(
          (r) => r.id === row.values.workspaceId && r.status === 'active'
        )
      )
        throw new Error('invalidWorkspace')
      if (
        row.values.projectId &&
        !state.rows.projects?.some(
          (r) =>
            r.id === row.values.projectId &&
            r.values.workspaceId === row.values.workspaceId &&
            r.status === 'active'
        )
      )
        throw new Error('invalidProject')
      if (
        kind === 'users' &&
        row.id === this.userId &&
        row.values.role !== 'superadmin'
      )
        throw new Error('selfProtection')
      state.rows[kind] = [row, ...rows.filter((r) => r.id !== row.id)]
      if (kind === 'workspaces' && row.values.ownerId) {
        state.memberships = state.memberships.filter(
          (m) => !(m.workspaceId === row.id && m.userId === row.values.ownerId)
        )
        state.memberships.push({
          id: crypto.randomUUID(),
          workspaceId: row.id,
          userId: row.values.ownerId,
          role: 'owner',
        })
      }
      this.audit(
        state,
        rows.some((r) => r.id === row.id) ? 'edit' : 'create',
        row.name
      )
    })
  }
  action(kind: Collection, ids: string[], action: Action, reason = '') {
    this.commit((state) => {
      const rows = state.rows[kind] ?? []
      const selected = rows.filter((r) => ids.includes(r.id))
      selected.forEach((row) => {
        if (!availableActions(kind, row).includes(action))
          throw new Error('invalidTransition')
        if (kind === 'users' && row.id === this.userId)
          throw new Error('selfProtection')
        if (
          kind === 'workspaces' &&
          action === 'delete' &&
          ((state.rows.projects ?? []).some(
            (p) => p.values.workspaceId === row.id
          ) ||
            state.memberships.some(
              (m) => m.workspaceId === row.id && m.userId !== row.values.ownerId
            ))
        )
          throw new Error('hasDependencies')
        if (
          kind === 'projects' &&
          action === 'delete' &&
          Object.entries(state.rows).some(
            ([key, data]) =>
              key !== 'audit' && data.some((r) => r.values.projectId === row.id)
          )
        )
          throw new Error('hasDependencies')
        if (action === 'delete') {
          if (state.allocations.some((a) => a.resources.includes(row.id)))
            throw new Error('hasDependencies')
          state.memberships = state.memberships.filter((m) =>
            kind === 'workspaces' ? m.workspaceId !== row.id : true
          )
          state.allocations = state.allocations.filter((a) =>
            kind === 'workspaces'
              ? a.workspaceId !== row.id
              : kind === 'projects'
                ? a.projectId !== row.id
                : true
          )
        } else {
          row.status =
            (
              {
                enable: 'active',
                disable: 'disabled',
                cancel: 'cancelled',
                retry: 'pending',
                approve: 'approved',
                reject: 'rejected',
                resolve: 'resolved',
                publish: kind === 'plugins' ? 'active' : 'published',
                withdraw: 'withdrawn',
                revoke: kind === 'users' ? row.status : 'revoked',
                drain: 'draining',
                restore: 'active',
              } as Record<string, string>
            )[action] ?? row.status
          row.updatedAt = new Date().toISOString()
          if (reason) row.values.reason = reason
          if (action === 'revoke') row.values.revokedAt = row.updatedAt
          if (action === 'reset') row.values.passwordResetAt = row.updatedAt
        }
        this.audit(state, action, row.name)
      })
      if (action === 'delete')
        state.rows[kind] = rows.filter((row) => !ids.includes(row.id))
    })
  }
  saveMembership(membership: Membership) {
    this.commit((state) => {
      if (
        !state.rows.users?.some(
          (u) => u.id === membership.userId && u.status === 'active'
        )
      )
        throw new Error('invalidUser')
      if (
        !state.rows.workspaces?.some(
          (w) => w.id === membership.workspaceId && w.status === 'active'
        )
      )
        throw new Error('invalidWorkspace')
      if (
        state.memberships.some(
          (m) =>
            m.id !== membership.id &&
            m.userId === membership.userId &&
            m.workspaceId === membership.workspaceId
        )
      )
        throw new Error('duplicate')
      if (membership.role === 'owner') {
        const workspace = state.rows.workspaces?.find(
          (item) => item.id === membership.workspaceId
        )
        if (workspace) workspace.values.ownerId = membership.userId
        state.memberships.forEach((item) => {
          if (
            item.workspaceId === membership.workspaceId &&
            item.userId !== membership.userId &&
            item.role === 'owner'
          )
            item.role = 'admin'
        })
      }
      state.memberships = [
        ...state.memberships.filter((m) => m.id !== membership.id),
        membership,
      ]
      this.audit(state, 'membership', membership.userId)
    })
  }
  removeMembership(id: string) {
    this.commit((state) => {
      const item = state.memberships.find((m) => m.id === id)
      if (!item) return
      const target = state.rows.workspaces?.find(
        (workspace) => workspace.id === item.workspaceId
      )
      if (target?.values.ownerId === item.userId)
        throw new Error('ownerProtection')
      state.memberships = state.memberships.filter((m) => m.id !== id)
      this.audit(state, 'remove', item.userId)
    })
  }
  saveAllocation(allocation: Allocation) {
    this.commit((state) => {
      if (!state.rows.workspaces?.some((w) => w.id === allocation.workspaceId))
        throw new Error('invalidWorkspace')
      const remaining = state.allocations.filter(
        (a) =>
          a.workspaceId !== allocation.workspaceId ||
          a.projectId !== allocation.projectId
      )
      const space = allocation.projectId
        ? remaining.find(
            (a) => a.workspaceId === allocation.workspaceId && !a.projectId
          )
        : allocation
      if (!space) throw new Error('workspaceFirst')
      if (
        allocation.projectId &&
        !state.rows.projects?.some(
          (p) =>
            p.id === allocation.projectId &&
            p.values.workspaceId === allocation.workspaceId
        )
      )
        throw new Error('invalidProject')
      const children = [
        ...remaining.filter(
          (a) => a.workspaceId === allocation.workspaceId && a.projectId
        ),
        ...(allocation.projectId ? [allocation] : []),
      ]
      if (
        children.some((child) =>
          child.resources.some((id) => !space.resources.includes(id))
        )
      )
        throw new Error('resourceOutsidePool')
      if (
        allocationLimits.some(
          (key) =>
            children.reduce((sum, child) => sum + child[key], 0) > space[key]
        )
      )
        throw new Error('overQuota')
      state.allocations = [...remaining, allocation]
      this.audit(
        state,
        'allocation',
        allocation.projectId || allocation.workspaceId
      )
    })
  }
  saveSettings(settings: Record<string, string>) {
    this.commit((state) => {
      state.settings = settings
      this.audit(state, 'configuration', 'OpsMesh')
    })
  }
}
const stores = new Map<string, ManagementStore>()
export function useManagement() {
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const store = useMemo(() => {
    let value = stores.get(user.user_id)
    if (!value) {
      value = new ManagementStore(user.user_id)
      stores.set(user.user_id, value)
    }
    return value
  }, [user.user_id])
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return { state, store, user }
}
export function availableActions(
  kind: Collection,
  row: ManagementRow
): Action[] {
  return definitions[kind].actions.filter((action) => {
    if (action === 'enable')
      return ['disabled', 'draining'].includes(row.status)
    if (action === 'disable' || action === 'drain')
      return row.status === 'active'
    if (action === 'cancel') return ['pending', 'running'].includes(row.status)
    if (action === 'retry') return ['failed', 'cancelled'].includes(row.status)
    if (action === 'approve' || action === 'reject')
      return row.status === 'pending'
    if (action === 'resolve') return row.status === 'open'
    if (action === 'publish')
      return ['draft', 'withdrawn', 'disabled'].includes(row.status)
    if (action === 'withdraw') return row.status === 'published'
    if (action === 'edit' && ['tasks', 'notifications'].includes(kind))
      return ['pending', 'draft', 'withdrawn'].includes(row.status)
    if (action === 'delete' && kind === 'notifications')
      return row.status !== 'published'
    if (action === 'revoke' && kind === 'leases') return row.status === 'active'
    return true
  })
}
export function downloadRows(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })
  )
  const link = document.createElement('a')
  link.href = url
  link.download = `${name}.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
