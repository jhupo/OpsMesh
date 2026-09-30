import { useQuery } from '@tanstack/react-query'
import {
  ListTodo,
  CircleCheck,
  CircleX,
  Timer,
  Bot,
  Users,
  ShieldCheck,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  type PlatformExecutionOverview,
  type PlatformAnalyticsRange,
  platformOperationsSummaryQueryOptions,
} from '@/api/platform-admin'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ReadOnlyDataTable } from '@/components/data-table'
import { OverviewTimeChart } from './overview-time-chart'

export function PlatformOverviewExecution({
  data,
  pending,
  range,
}: {
  data: PlatformExecutionOverview | undefined
  pending: boolean
  range: PlatformAnalyticsRange
}) {
  const { t, i18n } = useTranslation()
  const operations = useQuery({
    ...platformOperationsSummaryQueryOptions(),
    retry: false,
  })
  const format = (value: number | undefined) =>
    value == null ? '—' : new Intl.NumberFormat(i18n.language).format(value)
  const metrics = [
    { key: 'tasksCreated', icon: ListTodo, value: data?.totals?.tasks_created },
    { key: 'tasksRunning', icon: Timer, value: data?.totals?.tasks_running },
    {
      key: 'tasksCompleted',
      icon: CircleCheck,
      value: data?.totals?.tasks_completed,
    },
    { key: 'tasksFailed', icon: CircleX, value: data?.totals?.tasks_failed },
  ]
  const taskConfig = {
    created: {
      label: t('platformAdmin.overview.tasksCreated'),
      color: 'var(--chart-1)',
    },
    completed: {
      label: t('platformAdmin.overview.tasksCompleted'),
      color: 'var(--chart-2)',
    },
    failed: {
      label: t('platformAdmin.overview.tasksFailed'),
      color: 'var(--destructive)',
    },
  }
  const agentConfig = {
    agent_runs: {
      label: t('platformAdmin.overview.agentRuns'),
      color: 'var(--chart-1)',
    },
    team_runs: {
      label: t('platformAdmin.overview.teamRuns'),
      color: 'var(--chart-2)',
    },
  }
  return (
    <>
      <section
        className='grid grid-cols-2 gap-3 lg:grid-cols-4'
        aria-label={t('platformAdmin.overview.execution')}
      >
        {metrics.map(({ key, icon: Icon, value }) => (
          <Card key={key} className='gap-3 py-4'>
            <CardHeader className='px-4'>
              <CardTitle className='flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground'>
                {t(`platformAdmin.overview.${key}`)}
                <Icon className='size-4' aria-hidden='true' />
              </CardTitle>
            </CardHeader>
            <CardContent className='px-4'>
              {pending ? (
                <Skeleton className='h-8 w-16' />
              ) : (
                <strong className='text-3xl font-semibold tabular-nums'>
                  {format(value)}
                </strong>
              )}
            </CardContent>
          </Card>
        ))}
      </section>
      <div className='grid min-w-0 gap-4 lg:grid-cols-2'>
        <Card className='min-w-0 gap-4'>
          <CardHeader>
            <CardTitle className='text-sm'>
              {t('platformAdmin.overview.taskTrend')}
            </CardTitle>
          </CardHeader>
          <CardContent className='flex flex-col gap-4'>
            <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
              {Object.entries(taskConfig).map(([key, item]) => (
                <span key={key} className='flex items-center gap-1.5'>
                  <span
                    className='size-2 rounded-full'
                    style={{ background: item.color }}
                    aria-hidden='true'
                  />
                  {item.label}
                </span>
              ))}
            </div>
            <OverviewTimeChart
              points={data?.timeline}
              config={taskConfig}
              range={range}
              pending={pending}
              height='h-56'
            />
          </CardContent>
        </Card>
        <Card className='min-w-0 gap-4'>
          <CardHeader>
            <CardTitle className='text-sm'>
              {t('platformAdmin.overview.agentTrend')}
            </CardTitle>
          </CardHeader>
          <CardContent className='flex flex-col gap-4'>
            <div className='flex flex-wrap gap-4 text-xs text-muted-foreground'>
              {Object.entries(agentConfig).map(([key, item]) => (
                <span key={key} className='flex items-center gap-1.5'>
                  <span
                    className='size-2 rounded-full'
                    style={{ background: item.color }}
                    aria-hidden='true'
                  />
                  {item.label}
                </span>
              ))}
            </div>
            <OverviewTimeChart
              points={data?.timeline}
              config={agentConfig}
              range={range}
              pending={pending}
              height='h-56'
            />
          </CardContent>
        </Card>
      </div>
      <div className='grid grid-cols-2 gap-4 rounded-xl border bg-card p-4 lg:grid-cols-4'>
        {[
          {
            key: 'agentsRunning',
            icon: Bot,
            value: data?.totals?.agents_running,
          },
          {
            key: 'teamsRunning',
            icon: Users,
            value: data?.totals?.teams_running,
          },
          {
            key: 'approvals',
            icon: ShieldCheck,
            value: operations.data?.approvals?.pending,
          },
          {
            key: 'waitingTasks',
            icon: Timer,
            value: operations.data?.approvals?.tasks_waiting,
          },
        ].map(({ key, icon: Icon, value }) => (
          <div key={key} className='flex items-center gap-2'>
            <Icon
              className='size-4 shrink-0 text-muted-foreground'
              aria-hidden='true'
            />
            <span className='text-xs text-muted-foreground'>
              {t(`platformAdmin.overview.${key}`)}
            </span>
            <strong className='ms-auto text-lg font-semibold tabular-nums'>
              {format(value)}
            </strong>
          </div>
        ))}
      </div>
      <Card className='gap-4'>
        <CardHeader>
          <CardTitle className='text-sm'>
            {t('platformAdmin.overview.recentExecution')}
          </CardTitle>
        </CardHeader>
        <CardContent className='px-0'>
          {pending ? (
            <Skeleton className='h-24 w-full' />
          ) : (
            <ReadOnlyDataTable
              data={data?.recent_runs ?? []}
              columns={[
                {
                  accessorKey: 'task_title',
                  header: t('platformAdmin.overview.tasks'),
                  cell: ({ row }) => (
                    <div
                      className='w-28 truncate sm:w-52'
                      title={row.original.task_title}
                    >
                      {row.original.task_title}
                    </div>
                  ),
                },
                {
                  accessorKey: 'status',
                  header: t('platformAdmin.overview.state'),
                  cell: ({ row }) => (
                    <Badge
                      variant={
                        row.original.status === 'failed'
                          ? 'destructive'
                          : 'secondary'
                      }
                    >
                      {t(
                        `platformAdmin.overview.runStatus.${row.original.status}`
                      )}
                    </Badge>
                  ),
                },
                {
                  accessorKey: 'workspace_name',
                  header: t('platformAdmin.overview.workspace'),
                  cell: ({ row }) => (
                    <div className='max-w-28 truncate sm:max-w-44'>
                      {row.original.workspace_name}
                    </div>
                  ),
                },
                {
                  accessorKey: 'agent_name',
                  header: t('platformAdmin.overview.agent'),
                  cell: ({ row }) => (
                    <div className='max-w-28 truncate sm:max-w-44'>
                      {row.original.agent_name}
                    </div>
                  ),
                },
                {
                  accessorKey: 'team_name',
                  header: t('platformAdmin.overview.team'),
                  cell: ({ row }) => row.original.team_name ?? '—',
                },
                {
                  accessorKey: 'started_at',
                  header: t('platformAdmin.overview.startedAt'),
                  cell: ({ row }) =>
                    new Intl.DateTimeFormat(i18n.language, {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    }).format(new Date(row.original.started_at)),
                },
                {
                  accessorKey: 'duration_ms',
                  header: t('platformAdmin.overview.duration'),
                  cell: ({ row }) =>
                    row.original.duration_ms == null
                      ? '—'
                      : `${format(Math.round(row.original.duration_ms / 1000))}s`,
                },
                {
                  accessorKey: 'tool_calls',
                  header: t('platformAdmin.overview.toolCalls'),
                  cell: ({ row }) => format(row.original.tool_calls),
                },
              ]}
            />
          )}
        </CardContent>
      </Card>
    </>
  )
}
