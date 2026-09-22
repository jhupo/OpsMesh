import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type AgentMessage = {
  id: string
  body: string
  read_at: string | null
  created_at: string
}

type AgentMailboxSummary = {
  unread_count: number
  latest_messages_by_task: Array<{ message: AgentMessage }>
  latest_messages_by_team: Array<{ message: AgentMessage }>
}

export function agentMailboxSummaryQueryOptions(
  workspaceId: string | undefined
) {
  return queryOptions({
    queryKey: ['agent-messages', workspaceId, 'summary'],
    queryFn: () =>
      apiRequest<AgentMailboxSummary>(
        `/workspaces/${workspaceId}/agent-message-threads/summary?latest_limit=20`
      ),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
  })
}

export function markAgentMessageRead(workspaceId: string, messageId: string) {
  return apiRequest<AgentMessage>(
    `/workspaces/${workspaceId}/agent-message-threads/messages/${messageId}/read`,
    { method: 'POST' }
  )
}
