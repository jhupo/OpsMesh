import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { projectsQueryOptions, type Project } from '@/api/projects'
import { useWorkspace } from '@/context/workspace-provider'

const PROJECT_KEY_PREFIX = 'opsmesh.active-project.'

type ProjectContextValue = {
  projects: Project[]
  activeProject: Project | undefined
  isPending: boolean
  selectProject: (projectId: string | null) => void
}

const ProjectContext = React.createContext<ProjectContextValue | null>(null)

export function ProjectProvider({ children }: React.PropsWithChildren) {
  const { activeWorkspace } = useWorkspace()
  const workspaceId = activeWorkspace?.id
  const { data, isPending } = useQuery(projectsQueryOptions(workspaceId))
  const [selectedByWorkspace, setSelectedByWorkspace] = React.useState<
    Record<string, string | null>
  >({})
  const projects = React.useMemo(() => data?.items ?? [], [data?.items])
  const storedProjectId = workspaceId
    ? (selectedByWorkspace[workspaceId] ??
      window.localStorage.getItem(`${PROJECT_KEY_PREFIX}${workspaceId}`))
    : null
  const activeProject = projects.find(
    (project) => project.id === storedProjectId
  )

  const selectProject = React.useCallback(
    (projectId: string | null) => {
      if (!workspaceId) return
      setSelectedByWorkspace((current) => ({
        ...current,
        [workspaceId]: projectId,
      }))
      const key = `${PROJECT_KEY_PREFIX}${workspaceId}`
      if (projectId) window.localStorage.setItem(key, projectId)
      else window.localStorage.removeItem(key)
    },
    [workspaceId]
  )

  const value = React.useMemo(
    () => ({ projects, activeProject, isPending, selectProject }),
    [projects, activeProject, isPending, selectProject]
  )

  return (
    <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useProject() {
  const context = React.useContext(ProjectContext)
  if (!context)
    throw new Error('useProject must be used within ProjectProvider')
  return context
}
