import { createFileRoute } from '@tanstack/react-router'
import { WorkspaceResourcePage } from '@/features/workspace-console'

export const Route = createFileRoute('/_authenticated/')({
  component: () => <WorkspaceResourcePage view='overview' />,
})
