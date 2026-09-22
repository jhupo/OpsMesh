import { createFileRoute } from '@tanstack/react-router'
import { PlatformAdminPage } from '@/features/platform-admin'

export const Route = createFileRoute('/_authenticated/admin/$section/')({
  component: RouteComponent,
})

// eslint-disable-next-line react-refresh/only-export-components
function RouteComponent() {
  const { section } = Route.useParams()
  return <PlatformAdminPage section={section} />
}
