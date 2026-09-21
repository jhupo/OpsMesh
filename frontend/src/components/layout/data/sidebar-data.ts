import { LayoutDashboard } from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  navGroups: [
    {
      titleKey: 'navigation.platform',
      items: [
        {
          titleKey: 'navigation.overview',
          url: '/',
          icon: LayoutDashboard,
        },
      ],
    },
  ],
}
