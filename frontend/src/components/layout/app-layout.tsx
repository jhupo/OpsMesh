import { Outlet } from '@tanstack/react-router'
import { getCookie } from '@/lib/cookies'
import { cn } from '@/lib/utils'
import { LayoutProvider } from '@/context/layout-provider'
import { ProjectProvider } from '@/context/project-provider'
import { WorkspaceProvider } from '@/context/workspace-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { SkipToMain } from '@/components/skip-to-main'
import { AppSidebar } from './app-sidebar'
import { Header } from './header'

export function AppLayout() {
  const defaultOpen = getCookie('sidebar_state') !== 'false'

  return (
    <WorkspaceProvider>
      <ProjectProvider>
        <LayoutProvider>
          <SidebarProvider defaultOpen={defaultOpen}>
            <SkipToMain />
            <AppSidebar />
            <SidebarInset
              className={cn(
                '@container/content',
                'has-data-[layout=fixed]:h-svh',
                'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]'
              )}
            >
              <Header fixed />
              <Outlet />
            </SidebarInset>
          </SidebarProvider>
        </LayoutProvider>
      </ProjectProvider>
    </WorkspaceProvider>
  )
}
