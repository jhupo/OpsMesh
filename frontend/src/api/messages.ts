import { queryOptions } from '@tanstack/react-query'
import { apiRequest } from './client'

export type AgentMessage = {
  id: string
  workspace_id: string
  thread_id: string
  task_id: string | null
  agent_team_id: string | null
  sender_agent_profile_id: string | null
  recipient_agent_profile_id: string | null
  reply_to_message_id: string | null
  message_type: string
  body: string
  payload: Record<string, unknown>
  status: string
  read_at: string | null
  created_at: string
  updated_at: string
}

export type AgentMailboxSummary = {
  workspace_id: string
  generated_at: string
  thread_count: number
  message_count: number
  unread_count: number
  pending_count: number
  thread_status_counts: Record<string, number>
  message_status_counts: Record<string, number>
  latest_messages_by_task: Array<{ task_id: string; message: AgentMessage }>
  latest_messages_by_team: Array<{ team_id: string; message: AgentMessage }>
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

export async function markAgentMessageRead(
  workspaceId: string,
  messageId: string
) {
  return apiRequest<AgentMessage>(
    `/workspaces/${workspaceId}/agent-message-threads/messages/${messageId}/read`,
    { method: 'POST' }
  )
}
