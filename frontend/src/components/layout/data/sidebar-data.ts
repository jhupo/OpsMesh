import { useTranslation } from 'react-i18next'
import { useWorkspace } from '@/context/workspace-provider'
import {
  platformNavigation,
  workspaceNavigation,
} from '@/features/navigation/catalog'
import type { SidebarData } from '../types'

export function useSidebarData(): SidebarData {
  const { t } = useTranslation()
  const { access, accessError } = useWorkspace()
  return {
    teams: [],
    navGroups: [
      {
        title: t('navigation.workspaceMode'),
        items: workspaceNavigation
          .map((group) => ({
            title: t(`navigation.${group.key}`),
            icon: group.icon,
            items: group.items
              .filter(
                (item) =>
                  item.url === '/' ||
                  (!accessError &&
                    access?.allowed_actions.includes(item.action))
              )
              .map((item) => ({
                title: t(`navigation.${item.key}`),
                url: item.url,
              })),
          }))
          .filter((group) => group.items.length > 0),
      },
    ],
  }
}
export function usePlatformAdminSidebarData(): SidebarData {
  const { t } = useTranslation()
  return {
    teams: [],
    navGroups: [
      {
        title: t('navigation.adminMode'),
        items: platformNavigation.map((group) => ({
          title: t(`navigation.${group.key}`),
          icon: group.icon,
          items: group.items.map((item) => ({
            title: t(`navigation.${item.key}`),
            url: item.url,
          })),
        })),
      },
    ],
  }
}
