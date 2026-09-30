import { createFileRoute } from '@tanstack/react-router'
import { WorkspaceResourcePage } from '@/features/workspace-console'

export const Route = createFileRoute('/_authenticated/workspace/$view')({
  component: RouteComponent,
})

function RouteComponent() {
  const { view } = Route.useParams()
  return <WorkspaceResourcePage view={view} />
}
