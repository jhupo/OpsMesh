import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { workspacesQueryOptions, type Workspace } from '@/api/workspaces'

const ACTIVE_WORKSPACE_KEY = 'opsmesh.active-workspace'

type WorkspaceContextValue = {
  workspaces: Workspace[]
  activeWorkspace: Workspace | undefined
  selectWorkspace: (workspaceId: string) => void
}

const WorkspaceContext = React.createContext<WorkspaceContextValue | null>(null)

export function WorkspaceProvider({ children }: React.PropsWithChildren) {
  const { data } = useQuery(workspacesQueryOptions())
  const [selectedWorkspaceId, setSelectedWorkspaceId] = React.useState(() =>
    window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)
  )
  const workspaces = React.useMemo(() => data?.items ?? [], [data?.items])
  const activeWorkspace =
    workspaces.find((workspace) => workspace.id === selectedWorkspaceId) ??
    workspaces[0]

  React.useEffect(() => {
    if (!activeWorkspace || activeWorkspace.id === selectedWorkspaceId) return
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspace.id)
  }, [activeWorkspace, selectedWorkspaceId])

  const selectWorkspace = React.useCallback((workspaceId: string) => {
    setSelectedWorkspaceId(workspaceId)
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaceId)
  }, [])

  const value = React.useMemo(
    () => ({ workspaces, activeWorkspace, selectWorkspace }),
    [workspaces, activeWorkspace, selectWorkspace]
  )

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspace() {
  const context = React.useContext(WorkspaceContext)
  if (!context)
    throw new Error('useWorkspace must be used within WorkspaceProvider')
  return context
}
