import {
  Activity,
  ArchiveRestore,
  Bell,
  Bot,
  Boxes,
  BrainCircuit,
  Building2,
  CircleGauge,
  Combine,
  Cpu,
  FileClock,
  FolderKanban,
  Gavel,
  HardDrive,
  History,
  LayoutDashboard,
  ListTodo,
  Network,
  Package,
  PlugZap,
  ServerCog,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  TimerReset,
  Users,
  UsersRound,
  Wallet,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

export type Collection =
  | 'users'
  | 'workspaces'
  | 'projects'
  | 'teams'
  | 'orchestrations'
  | 'tasks'
  | 'approvals'
  | 'automations'
  | 'notifications'
  | 'policies'
  | 'reviews'
  | 'security'
  | 'audit'
  | 'models'
  | 'allocations'
  | 'knowledge'
  | 'memory'
  | 'files'
  | 'artifacts'
  | 'lifecycle'
  | 'runs'
  | 'runtimes'
  | 'runtimeSpaces'
  | 'leases'
  | 'nodes'
  | 'selfHosted'
  | 'queues'
  | 'deadletters'
  | 'schedules'
  | 'calls'
  | 'logs'
  | 'traces'
  | 'costs'
  | 'plugins'
  | 'integrations'
export type FieldDefinition = {
  key: string
  type?:
    | 'text'
    | 'email'
    | 'number'
    | 'url'
    | 'textarea'
    | 'select'
    | 'workspace'
    | 'project'
    | 'user'
  options?: string[]
  required?: boolean
  min?: number
  max?: number
}
export type Action =
  | 'edit'
  | 'delete'
  | 'enable'
  | 'disable'
  | 'cancel'
  | 'retry'
  | 'approve'
  | 'reject'
  | 'resolve'
  | 'publish'
  | 'withdraw'
  | 'revoke'
  | 'reset'
  | 'drain'
  | 'restore'
export type Definition = {
  key: Collection
  icon: LucideIcon
  fields: FieldDefinition[]
  columns: string[]
  statuses: string[]
  create?: boolean
  actions: Action[]
  scoped?: boolean
}
const name: FieldDefinition = { key: 'name', required: true }
const workspace: FieldDefinition = {
  key: 'workspaceId',
  type: 'workspace',
  required: true,
}
const project: FieldDefinition = { key: 'projectId', type: 'project' }
const select = (
  key: string,
  options: string[],
  required = true
): FieldDefinition => ({ key, type: 'select', options, required })
const number = (key: string, max = 1000000): FieldDefinition => ({
  key,
  type: 'number',
  required: true,
  min: 0,
  max,
})
const lifecycleActions: Action[] = ['edit', 'disable', 'enable', 'delete']
const scoped = [workspace, project]
const standard = ['name', 'workspaceId', 'status', 'updatedAt']

// UI authoring schemas, not API DTOs. Read-only execution evidence has no CRUD actions.
export const definitions: Record<Collection, Definition> = {
  users: {
    key: 'users',
    icon: Users,
    create: true,
    columns: [
      'name',
      'role',
      'status',
      'quotaUsage',
      'ownership',
      'createdAt',
      'lastLoginAt',
    ],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      { key: 'email', type: 'email', required: true },
      select('role', ['user', 'admin', 'superadmin']),
    ],
    actions: ['disable', 'enable', 'revoke', 'reset'],
  },
  workspaces: {
    key: 'workspaces',
    icon: Building2,
    create: true,
    columns: ['name', 'ownerId', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [name, { key: 'ownerId', type: 'user', required: true }],
    actions: lifecycleActions,
  },
  projects: {
    key: 'projects',
    icon: FolderKanban,
    create: true,
    columns: standard,
    statuses: ['active', 'disabled'],
    fields: [name, workspace, { key: 'description', type: 'textarea' }],
    actions: lifecycleActions,
    scoped: true,
  },
  teams: {
    key: 'teams',
    icon: UsersRound,
    create: true,
    columns: [
      'name',
      'workspaceId',
      'memberCount',
      'runtimeMode',
      'status',
      'updatedAt',
    ],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      workspace,
      number('memberCount', 128),
      select('runtimeMode', ['none', 'isolated', 'pooled', 'persistent']),
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  orchestrations: {
    key: 'orchestrations',
    icon: Combine,
    create: true,
    columns: ['name', 'workspaceId', 'version', 'status', 'updatedAt'],
    statuses: ['draft', 'active', 'disabled'],
    fields: [
      name,
      workspace,
      { key: 'description', type: 'textarea' },
      number('version', 100000),
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  tasks: {
    key: 'tasks',
    icon: ListTodo,
    create: true,
    columns: [
      'name',
      'workspaceId',
      'projectId',
      'status',
      'priority',
      'updatedAt',
    ],
    statuses: ['pending', 'running', 'completed', 'failed', 'cancelled'],
    fields: [
      name,
      ...scoped,
      { key: 'ownerId', type: 'user', required: true },
      select('priority', ['low', 'normal', 'high']),
      { key: 'instructions', type: 'textarea', required: true },
    ],
    actions: ['edit', 'cancel', 'retry'],
    scoped: true,
  },
  approvals: {
    key: 'approvals',
    icon: ShieldCheck,
    columns: [
      'name',
      'workspaceId',
      'requestedBy',
      'risk',
      'status',
      'updatedAt',
    ],
    statuses: ['pending', 'approved', 'rejected'],
    fields: [
      name,
      workspace,
      { key: 'requestedBy', type: 'user' },
      select('risk', ['low', 'medium', 'high', 'critical']),
      { key: 'reason', type: 'textarea' },
    ],
    actions: ['approve', 'reject'],
    scoped: true,
  },
  automations: {
    key: 'automations',
    icon: Workflow,
    create: true,
    columns: ['name', 'workspaceId', 'trigger', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      ...scoped,
      select('trigger', ['cron', 'event']),
      { key: 'expression', required: true },
      { key: 'timezone', required: true },
      { key: 'instructions', type: 'textarea', required: true },
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  notifications: {
    key: 'notifications',
    icon: Bell,
    create: true,
    columns: ['name', 'audience', 'status', 'updatedAt'],
    statuses: ['draft', 'published', 'withdrawn'],
    fields: [
      name,
      { key: 'body', type: 'textarea', required: true },
      select('audience', ['all', 'workspace', 'project', 'users']),
      { ...workspace, required: false },
      project,
      { key: 'recipientId', type: 'user' },
    ],
    actions: ['edit', 'publish', 'withdraw', 'delete'],
  },
  policies: {
    key: 'policies',
    icon: Gavel,
    create: true,
    columns: ['name', 'category', 'effect', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      select('category', ['access', 'execution', 'worker']),
      { ...workspace, required: false },
      select('resource', ['expert', 'mcp', 'tool', 'skill', 'runtime', 'file']),
      select('operation', ['read', 'use', 'edit', 'publish', 'delete']),
      select('effect', ['allow', 'deny', 'approval']),
      number('timeout', 3600),
    ],
    actions: lifecycleActions,
  },
  reviews: {
    key: 'reviews',
    icon: ShieldCheck,
    columns: [
      'name',
      'category',
      'workspaceId',
      'requestedBy',
      'status',
      'updatedAt',
    ],
    statuses: ['pending', 'approved', 'rejected'],
    fields: [
      name,
      select('category', ['execution', 'publication']),
      workspace,
      { key: 'requestedBy', type: 'user' },
      { key: 'reason', type: 'textarea' },
    ],
    actions: ['approve', 'reject'],
  },
  security: {
    key: 'security',
    icon: ShieldAlert,
    columns: ['name', 'severity', 'workspaceId', 'status', 'updatedAt'],
    statuses: ['open', 'resolved'],
    fields: [
      name,
      workspace,
      select('severity', ['low', 'medium', 'high', 'critical']),
      { key: 'reason', type: 'textarea' },
    ],
    actions: ['resolve'],
  },
  audit: {
    key: 'audit',
    icon: FileClock,
    columns: ['name', 'actor', 'target', 'status', 'updatedAt'],
    statuses: ['completed'],
    fields: [
      name,
      { key: 'actor' },
      { key: 'target' },
      { key: 'reason', type: 'textarea' },
    ],
    actions: [],
  },
  models: {
    key: 'models',
    icon: Cpu,
    create: true,
    columns: ['name', 'provider', 'model', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      select('provider', ['openai', 'anthropic']),
      { key: 'endpoint', type: 'url', required: true },
      { key: 'model', required: true },
      { key: 'credentialRef', required: true },
      number('contextWindow'),
      number('inputPrice'),
      number('outputPrice'),
    ],
    actions: lifecycleActions,
  },
  allocations: {
    key: 'allocations',
    icon: Wallet,
    create: true,
    columns: [
      'name',
      'workspaceId',
      'projectId',
      'resources',
      'budget',
      'updatedAt',
    ],
    statuses: ['active'],
    fields: [
      name,
      workspace,
      project,
      { key: 'resources', required: true },
      number('concurrency', 100000),
      number('tokens'),
      number('storage'),
      number('budget'),
    ],
    actions: ['edit', 'delete'],
    scoped: true,
  },
  knowledge: {
    key: 'knowledge',
    icon: BrainCircuit,
    create: true,
    columns: standard,
    statuses: ['active', 'disabled'],
    fields: [
      name,
      ...scoped,
      select('retrieval', ['hybrid', 'vector', 'fulltext']),
      number('chunkSize', 8192),
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  memory: {
    key: 'memory',
    icon: Bot,
    create: true,
    columns: standard,
    statuses: ['active', 'disabled'],
    fields: [
      name,
      ...scoped,
      { key: 'ownerId', type: 'user' },
      { key: 'content', type: 'textarea', required: true },
      number('retentionDays', 3650),
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  files: {
    key: 'files',
    icon: HardDrive,
    create: true,
    columns: ['name', 'workspaceId', 'size', 'mime', 'updatedAt'],
    statuses: ['active'],
    fields: [name, ...scoped],
    actions: ['delete'],
    scoped: true,
  },
  artifacts: {
    key: 'artifacts',
    icon: Package,
    columns: ['name', 'workspaceId', 'taskId', 'size', 'updatedAt'],
    statuses: ['active'],
    fields: [
      name,
      workspace,
      { key: 'taskId' },
      { key: 'mime' },
      { key: 'size' },
    ],
    actions: ['delete'],
    scoped: true,
  },
  lifecycle: {
    key: 'lifecycle',
    icon: ArchiveRestore,
    create: true,
    columns: ['name', 'workspaceId', 'category', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      workspace,
      select('category', ['retention', 'backup']),
      select('resource', ['file', 'artifact', 'memory', 'audit']),
      number('retentionDays', 3650),
      { key: 'expression', required: true },
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  runs: {
    key: 'runs',
    icon: History,
    columns: [
      'name',
      'workspaceId',
      'taskId',
      'status',
      'duration',
      'updatedAt',
    ],
    statuses: ['pending', 'running', 'completed', 'failed', 'cancelled'],
    fields: [
      name,
      workspace,
      { key: 'taskId' },
      { key: 'duration' },
      { key: 'output', type: 'textarea' },
    ],
    actions: ['cancel', 'retry'],
  },
  runtimes: {
    key: 'runtimes',
    icon: ServerCog,
    create: true,
    columns: ['name', 'workspaceId', 'mode', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      workspace,
      select('mode', ['none', 'isolated', 'pooled', 'persistent']),
      { key: 'image', required: true },
      number('cpu', 256),
      number('memoryMb', 1048576),
      number('timeout', 86400),
    ],
    actions: lifecycleActions,
    scoped: true,
  },
  runtimeSpaces: {
    key: 'runtimeSpaces',
    icon: Boxes,
    create: true,
    columns: ['name', 'scope', 'concurrency', 'status', 'updatedAt'],
    statuses: ['active', 'quarantined', 'disabled'],
    fields: [
      name,
      select('scope', ['host', 'region', 'workspace']),
      number('concurrency', 100000),
      number('cpu', 100000),
      number('memoryMb', 1048576),
      number('storage'),
    ],
    actions: ['edit', 'disable', 'enable'],
  },
  leases: {
    key: 'leases',
    icon: TimerReset,
    columns: ['name', 'workspaceId', 'runtimeId', 'status', 'expiresAt'],
    statuses: ['active', 'revoked', 'expired'],
    fields: [name, workspace, { key: 'runtimeId' }, { key: 'expiresAt' }],
    actions: ['revoke'],
  },
  nodes: {
    key: 'nodes',
    icon: Network,
    create: true,
    columns: ['name', 'endpoint', 'concurrency', 'status', 'updatedAt'],
    statuses: ['active', 'draining', 'disabled'],
    fields: [
      name,
      { key: 'endpoint', type: 'url', required: true },
      number('concurrency', 10000),
      { key: 'region' },
      { key: 'labels' },
    ],
    actions: ['edit', 'drain', 'enable', 'delete'],
  },
  selfHosted: {
    key: 'selfHosted',
    icon: Network,
    create: true,
    columns: ['name', 'workspaceId', 'endpoint', 'status', 'updatedAt'],
    statuses: ['active', 'draining', 'disabled', 'revoked'],
    fields: [
      name,
      workspace,
      { key: 'endpoint', type: 'url', required: true },
      { key: 'region' },
      number('concurrency', 10000),
    ],
    actions: ['edit', 'drain', 'disable', 'enable', 'revoke'],
    scoped: true,
  },
  queues: {
    key: 'queues',
    icon: Boxes,
    create: true,
    columns: ['name', 'concurrency', 'pending', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      number('concurrency', 10000),
      number('maxRetries', 20),
      number('timeout', 86400),
    ],
    actions: ['edit', 'disable', 'enable'],
  },
  deadletters: {
    key: 'deadletters',
    icon: ShieldAlert,
    columns: ['name', 'taskId', 'attempts', 'reason', 'updatedAt'],
    statuses: ['failed', 'pending'],
    fields: [
      name,
      { key: 'taskId' },
      { key: 'attempts' },
      { key: 'reason', type: 'textarea' },
    ],
    actions: ['retry', 'delete'],
  },
  schedules: {
    key: 'schedules',
    icon: TimerReset,
    create: true,
    columns: ['name', 'workspaceId', 'expression', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      workspace,
      { key: 'expression', required: true },
      { key: 'timezone', required: true },
      number('concurrency', 1000),
    ],
    actions: lifecycleActions,
  },
  calls: {
    key: 'calls',
    icon: Activity,
    columns: ['name', 'workspaceId', 'model', 'status', 'duration', 'tokens'],
    statuses: ['completed', 'failed'],
    fields: [
      name,
      workspace,
      { key: 'model' },
      { key: 'duration' },
      { key: 'tokens' },
      { key: 'cost' },
      { key: 'traceId' },
      { key: 'reason', type: 'textarea' },
    ],
    actions: [],
  },
  logs: {
    key: 'logs',
    icon: FileClock,
    columns: ['name', 'severity', 'workspaceId', 'updatedAt'],
    statuses: ['completed'],
    fields: [
      name,
      workspace,
      { key: 'severity' },
      { key: 'traceId' },
      { key: 'content', type: 'textarea' },
    ],
    actions: [],
  },
  traces: {
    key: 'traces',
    icon: Workflow,
    columns: ['name', 'workspaceId', 'status', 'duration', 'updatedAt'],
    statuses: ['completed', 'failed'],
    fields: [
      name,
      workspace,
      { key: 'duration' },
      { key: 'taskId' },
      { key: 'content', type: 'textarea' },
    ],
    actions: [],
  },
  costs: {
    key: 'costs',
    icon: Wallet,
    columns: ['name', 'workspaceId', 'model', 'tokens', 'cost', 'updatedAt'],
    statuses: ['completed'],
    fields: [
      name,
      workspace,
      { key: 'model' },
      { key: 'tokens' },
      { key: 'cost' },
    ],
    actions: [],
  },
  plugins: {
    key: 'plugins',
    icon: Package,
    create: true,
    columns: ['name', 'publisher', 'version', 'status', 'updatedAt'],
    statuses: ['draft', 'active', 'disabled'],
    fields: [
      name,
      { key: 'source', type: 'url', required: true },
      { key: 'publisher', required: true },
      { key: 'version', required: true },
      { key: 'image', required: true },
      { key: 'credentialRef' },
      select('trust', ['untrusted', 'verified']),
      number('replicas', 100),
    ],
    actions: ['edit', 'publish', 'disable', 'delete'],
  },
  integrations: {
    key: 'integrations',
    icon: PlugZap,
    create: true,
    columns: ['name', 'category', 'endpoint', 'status', 'updatedAt'],
    statuses: ['active', 'disabled'],
    fields: [
      name,
      select('category', ['webhook', 'email', 'oidc']),
      { key: 'endpoint', type: 'url', required: true },
      { key: 'credentialRef', required: true },
      number('timeout', 300),
      number('maxRetries', 20),
    ],
    actions: lifecycleActions,
  },
}

export type NavigationEntry = {
  key: string
  icon: LucideIcon
  path?: string
  children?: { key: string; path: string }[]
}
export const adminNavigation: { key: string; items: NavigationEntry[] }[] = [
  {
    key: 'overview',
    items: [{ key: 'overview', icon: LayoutDashboard, path: 'overview' }],
  },
  {
    key: 'organization',
    items: [
      { key: 'workspaces', icon: Building2, path: 'workspaces' },
      { key: 'userAccess', icon: Users, path: 'users' },
    ],
  },
  {
    key: 'catalogGovernance',
    items: [
      {
        key: 'agentsAndTeams',
        icon: Bot,
        children: [
          { key: 'experts', path: 'experts' },
          { key: 'teams', path: 'teams' },
        ],
      },
      {
        key: 'capabilityCatalog',
        icon: Boxes,
        children: [
          { key: 'tools', path: 'capabilities/tools' },
          { key: 'skills', path: 'capabilities/skills' },
          { key: 'mcp', path: 'mcp' },
        ],
      },
      {
        key: 'plugins',
        icon: Package,
        path: 'extensions/plugins',
      },
    ],
  },
  {
    key: 'operations',
    items: [
      {
        key: 'executionCenter',
        icon: ListTodo,
        children: [
          { key: 'tasks', path: 'execution/tasks' },
          { key: 'runs', path: 'execution/runs' },
        ],
      },
      {
        key: 'runtimeControl',
        icon: ServerCog,
        children: [
          { key: 'runtimeSpaces', path: 'runtime/spaces' },
          { key: 'runtimes', path: 'runtime/runtimes' },
          { key: 'runtimeLeases', path: 'runtime/leases' },
        ],
      },
      {
        key: 'workers',
        icon: Network,
        children: [
          { key: 'allWorkers', path: 'runtime/workers' },
          { key: 'selfHosted', path: 'runtime/self-hosted' },
        ],
      },
      {
        key: 'queues',
        icon: ListTodo,
        children: [
          { key: 'queueOverview', path: 'runtime/queues' },
          { key: 'deadLetters', path: 'runtime/dead-letters' },
        ],
      },
    ],
  },
  {
    key: 'governance',
    items: [
      {
        key: 'policyCenter',
        icon: ShieldCheck,
        path: 'governance/policies',
      },
      {
        key: 'securityEvents',
        icon: ShieldAlert,
        path: 'governance/security',
      },
      {
        key: 'auditEvidence',
        icon: FileClock,
        path: 'governance/audit',
      },
    ],
  },
  {
    key: 'system',
    items: [
      {
        key: 'systemConfiguration',
        icon: Settings2,
        path: 'system/configuration',
      },
      { key: 'serviceStatus', icon: CircleGauge, path: 'system/status' },
    ],
  },
]

export const routes: Record<string, Collection> = {
  users: 'users',
  teams: 'teams',
  orchestrations: 'orchestrations',
  models: 'models',
  'extensions/plugins': 'plugins',
  'extensions/reviews': 'reviews',
  'execution/tasks': 'tasks',
  'execution/runs': 'runs',
  'execution/approvals': 'approvals',
  'execution/automations': 'automations',
  'execution/schedules': 'schedules',
  'runtime/spaces': 'runtimeSpaces',
  'runtime/runtimes': 'runtimes',
  'runtime/leases': 'leases',
  'runtime/workers': 'nodes',
  'runtime/queues': 'queues',
  'runtime/dead-letters': 'deadletters',
  'runtime/self-hosted': 'selfHosted',
  'observability/calls': 'calls',
  'observability/logs': 'logs',
  'observability/traces': 'traces',
  'observability/costs': 'costs',
  'governance/policies': 'policies',
  'governance/security': 'security',
  'governance/audit': 'audit',
  'system/integrations': 'integrations',
}
