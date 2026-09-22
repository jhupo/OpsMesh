import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, MailOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  agentMailboxSummaryQueryOptions,
  markAgentMessageRead,
  type AgentMessage,
} from '@/api/messages'
import { cn } from '@/lib/utils'
import { useWorkspace } from '@/context/workspace-provider'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'

export function MessageCenter() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { activeWorkspace } = useWorkspace()
  const workspaceId = activeWorkspace?.id
  const { data, isPending } = useQuery(
    agentMailboxSummaryQueryOptions(workspaceId)
  )
  const messages = Array.from(
    new Map(
      [
        ...(data?.latest_messages_by_task ?? []),
        ...(data?.latest_messages_by_team ?? []),
      ].map(({ message }) => [message.id, message])
    ).values()
  ).sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  )
  const unreadCount = data?.unread_count ?? 0
  const loading = Boolean(workspaceId) && isPending
  const markRead = useMutation({
    mutationFn: (message: AgentMessage) =>
      markAgentMessageRead(workspaceId as string, message.id),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['agent-messages', workspaceId],
      }),
  })

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='relative rounded-full'
          aria-label={t('opsmesh.messages')}
        >
          <Mail />
          {unreadCount > 0 && (
            <span className='absolute top-1 right-1 size-2 animate-pulse rounded-full bg-destructive ring-2 ring-background motion-reduce:animate-none' />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align='end'
        sideOffset={8}
        className={cn(
          'overflow-hidden p-0',
          loading || messages.length > 0 ? 'w-90' : 'w-64'
        )}
      >
        <div className='flex items-center justify-between border-b px-4 py-3'>
          <h2 className='font-semibold'>{t('opsmesh.messages')}</h2>
          {unreadCount > 0 && (
            <span className='rounded-full bg-foreground px-2 py-0.5 text-xs text-background'>
              {unreadCount}
            </span>
          )}
        </div>
        <ScrollArea
          className={cn(loading ? 'h-56' : messages.length ? 'h-80' : 'h-24')}
        >
          {loading ? (
            <LoadingRows />
          ) : messages.length ? (
            <div className='divide-y'>
              {messages.map((message) => (
                <button
                  key={message.id}
                  type='button'
                  disabled={Boolean(message.read_at)}
                  className={cn(
                    'flex w-full items-start gap-3 px-4 py-3 text-start hover:bg-muted/70 disabled:cursor-default',
                    !message.read_at && 'bg-muted/45'
                  )}
                  onClick={() => markRead.mutate(message)}
                >
                  <span className='mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground'>
                    {message.read_at ? <MailOpen /> : <Mail />}
                  </span>
                  <span className='min-w-0 flex-1 truncate text-sm font-medium'>
                    {message.body || t('opsmesh.messages')}
                  </span>
                  <time className='shrink-0 text-xs text-muted-foreground'>
                    {new Intl.DateTimeFormat(i18n.language, {
                      hour: 'numeric',
                      minute: '2-digit',
                    }).format(new Date(message.created_at))}
                  </time>
                </button>
              ))}
            </div>
          ) : (
            <div className='flex h-24 items-center justify-center text-muted-foreground'>
              <MailOpen />
              <span className='sr-only'>{t('opsmesh.messages')}</span>
            </div>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}

function LoadingRows() {
  return (
    <div className='space-y-1 p-2' aria-busy='true'>
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className='flex items-center gap-3 px-2 py-3'>
          <Skeleton className='size-9 rounded-full' />
          <Skeleton className='h-3 flex-1' />
        </div>
      ))}
    </div>
  )
}
