import { Command, LayoutDashboard } from 'lucide-react'
import { type SidebarData } from '../types'

export const sidebarData: SidebarData = {
  teams: [{ name: 'OpsMesh', logo: Command, plan: 'Platform' }],
  navGroups: [
    {
      titleKey: 'navigation.platform',
      items: [
        {
          titleKey: 'navigation.dashboard',
          url: '/',
          icon: LayoutDashboard,
        },
      ],
    },
  ],
}
