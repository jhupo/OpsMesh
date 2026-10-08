import { createFileRoute, redirect } from '@tanstack/react-router'
import { WorkspaceResourcePage } from '@/features/workspace-console'

export const Route = createFileRoute('/_authenticated/workspace/$view')({
  beforeLoad: ({ params }) => {
    const aliases: Record<string, { view: string; tab: number }> = {
      'runtime-spaces': { view: 'runtimes', tab: 1 },
      automations: { view: 'integrations', tab: 1 },
      webhooks: { view: 'integrations', tab: 0 },
    }
    const alias = aliases[params.view]
    if (alias)
      throw redirect({
        to: '/workspace/$view',
        params: { view: alias.view },
        search: { tab: alias.tab } as never,
      })
  },
  component: RouteComponent,
})

function RouteComponent() {
  const { view } = Route.useParams()
  return <WorkspaceResourcePage view={view} />
}
