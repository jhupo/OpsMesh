import {
  Bot,
  Boxes,
  Gauge,
  HardDrive,
  LayoutDashboard,
  ListTodo,
  Settings2,
  ShieldCheck,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
// Product destinations shared by navigation, search and route resolution.
import type { WorkspaceAction } from '@/api/workspace-access'

export type Destination = {
  titleKey: string
  path: string
  tabs: string[]
  action: WorkspaceAction
}
export type NavigationSection = {
  key: string
  icon: LucideIcon
  items: { key: string; url: string; action: WorkspaceAction }[]
}
export const workspaceDestinations: Record<string, Destination> = {
  overview: {
    titleKey: 'workspace_overview_page',
    path: 'health',
    tabs: ['health/snapshots', 'health/trends'],
    action: 'read',
  },
  approvals: {
    titleKey: 'workspace_approvals_page',
    path: 'approvals',
    tabs: [],
    action: 'approve',
  },
  notifications: {
    titleKey: 'workspace_notifications_page',
    path: 'notifications',
    tabs: [],
    action: 'read',
  },
  agents: {
    titleKey: 'workspace_agents_page',
    path: 'agents',
    tabs: [],
    action: 'read',
  },
  teams: {
    titleKey: 'workspace_teams_page',
    path: 'teams',
    tabs: [],
    action: 'read',
  },
  sessions: {
    titleKey: 'workspace_sessions_page',
    path: 'agents',
    tabs: [],
    action: 'read',
  },
  messages: {
    titleKey: 'workspace_messages_page',
    path: 'agent-message-threads',
    tabs: [],
    action: 'read',
  },
  models: {
    titleKey: 'workspace_models_page',
    path: 'model-provider-credentials',
    tabs: ['model-provider-capabilities'],
    action: 'read',
  },
  tasks: {
    titleKey: 'workspace_tasks_page',
    path: 'tasks',
    tabs: [],
    action: 'read',
  },
  orchestrations: {
    titleKey: 'workspace_orchestrations_page',
    path: 'orchestrations',
    tabs: [],
    action: 'read',
  },
  runs: {
    titleKey: 'workspace_runs_page',
    path: 'runs',
    tabs: [],
    action: 'read',
  },
  schedules: {
    titleKey: 'workspace_schedules_page',
    path: 'scheduled-jobs',
    tabs: [],
    action: 'read',
  },
  projects: {
    titleKey: 'workspace_projects_page',
    path: 'projects',
    tabs: [],
    action: 'read',
  },
  domains: {
    titleKey: 'workspace_domains_page',
    path: 'domain-projects',
    tabs: ['domain-items'],
    action: 'read',
  },
  files: {
    titleKey: 'workspace_files_page',
    path: 'files',
    tabs: [],
    action: 'read',
  },
  artifacts: {
    titleKey: 'workspace_artifacts_page',
    path: 'artifacts',
    tabs: ['artifacts/history', 'artifacts/final-output/history'],
    action: 'read',
  },
  knowledge: {
    titleKey: 'workspace_knowledge_page',
    path: 'knowledge/sources',
    tabs: [],
    action: 'read',
  },
  memory: {
    titleKey: 'workspace_memory_page',
    path: 'memories/semantic',
    tabs: [],
    action: 'read',
  },
  skills: {
    titleKey: 'workspace_skills_page',
    path: 'capabilities/workspace-skills',
    tabs: [],
    action: 'read',
  },
  tools: {
    titleKey: 'workspace_tools_page',
    path: 'capabilities',
    tabs: [],
    action: 'read',
  },
  mcp: {
    titleKey: 'workspace_mcp_page',
    path: 'capabilities/mcp-servers',
    tabs: ['capabilities/mcp-credentials'],
    action: 'read',
  },
  plugins: {
    titleKey: 'workspace_plugins_page',
    path: 'plugins',
    tabs: [],
    action: 'read',
  },
  market: {
    titleKey: 'workspace_market_page',
    path: '/marketplace?listing_type=skill',
    tabs: ['/talent-market', 'marketplace-installs', 'talent-installs'],
    action: 'read',
  },
  runtimes: {
    titleKey: 'workspace_runtimes_page',
    path: 'runtimes',
    tabs: ['runtime-spaces', 'runtime-templates'],
    action: 'operate',
  },
  workers: {
    titleKey: 'workspace_workers_page',
    path: 'self-hosted/workers/trust',
    tabs: ['operations/self-hosted-machines'],
    action: 'operate',
  },
  diagnostics: {
    titleKey: 'workspace_diagnostics_page',
    path: 'operations/control-plane',
    tabs: [
      'operations/failed-runs',
      'operations/stale-runs',
      'operations/capacity',
      'operations/runtime-capacity',
      'operations/model-providers',
      'operations/mcp-jobs',
    ],
    action: 'operate',
  },
  queues: {
    titleKey: 'workspace_queues_page',
    path: 'operations/scheduler',
    tabs: [
      'operations/blocked-steps',
      'operations/queue-metrics',
      'operations/queue-insights',
      'operations/dead-letter-jobs',
    ],
    action: 'operate',
  },
  security: {
    titleKey: 'workspace_security_page',
    path: 'operations/security-events',
    tabs: [],
    action: 'operate',
  },
  audit: {
    titleKey: 'workspace_audit_page',
    path: 'operations/audit-events',
    tabs: ['operations/audit-integrity'],
    action: 'operate',
  },
  costs: {
    titleKey: 'workspace_costs_page',
    path: 'costs/summary',
    tabs: [],
    action: 'read',
  },
  usage: {
    titleKey: 'workspace_usage_page',
    path: 'costs/usage',
    tabs: [],
    action: 'read',
  },
  budget: {
    titleKey: 'workspace_budget_page',
    path: 'costs/budget',
    tabs: [],
    action: 'read',
  },
  pricing: {
    titleKey: 'workspace_pricing_page',
    path: 'costs/pricing-rules',
    tabs: [],
    action: 'read',
  },
  settings: {
    titleKey: 'workspace_settings_page',
    path: '',
    tabs: [],
    action: 'read',
  },
  members: {
    titleKey: 'workspace_members_page',
    path: 'members',
    tabs: [],
    action: 'manage_members',
  },
  quotas: {
    titleKey: 'workspace_quotas_page',
    path: 'quotas',
    tabs: [],
    action: 'admin',
  },
  integrations: {
    titleKey: 'workspace_integrations_page',
    path: 'webhook-subscriptions',
    tabs: ['automations'],
    action: 'read',
  },
  data: {
    titleKey: 'workspace_data_page',
    path: 'exports/lifecycle-diagnostics',
    tabs: ['exports/recovery-readiness'],
    action: 'read',
  },
  'notification-preferences': {
    titleKey: 'workspace_notification-preferences_page',
    path: 'notifications/preferences',
    tabs: [],
    action: 'read',
  },
}
export const adminDestinations: Record<string, Destination> = {
  overview: {
    titleKey: 'admin_overview_page',
    path: 'overview',
    tabs: [],
    action: 'read',
  },
  health: {
    titleKey: 'admin_health_page',
    path: 'operations/history',
    tabs: ['operations/summary'],
    action: 'read',
  },
  users: {
    titleKey: 'admin_users_page',
    path: 'users',
    tabs: [],
    action: 'read',
  },
  invitations: {
    titleKey: 'admin_invitations_page',
    path: 'users',
    tabs: [],
    action: 'read',
  },
  workspaces: {
    titleKey: 'admin_workspaces_page',
    path: 'workspaces',
    tabs: [],
    action: 'read',
  },
  catalog: {
    titleKey: 'admin_catalog_page',
    path: 'catalog/tool',
    tabs: ['catalog/skill', 'catalog/mcp_server', 'catalog/agent'],
    action: 'read',
  },
  market: {
    titleKey: 'admin_market_page',
    path: 'catalog/marketplace_listing',
    tabs: [],
    action: 'read',
  },
  reviews: {
    titleKey: 'admin_reviews_page',
    path: 'marketplace/reviews',
    tabs: [],
    action: 'read',
  },
  plugins: {
    titleKey: 'admin_plugins_page',
    path: 'catalog/plugin',
    tabs: [],
    action: 'read',
  },
  workers: {
    titleKey: 'admin_workers_page',
    path: 'workers',
    tabs: [],
    action: 'operate',
  },
  runtimes: {
    titleKey: 'admin_runtimes_page',
    path: 'runtimes',
    tabs: ['runtime-spaces', 'runtime-leases'],
    action: 'operate',
  },
  queues: {
    titleKey: 'admin_queues_page',
    path: 'operations/summary',
    tabs: [],
    action: 'operate',
  },
  diagnostics: {
    titleKey: 'admin_diagnostics_page',
    path: 'operations/runs?kind=failed',
    tabs: ['operations/runs?kind=stale'],
    action: 'operate',
  },
  scheduler: {
    titleKey: 'admin_scheduler_page',
    path: 'workspaces/{workspace}/operations/scheduler',
    tabs: [],
    action: 'operate',
  },
  requests: {
    titleKey: 'admin_requests_page',
    path: 'operations/requests',
    tabs: [],
    action: 'operate',
  },
  costs: {
    titleKey: 'admin_costs_page',
    path: 'costs/summary',
    tabs: [],
    action: 'read',
  },
  'workspace-costs': {
    titleKey: 'admin_workspace-costs_page',
    path: 'costs/summary?group_by=workspace',
    tabs: [],
    action: 'read',
  },
  'model-costs': {
    titleKey: 'admin_model-costs_page',
    path: 'costs/summary?group_by=model',
    tabs: ['costs/summary?group_by=provider', 'costs/summary?group_by=day'],
    action: 'read',
  },
  policies: {
    titleKey: 'admin_policies_page',
    path: 'platform-policies',
    tabs: [],
    action: 'read',
  },
  security: {
    titleKey: 'admin_security_page',
    path: 'security-events',
    tabs: [],
    action: 'read',
  },
  audit: {
    titleKey: 'admin_audit_page',
    path: 'system/logs',
    tabs: [],
    action: 'read',
  },
  integrity: {
    titleKey: 'admin_integrity_page',
    path: 'operations/audit-integrity',
    tabs: [],
    action: 'read',
  },
  configuration: {
    titleKey: 'admin_configuration_page',
    path: 'system/configuration',
    tabs: [],
    action: 'read',
  },
  mail: {
    titleKey: 'admin_mail_page',
    path: 'system/mail',
    tabs: [],
    action: 'read',
  },
  announcements: {
    titleKey: 'admin_announcements_page',
    path: 'announcements',
    tabs: [],
    action: 'read',
  },
  updates: {
    titleKey: 'admin_updates_page',
    path: 'system/version',
    tabs: ['system/check-updates'],
    action: 'read',
  },
}
export const workspaceNavigation: NavigationSection[] = [
  {
    key: 'workspace_workbench',
    icon: LayoutDashboard,
    items: [
      {
        key: 'workspace_overview_page',
        url: '/',
        action: 'read',
      },
      {
        key: 'workspace_approvals_page',
        url: '/workspace/approvals',
        action: 'approve',
      },
      {
        key: 'workspace_notifications_page',
        url: '/workspace/notifications',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_agents',
    icon: Bot,
    items: [
      {
        key: 'workspace_agents_page',
        url: '/workspace/agents',
        action: 'read',
      },
      {
        key: 'workspace_teams_page',
        url: '/workspace/teams',
        action: 'read',
      },
      {
        key: 'workspace_sessions_page',
        url: '/workspace/sessions',
        action: 'read',
      },
      {
        key: 'workspace_messages_page',
        url: '/workspace/messages',
        action: 'read',
      },
      {
        key: 'workspace_models_page',
        url: '/workspace/models',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_tasks',
    icon: ListTodo,
    items: [
      {
        key: 'workspace_tasks_page',
        url: '/tasks',
        action: 'read',
      },
      {
        key: 'workspace_orchestrations_page',
        url: '/workspace/orchestrations',
        action: 'read',
      },
      {
        key: 'workspace_runs_page',
        url: '/workspace/runs',
        action: 'read',
      },
      {
        key: 'workspace_schedules_page',
        url: '/workspace/schedules',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_resources',
    icon: HardDrive,
    items: [
      {
        key: 'workspace_projects_page',
        url: '/workspace/projects',
        action: 'read',
      },
      {
        key: 'workspace_domains_page',
        url: '/workspace/domains',
        action: 'read',
      },
      {
        key: 'workspace_files_page',
        url: '/workspace/files',
        action: 'read',
      },
      {
        key: 'workspace_artifacts_page',
        url: '/workspace/artifacts',
        action: 'read',
      },
      {
        key: 'workspace_knowledge_page',
        url: '/workspace/knowledge',
        action: 'read',
      },
      {
        key: 'workspace_memory_page',
        url: '/workspace/memory',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_capabilities',
    icon: Boxes,
    items: [
      {
        key: 'workspace_skills_page',
        url: '/workspace/skills',
        action: 'read',
      },
      {
        key: 'workspace_tools_page',
        url: '/workspace/tools',
        action: 'read',
      },
      {
        key: 'workspace_mcp_page',
        url: '/workspace/mcp',
        action: 'read',
      },
      {
        key: 'workspace_plugins_page',
        url: '/workspace/plugins',
        action: 'read',
      },
      {
        key: 'workspace_market_page',
        url: '/workspace/market',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_operations',
    icon: Gauge,
    items: [
      {
        key: 'workspace_runtimes_page',
        url: '/workspace/runtimes',
        action: 'operate',
      },
      {
        key: 'workspace_workers_page',
        url: '/workspace/workers',
        action: 'operate',
      },
      {
        key: 'workspace_diagnostics_page',
        url: '/workspace/diagnostics',
        action: 'operate',
      },
      {
        key: 'workspace_queues_page',
        url: '/workspace/queues',
        action: 'operate',
      },
      {
        key: 'workspace_security_page',
        url: '/workspace/security',
        action: 'operate',
      },
      {
        key: 'workspace_audit_page',
        url: '/workspace/audit',
        action: 'operate',
      },
    ],
  },
  {
    key: 'workspace_costs',
    icon: Wallet,
    items: [
      {
        key: 'workspace_costs_page',
        url: '/workspace/costs',
        action: 'read',
      },
      {
        key: 'workspace_usage_page',
        url: '/workspace/usage',
        action: 'read',
      },
      {
        key: 'workspace_budget_page',
        url: '/workspace/budget',
        action: 'read',
      },
      {
        key: 'workspace_pricing_page',
        url: '/workspace/pricing',
        action: 'read',
      },
    ],
  },
  {
    key: 'workspace_settings',
    icon: Settings2,
    items: [
      {
        key: 'workspace_settings_page',
        url: '/workspace/settings',
        action: 'read',
      },
      {
        key: 'workspace_members_page',
        url: '/workspace/members',
        action: 'manage_members',
      },
      {
        key: 'workspace_quotas_page',
        url: '/workspace/quotas',
        action: 'admin',
      },
      {
        key: 'workspace_integrations_page',
        url: '/workspace/integrations',
        action: 'read',
      },
      {
        key: 'workspace_data_page',
        url: '/workspace/data',
        action: 'read',
      },
      {
        key: 'workspace_notification-preferences_page',
        url: '/workspace/notification-preferences',
        action: 'read',
      },
    ],
  },
]
export const platformNavigation: NavigationSection[] = [
  {
    key: 'admin_overview',
    icon: LayoutDashboard,
    items: [
      {
        key: 'admin_overview_page',
        url: '/admin/overview',
        action: 'read',
      },
      {
        key: 'admin_health_page',
        url: '/admin/overview/health',
        action: 'read',
      },
    ],
  },
  {
    key: 'admin_organization',
    icon: Users,
    items: [
      {
        key: 'admin_users_page',
        url: '/admin/users',
        action: 'read',
      },
      {
        key: 'admin_invitations_page',
        url: '/admin/organization/invitations',
        action: 'read',
      },
      {
        key: 'admin_workspaces_page',
        url: '/admin/workspaces',
        action: 'read',
      },
    ],
  },
  {
    key: 'admin_governance',
    icon: Boxes,
    items: [
      {
        key: 'admin_catalog_page',
        url: '/admin/governance/catalog',
        action: 'read',
      },
      {
        key: 'admin_market_page',
        url: '/admin/governance/market',
        action: 'read',
      },
      {
        key: 'admin_reviews_page',
        url: '/admin/governance/reviews',
        action: 'read',
      },
      {
        key: 'admin_plugins_page',
        url: '/admin/governance/plugins',
        action: 'read',
      },
    ],
  },
  {
    key: 'admin_operations',
    icon: Gauge,
    items: [
      {
        key: 'admin_workers_page',
        url: '/admin/operations/workers',
        action: 'operate',
      },
      {
        key: 'admin_runtimes_page',
        url: '/admin/operations/runtimes',
        action: 'operate',
      },
      {
        key: 'admin_queues_page',
        url: '/admin/operations/queues',
        action: 'operate',
      },
      {
        key: 'admin_diagnostics_page',
        url: '/admin/operations/diagnostics',
        action: 'operate',
      },
      {
        key: 'admin_scheduler_page',
        url: '/admin/operations/scheduler',
        action: 'operate',
      },
      {
        key: 'admin_requests_page',
        url: '/admin/operations/requests',
        action: 'operate',
      },
    ],
  },
  {
    key: 'admin_costs',
    icon: Wallet,
    items: [
      {
        key: 'admin_costs_page',
        url: '/admin/costs/costs',
        action: 'read',
      },
      {
        key: 'admin_workspace-costs_page',
        url: '/admin/costs/workspace-costs',
        action: 'read',
      },
      {
        key: 'admin_model-costs_page',
        url: '/admin/costs/model-costs',
        action: 'read',
      },
    ],
  },
  {
    key: 'admin_security',
    icon: ShieldCheck,
    items: [
      {
        key: 'admin_policies_page',
        url: '/admin/security/policies',
        action: 'read',
      },
      {
        key: 'admin_security_page',
        url: '/admin/security/security',
        action: 'read',
      },
      {
        key: 'admin_audit_page',
        url: '/admin/security/audit',
        action: 'read',
      },
      {
        key: 'admin_integrity_page',
        url: '/admin/security/integrity',
        action: 'read',
      },
    ],
  },
  {
    key: 'admin_system',
    icon: Settings2,
    items: [
      {
        key: 'admin_configuration_page',
        url: '/admin/system/configuration',
        action: 'read',
      },
      {
        key: 'admin_mail_page',
        url: '/admin/system/mail',
        action: 'read',
      },
      {
        key: 'admin_announcements_page',
        url: '/admin/system/announcements',
        action: 'read',
      },
      {
        key: 'admin_updates_page',
        url: '/admin/system/updates',
        action: 'read',
      },
    ],
  },
]

export function workspaceDatasetAction(path: string): WorkspaceAction {
  if (
    [
      'operations/control-plane',
      'operations/stale-runs',
      'operations/capacity',
      'operations/runtime-capacity',
      'operations/model-providers',
      'operations/mcp-jobs',
      'operations/self-hosted-machines',
      'operations/scheduler',
      'operations/blocked-steps',
      'operations/audit-integrity',
    ].includes(path)
  )
    return 'admin'
  if (path.startsWith('self-hosted/') || path === 'quotas')
    return 'manage_runtime'
  if (path.startsWith('operations/')) return 'operate'
  if (path === 'approvals') return 'approve'
  if (['members', 'invites'].includes(path)) return 'manage_members'
  return 'read'
}
workspaceDestinations.members.tabs = ['invites']
workspaceDestinations.runtimes.action = 'read'
workspaceDestinations.workers.action = 'manage_runtime'
workspaceDestinations.quotas.action = 'manage_runtime'
for (const group of workspaceNavigation)
  for (const item of group.items) {
    const view = item.url.split('/').pop() ?? ''
    if (workspaceDestinations[view])
      item.action = workspaceDestinations[view].action
  }
