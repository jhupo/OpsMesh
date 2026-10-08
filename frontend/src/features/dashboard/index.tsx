import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { workspacesQueryOptions } from '@/api/workspaces'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Main } from '@/components/layout/main'
import { PlatformPageHeading } from '@/features/platform-admin/page-heading'

export function Dashboard() {
  const { t } = useTranslation()
  const workspaces = useQuery(workspacesQueryOptions())
  const { activeWorkspace, selectWorkspace } = useWorkspace()
  return (
    <Main>
      <PlatformPageHeading
        title={t('sidebar.overview')}
        actions={
          activeWorkspace && (
            <Button asChild>
              <Link to='/tasks'>{t('tasks.title')}</Link>
            </Button>
          )
        }
      />
      {workspaces.error ? (
        <Alert variant='destructive'>
          <AlertDescription>{workspaces.error.message}</AlertDescription>
        </Alert>
      ) : workspaces.isPending ? (
        <Skeleton className='h-64 w-full' />
      ) : workspaces.data.items.length === 0 ? (
        <div className='py-16 text-center text-muted-foreground'>
          {t('workspaceConsole.noWorkspace')}
        </div>
      ) : (
        <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-3'>
          {workspaces.data.items.map((workspace) => (
            <Card key={workspace.id}>
              <CardHeader>
                <CardTitle className='truncate'>{workspace.name}</CardTitle>
              </CardHeader>
              <CardContent className='flex flex-wrap items-center justify-between gap-3'>
                <Badge variant='outline'>
                  {t(`workspaceConsole.taskStatus.${workspace.status}`, {
                    defaultValue: workspace.status,
                  })}
                </Badge>
                <Button
                  variant={
                    activeWorkspace?.id === workspace.id
                      ? 'secondary'
                      : 'outline'
                  }
                  onClick={() => selectWorkspace(workspace.id)}
                  disabled={activeWorkspace?.id === workspace.id}
                >
                  {t(
                    activeWorkspace?.id === workspace.id
                      ? 'workspaceConsole.current'
                      : 'workspaceConsole.select'
                  )}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </Main>
  )
}
