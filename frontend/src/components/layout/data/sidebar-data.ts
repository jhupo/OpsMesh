import {
  Bot,
  Boxes,
  CheckCircle2,
  Gauge,
  HardDrive,
  LayoutDashboard,
  ListTodo,
  Settings2,
  Workflow,
  Zap,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { adminNavigation } from '@/features/platform-admin/management/catalog'
import { type SidebarData } from '../types'

export function useSidebarData(): SidebarData {
  const { t } = useTranslation()

  return {
    teams: [],
    navGroups: [
      {
        title: t('sidebar.workspace'),
        items: [
          {
            title: t('sidebar.overview'),
            url: '/',
            icon: LayoutDashboard,
          },
          {
            title: t('sidebar.tasks'),
            url: '/tasks',
            icon: ListTodo,
          },
          {
            title: t('workspaceConsole.navigation.approvals'),
            url: '/workspace/approvals',
            icon: CheckCircle2,
          },
          {
            title: t('workspaceConsole.navigation.agentsAndTeams'),
            icon: Bot,
            items: [
              {
                title: t('workspaceConsole.navigation.agents'),
                url: '/workspace/agents',
              },
              {
                title: t('workspaceConsole.navigation.teams'),
                url: '/workspace/teams',
              },
            ],
          },
          {
            title: t('workspaceConsole.navigation.orchestration'),
            url: '/workspace/orchestrations',
            icon: Workflow,
          },
        ],
      },
      {
        title: t('workspaceConsole.navigation.automationGroup'),
        items: [
          {
            title: t('workspaceConsole.navigation.automation'),
            icon: Zap,
            items: [
              {
                title: t('workspaceConsole.navigation.automations'),
                url: '/workspace/automations',
              },
              {
                title: t('workspaceConsole.navigation.schedules'),
                url: '/workspace/schedules',
              },
              {
                title: t('workspaceConsole.navigation.webhooks'),
                url: '/workspace/webhooks',
              },
            ],
          },
        ],
      },
      {
        title: t('workspaceConsole.navigation.capabilitiesGroup'),
        items: [
          {
            title: t('workspaceConsole.navigation.capabilities'),
            icon: Boxes,
            items: [
              {
                title: t('workspaceConsole.navigation.tools'),
                url: '/workspace/tools',
              },
              {
                title: t('workspaceConsole.navigation.skills'),
                url: '/workspace/skills',
              },
              {
                title: t('workspaceConsole.navigation.mcp'),
                url: '/workspace/mcp',
              },
              {
                title: t('workspaceConsole.navigation.plugins'),
                url: '/workspace/plugins',
              },
            ],
          },
        ],
      },
      {
        title: t('workspaceConsole.navigation.resourcesGroup'),
        items: [
          {
            title: t('workspaceConsole.navigation.resources'),
            icon: HardDrive,
            items: [
              {
                title: t('workspaceConsole.navigation.files'),
                url: '/workspace/files',
              },
              {
                title: t('workspaceConsole.navigation.knowledge'),
                url: '/workspace/knowledge',
              },
              {
                title: t('workspaceConsole.navigation.memory'),
                url: '/workspace/memory',
              },
            ],
          },
        ],
      },
      {
        title: t('workspaceConsole.navigation.operationsGroup'),
        items: [
          {
            title: t('workspaceConsole.navigation.runtime'),
            icon: Gauge,
            items: [
              {
                title: t('workspaceConsole.navigation.runtimes'),
                url: '/workspace/runtimes',
              },
              {
                title: t('workspaceConsole.navigation.runtimeSpaces'),
                url: '/workspace/runtime-spaces',
              },
              {
                title: t('workspaceConsole.navigation.workers'),
                url: '/workspace/workers',
              },
              {
                title: t('workspaceConsole.navigation.queues'),
                url: '/workspace/queues',
              },
              {
                title: t('workspaceConsole.navigation.costs'),
                url: '/workspace/costs',
              },
            ],
          },
        ],
      },
      {
        title: t('workspaceConsole.navigation.settingsGroup'),
        items: [
          {
            title: t('workspaceConsole.navigation.workspaceSettings'),
            icon: Settings2,
            items: [
              {
                title: t('workspaceConsole.navigation.members'),
                url: '/workspace/members',
              },
              {
                title: t('workspaceConsole.navigation.projects'),
                url: '/workspace/projects',
              },
              {
                title: t('workspaceConsole.navigation.quotas'),
                url: '/workspace/quotas',
              },
            ],
          },
        ],
      },
      {
        title: t('sidebar.account'),
        items: [
          {
            title: t('sidebar.personalSettings'),
            url: '/settings',
            icon: Settings2,
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
    navGroups: adminNavigation.map((group) => ({
      title: nav(group.key),
      items: group.items.map((item) =>
        item.children
          ? {
              title: nav(item.key),
              icon: item.icon,
              items: item.children.map((child) => ({
                title: nav(child.key),
                url: `/admin/${child.path}`,
              })),
            }
          : {
              title: nav(item.key),
              icon: item.icon,
              url: `/admin/${item.path}`,
            }
      ),
    })),
  }
}
