import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Main } from '@/components/layout/main'
import { NotFoundError } from '@/features/errors/not-found-error'
import { PlatformAdminOverview } from './overview'

type AdminPageProps = {
  section: string
  view?: string
}

type TabDefinition = {
  value: string
  label: string
}

type TabbedSection = {
  title: string
  tabs: TabDefinition[]
}

const directSections: Record<string, string> = {
  overview: 'overview',
  workspaces: 'workspaces',
  users: 'users',
  projects: 'projects',
  'security-events': 'securityEvents',
  system: 'systemSettings',
}

const nestedSections: Record<string, Record<string, string>> = {
  tasks: {
    list: 'taskList',
    approvals: 'approvals',
    runs: 'runHistory',
  },
  agents: {
    list: 'agentList',
    sessions: 'sessions',
  },
  teams: {
    list: 'teamList',
    status: 'runStatus',
  },
  orchestration: {
    workflows: 'workflows',
    automations: 'automations',
    schedules: 'schedules',
  },
  tools: {
    catalog: 'capabilityCatalog',
    groups: 'toolGroups',
  },
  mcp: {
    services: 'mcpServices',
    catalog: 'mcpCatalog',
  },
  skills: {
    workspace: 'workspaceSkills',
    installed: 'installedSkills',
  },
  plugins: {
    installed: 'installedPlugins',
    center: 'pluginCenter',
  },
  marketplace: {
    resources: 'resourceMarket',
    experts: 'expertMarket',
  },
  knowledge: {
    bases: 'knowledgeBases',
    memory: 'memory',
  },
  storage: {
    files: 'files',
    artifacts: 'artifacts',
  },
  data: {
    'import-export': 'importExport',
    'retention-recovery': 'retentionRecovery',
  },
  runtime: {
    runtimes: 'runtimes',
    spaces: 'runtimeSpaces',
    leases: 'runtimeLeases',
  },
  compute: {
    workers: 'workers',
    leases: 'workerLeases',
  },
  scheduling: {
    queues: 'queues',
    'dead-letters': 'deadLetters',
    scheduler: 'scheduler',
  },
}

const tabbedSections: Record<string, TabbedSection> = {
  observability: {
    title: 'observability',
    tabs: [
      { value: 'calls', label: 'calls' },
      { value: 'logs', label: 'logs' },
      { value: 'traces', label: 'traces' },
      { value: 'audit', label: 'auditLogs' },
      { value: 'costs', label: 'costAnalysis' },
    ],
  },
  policies: {
    title: 'platformPolicies',
    tabs: [
      { value: 'high-risk', label: 'highRiskExecution' },
      { value: 'workers', label: 'workerControl' },
      { value: 'changes', label: 'changeHistory' },
    ],
  },
  'market-review': {
    title: 'marketReview',
    tabs: [
      { value: 'resources', label: 'resourceReview' },
      { value: 'experts', label: 'expertReview' },
      { value: 'publishers', label: 'publishers' },
    ],
  },
}

export function PlatformAdminPage({ section, view }: AdminPageProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const tabbed = tabbedSections[section]
  const directTitle = view ? undefined : directSections[section]
  const nestedTitle = view ? nestedSections[section]?.[view] : undefined

  if (section === 'overview' && !view) {
    return <PlatformAdminOverview />
  }

  if (tabbed) {
    const activeTab = tabbed.tabs.find((tab) => tab.value === view)
    if (!activeTab) return <NotFoundError />

    return (
      <Main className='flex flex-1 flex-col gap-6'>
        <h1 className='text-2xl font-bold tracking-tight text-balance'>
          {t(`platformAdmin.navigation.${tabbed.title}`)}
        </h1>
        <Tabs
          value={activeTab.value}
          onValueChange={(nextView) =>
            void navigate({
              to: '/admin/$section/$view',
              params: { section, view: nextView },
            })
          }
        >
          <div className='w-full overflow-x-auto pb-1'>
            <TabsList>
              {tabbed.tabs.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  {t(`platformAdmin.navigation.${tab.label}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
        </Tabs>
      </Main>
    )
  }

  const title = directTitle ?? nestedTitle
  if (!title) return <NotFoundError />

  return (
    <Main className='flex flex-1 flex-col gap-6'>
      <h1 className='text-2xl font-bold tracking-tight text-balance'>
        {t(`platformAdmin.navigation.${title}`)}
      </h1>
    </Main>
  )
}
