import { useLayout } from '@/context/layout-provider'
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarRail,
} from '@/components/ui/sidebar'
import { AppTitle } from './app-title'
import {
  usePlatformAdminSidebarData,
  useSidebarData,
} from './data/sidebar-data'
import { NavGroup } from './nav-group'
import { TeamSwitcher } from './team-switcher'

type AppSidebarProps = {
  platformAdmin: boolean
}

export function AppSidebar({ platformAdmin }: AppSidebarProps) {
  const { collapsible, variant } = useLayout()
  const workspaceSidebarData = useSidebarData()
  const platformSidebarData = usePlatformAdminSidebarData()
  const sidebarData = platformAdmin ? platformSidebarData : workspaceSidebarData
  return (
    <Sidebar collapsible={collapsible} variant={variant}>
      <SidebarHeader>
        {platformAdmin ? (
          <AppTitle />
        ) : (
          <TeamSwitcher teams={sidebarData.teams} />
        )}
      </SidebarHeader>
      <SidebarContent>
        {sidebarData.navGroups.map((props) => (
          <NavGroup key={props.title} {...props} />
        ))}
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
