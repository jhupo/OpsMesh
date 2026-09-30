import { NotFoundError } from '@/features/errors/not-found-error'
import { Users } from '@/features/users'
import { CapabilityCenter } from './capabilities'
import { routes as managementRoutes } from './management/catalog'
import { AdminRoutePage } from './management/page'
import { PlatformSystemSettings } from './management/system-settings'
import { PlatformAdminOverview } from './overview'
import { PlatformServiceStatus } from './service-status'
import { PlatformWorkspaces } from './workspaces'

type AdminPageProps = {
  section: string
  view?: string
}

export function PlatformAdminPage({ section, view }: AdminPageProps) {
  if (section === 'users' && !view) {
    return <Users admin />
  }

  if (section === 'workspaces' && !view) {
    return <PlatformWorkspaces />
  }

  const managementPath = [section, view].filter(Boolean).join('/')
  if (managementRoutes[managementPath]) {
    return <AdminRoutePage path={managementPath} />
  }

  if (section === 'overview' && !view) {
    return <PlatformAdminOverview />
  }

  if (section === 'system' && view === 'configuration') {
    return <PlatformSystemSettings />
  }

  if (section === 'system' && view === 'status') {
    return <PlatformServiceStatus />
  }

  if ((section === 'experts' || section === 'mcp') && !view) {
    return <CapabilityCenter kind={section === 'experts' ? 'expert' : 'mcp'} />
  }
  if (section === 'capabilities' && (view === 'tools' || view === 'skills')) {
    return <CapabilityCenter kind={view === 'tools' ? 'tool' : 'skill'} />
  }
  return <NotFoundError />
}
