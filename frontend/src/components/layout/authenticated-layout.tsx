import { useSuspenseQuery } from '@tanstack/react-query'
import { Outlet } from '@tanstack/react-router'
import { currentUserQueryOptions } from '@/api/auth'
import { getCookie } from '@/lib/cookies'
import { LayoutProvider } from '@/context/layout-provider'
import { ProjectProvider } from '@/context/project-provider'
import { SearchProvider } from '@/context/search-provider'
import { WorkspaceProvider } from '@/context/workspace-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { Header } from '@/components/layout/header'
import { SkipToMain } from '@/components/skip-to-main'
import { DeveloperTools } from '@/features/settings/developer-tools'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const { data: user } = useSuspenseQuery(currentUserQueryOptions())
  const defaultOpen = getCookie('sidebar_state') !== 'false'
  const shell = (
    <SearchProvider>
      <LayoutProvider>
        <SidebarProvider defaultOpen={defaultOpen}>
          <SkipToMain />
          <DeveloperTools />
          <AppSidebar platformAdmin={user.platform_admin} />
          <SidebarInset className='h-svh overflow-hidden peer-data-[variant=inset]:md:h-[calc(100svh-(var(--spacing)*4))]'>
            <Header platformAdmin={user.platform_admin} />
            <div
              id='content'
              tabIndex={-1}
              className='@container/content flex min-h-0 flex-1 flex-col overflow-y-auto has-data-[layout=fixed]:overflow-hidden'
            >
              {children ?? <Outlet />}
            </div>
          </SidebarInset>
        </SidebarProvider>
      </LayoutProvider>
    </SearchProvider>
  )

  return (
    <WorkspaceProvider>
      {user.platform_admin ? shell : <ProjectProvider>{shell}</ProjectProvider>}
    </WorkspaceProvider>
  )
}
