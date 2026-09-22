import {
  Activity,
  ArchiveRestore,
  Construction,
  Bot,
  Boxes,
  BrainCircuit,
  Building2,
  FolderKanban,
  Gavel,
  HardDrive,
  LayoutDashboard,
  Bug,
  ListTodo,
  FileX,
  HelpCircle,
  Lock,
  Network,
  Package,
  PlugZap,
  ServerCog,
  ServerOff,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Store,
  TimerReset,
  UserX,
  Users,
  UsersRound,
  Workflow,
  Wrench,
  MessagesSquare,
  AudioWaveform,
  Command,
  GalleryVerticalEnd,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ClerkLogo } from '@/assets/clerk-logo'
import { type SidebarData } from '../types'

export function useSidebarData(): SidebarData {
  const { t } = useTranslation()

  return {
    teams: [
      {
        name: 'Shadcn Admin',
        logo: Command,
        plan: 'Vite + ShadcnUI',
      },
      {
        name: 'Acme Inc',
        logo: GalleryVerticalEnd,
        plan: 'Enterprise',
      },
      {
        name: 'Acme Corp.',
        logo: AudioWaveform,
        plan: 'Startup',
      },
    ],
    navGroups: [
      {
        title: t('sidebar.general'),
        items: [
          {
            title: t('sidebar.dashboard'),
            url: '/',
            icon: LayoutDashboard,
          },
          {
            title: t('sidebar.tasks'),
            url: '/tasks',
            icon: ListTodo,
          },
          {
            title: t('sidebar.apps'),
            url: '/apps',
            icon: Package,
          },
          {
            title: t('sidebar.chats'),
            url: '/chats',
            badge: '3',
            icon: MessagesSquare,
          },
          {
            title: t('sidebar.users'),
            url: '/users',
            icon: Users,
          },
          {
            title: t('sidebar.secured_by_clerk'),
            icon: ClerkLogo,
            items: [
              {
                title: t('sidebar.sign_in'),
                url: '/clerk/sign-in',
              },
              {
                title: t('sidebar.sign_up'),
                url: '/clerk/sign-up',
              },
              {
                title: t('sidebar.user_management'),
                url: '/clerk/user-management',
              },
            ],
          },
        ],
      },
      {
        title: t('sidebar.pages'),
        items: [
          {
            title: t('sidebar.auth'),
            icon: ShieldCheck,
            items: [
              {
                title: t('sidebar.sign_in'),
                url: '/sign-in',
              },
              {
                title: t('sidebar.sign_in_2col'),
                url: '/sign-in-2',
              },
              {
                title: t('sidebar.sign_up'),
                url: '/sign-up',
              },
              {
                title: t('sidebar.forgot_password'),
                url: '/forgot-password',
              },
              {
                title: t('sidebar.otp'),
                url: '/otp',
              },
            ],
          },
          {
            title: t('sidebar.errors'),
            icon: Bug,
            items: [
              {
                title: t('sidebar.unauthorized'),
                url: '/errors/unauthorized',
                icon: Lock,
              },
              {
                title: t('sidebar.forbidden'),
                url: '/errors/forbidden',
                icon: UserX,
              },
              {
                title: t('sidebar.not_found'),
                url: '/errors/not-found',
                icon: FileX,
              },
              {
                title: t('sidebar.internal_server_error'),
                url: '/errors/internal-server-error',
                icon: ServerOff,
              },
              {
                title: t('sidebar.maintenance_error'),
                url: '/errors/maintenance-error',
                icon: Construction,
              },
            ],
          },
        ],
      },
      {
        title: t('sidebar.other'),
        items: [
          {
            title: t('sidebar.help_center'),
            url: '/help-center',
            icon: HelpCircle,
          },
        ],
      },
    ],
  }
}

export function usePlatformAdminSidebarData(): SidebarData {
  const { t } = useTranslation()
  const nav = (key: string) => t(`platformAdmin.navigation.${key}`)

  return {
    teams: [],
    navGroups: [
      {
        title: nav('platform'),
        items: [
          {
            title: nav('overview'),
            url: '/admin/overview',
            icon: LayoutDashboard,
          },
          {
            title: nav('workspaces'),
            url: '/admin/workspaces',
            icon: Building2,
          },
          {
            title: nav('users'),
            url: '/admin/users',
            icon: Users,
          },
        ],
      },
      {
        title: nav('work'),
        items: [
          {
            title: nav('projects'),
            url: '/admin/projects',
            icon: FolderKanban,
          },
          {
            title: nav('tasks'),
            icon: ListTodo,
            items: [
              { title: nav('taskList'), url: '/admin/tasks/list' },
              { title: nav('approvals'), url: '/admin/tasks/approvals' },
              { title: nav('runHistory'), url: '/admin/tasks/runs' },
            ],
          },
        ],
      },
      {
        title: nav('collaboration'),
        items: [
          {
            title: nav('agents'),
            icon: Bot,
            items: [
              { title: nav('agentList'), url: '/admin/agents/list' },
              { title: nav('sessions'), url: '/admin/agents/sessions' },
            ],
          },
          {
            title: nav('teams'),
            icon: UsersRound,
            items: [
              { title: nav('teamList'), url: '/admin/teams/list' },
              { title: nav('runStatus'), url: '/admin/teams/status' },
            ],
          },
          {
            title: nav('orchestration'),
            icon: Workflow,
            items: [
              {
                title: nav('workflows'),
                url: '/admin/orchestration/workflows',
              },
              {
                title: nav('automations'),
                url: '/admin/orchestration/automations',
              },
              {
                title: nav('schedules'),
                url: '/admin/orchestration/schedules',
              },
            ],
          },
        ],
      },
      {
        title: nav('capabilities'),
        items: [
          {
            title: nav('tools'),
            icon: Wrench,
            items: [
              { title: nav('capabilityCatalog'), url: '/admin/tools/catalog' },
              { title: nav('toolGroups'), url: '/admin/tools/groups' },
            ],
          },
          {
            title: nav('mcp'),
            icon: PlugZap,
            items: [
              { title: nav('mcpServices'), url: '/admin/mcp/services' },
              { title: nav('mcpCatalog'), url: '/admin/mcp/catalog' },
            ],
          },
          {
            title: nav('skills'),
            icon: Sparkles,
            items: [
              { title: nav('workspaceSkills'), url: '/admin/skills/workspace' },
              { title: nav('installedSkills'), url: '/admin/skills/installed' },
            ],
          },
          {
            title: nav('plugins'),
            icon: Boxes,
            items: [
              {
                title: nav('installedPlugins'),
                url: '/admin/plugins/installed',
              },
              { title: nav('pluginCenter'), url: '/admin/plugins/center' },
            ],
          },
          {
            title: nav('marketplace'),
            icon: Store,
            items: [
              {
                title: nav('resourceMarket'),
                url: '/admin/marketplace/resources',
              },
              { title: nav('expertMarket'), url: '/admin/marketplace/experts' },
            ],
          },
        ],
      },
      {
        title: nav('data'),
        items: [
          {
            title: nav('knowledgeMemory'),
            icon: BrainCircuit,
            items: [
              { title: nav('knowledgeBases'), url: '/admin/knowledge/bases' },
              { title: nav('memory'), url: '/admin/knowledge/memory' },
            ],
          },
          {
            title: nav('storage'),
            icon: HardDrive,
            items: [
              { title: nav('files'), url: '/admin/storage/files' },
              { title: nav('artifacts'), url: '/admin/storage/artifacts' },
            ],
          },
          {
            title: nav('dataManagement'),
            icon: ArchiveRestore,
            items: [
              { title: nav('importExport'), url: '/admin/data/import-export' },
              {
                title: nav('retentionRecovery'),
                url: '/admin/data/retention-recovery',
              },
            ],
          },
        ],
      },
      {
        title: nav('operations'),
        items: [
          {
            title: nav('runtime'),
            icon: ServerCog,
            items: [
              { title: nav('runtimes'), url: '/admin/runtime/runtimes' },
              { title: nav('runtimeSpaces'), url: '/admin/runtime/spaces' },
              { title: nav('runtimeLeases'), url: '/admin/runtime/leases' },
            ],
          },
          {
            title: nav('compute'),
            icon: Network,
            items: [
              { title: nav('workers'), url: '/admin/compute/workers' },
              { title: nav('workerLeases'), url: '/admin/compute/leases' },
            ],
          },
          {
            title: nav('scheduling'),
            icon: TimerReset,
            items: [
              { title: nav('queues'), url: '/admin/scheduling/queues' },
              {
                title: nav('deadLetters'),
                url: '/admin/scheduling/dead-letters',
              },
              { title: nav('scheduler'), url: '/admin/scheduling/scheduler' },
            ],
          },
          {
            title: nav('observability'),
            url: '/admin/observability/calls',
            icon: Activity,
          },
        ],
      },
      {
        title: nav('governance'),
        items: [
          {
            title: nav('platformPolicies'),
            url: '/admin/policies/high-risk',
            icon: Gavel,
          },
          {
            title: nav('securityEvents'),
            url: '/admin/security-events',
            icon: ShieldAlert,
          },
          {
            title: nav('marketReview'),
            url: '/admin/market-review/resources',
            icon: ShieldCheck,
          },
        ],
      },
      {
        title: nav('system'),
        items: [
          {
            title: nav('systemSettings'),
            url: '/admin/system',
            icon: Settings2,
          },
        ],
      },
    ],
  }
}
