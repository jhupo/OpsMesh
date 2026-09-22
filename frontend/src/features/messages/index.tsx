import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail, MailOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  agentMailboxSummaryQueryOptions,
  markAgentMessageRead,
  type AgentMessage,
} from '@/api/messages'
import { workspacesQueryOptions } from '@/api/workspaces'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'

function formatMessageTime(value: string, language: string) {
  return new Intl.DateTimeFormat(language, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}

function MessageRow({
  message,
  workspaceId,
  onRead,
}: {
  message: AgentMessage
  workspaceId: string | undefined
  onRead: (message: AgentMessage) => void
}) {
  const { t, i18n } = useTranslation()
  const label = message.body || t('common.messages')

  return (
    <button
      type='button'
      className={cn(
        'flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/70 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset disabled:cursor-default disabled:hover:bg-transparent',
        !message.read_at && 'bg-muted/45'
      )}
      onClick={() => {
        if (workspaceId && !message.read_at) onRead(message)
      }}
      disabled={Boolean(message.read_at)}
      aria-label={label}
    >
      <span className='mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground'>
        {message.read_at ? (
          <MailOpen className='size-4' aria-hidden='true' />
        ) : (
          <Mail className='size-4' aria-hidden='true' />
        )}
      </span>
      <span className='min-w-0 flex-1'>
        <span className='flex items-start justify-between gap-3'>
          <span className='truncate text-sm font-medium'>{label}</span>
          <time
            dateTime={message.created_at}
            className='shrink-0 text-xs text-muted-foreground'
          >
            {formatMessageTime(message.created_at, i18n.language)}
          </time>
        </span>
      </span>
    </button>
  )
}

export function MessageCenter() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: workspacePage } = useQuery(workspacesQueryOptions())
  const workspaceId = workspacePage?.items[0]?.id
  const { data: summary, isPending } = useQuery(
    agentMailboxSummaryQueryOptions(workspaceId)
  )
  const markReadMutation = useMutation({
    mutationFn: (message: AgentMessage) =>
      markAgentMessageRead(workspaceId as string, message.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['agent-messages', workspaceId],
      })
    },
  })
  const messages = Array.from(
    new Map(
      [
        ...(summary?.latest_messages_by_task ?? []),
        ...(summary?.latest_messages_by_team ?? []),
      ].map(({ message }) => [message.id, message])
    ).values()
  ).sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  )
  const unreadCount = summary?.unread_count ?? 0
  const loading = Boolean(workspaceId) && isPending
  const hasContent = loading || messages.length > 0
  const listHeight = loading
    ? 224
    : Math.min(Math.max(messages.length * 64, 96), 352)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='relative rounded-full'
          aria-label={t('common.messages')}
        >
          <Mail aria-hidden='true' />
          {unreadCount > 0 && (
            <span
              className='absolute top-1 right-1 size-2 animate-pulse rounded-full bg-destructive ring-2 ring-background motion-reduce:animate-none'
              aria-hidden='true'
            />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align='end'
        sideOffset={8}
        className={cn(
          'overflow-hidden overscroll-contain p-0 shadow-sm',
          hasContent
            ? 'w-[min(22.5rem,calc(100vw-2rem))]'
            : 'w-[min(16rem,calc(100vw-2rem))]'
        )}
      >
        <div className='flex items-center justify-between border-b px-4 py-3'>
          <h2 className='text-base font-semibold'>{t('common.messages')}</h2>
          {unreadCount > 0 && (
            <span className='rounded-full bg-foreground px-2.5 py-1 text-xs font-medium text-background'>
              {unreadCount} {t('messages.new')}
            </span>
          )}
        </div>
        <ScrollArea style={{ height: listHeight }}>
          {loading ? (
            <div className='space-y-1 p-2' aria-busy='true'>
              {Array.from({ length: 3 }, (_, index) => (
                <div key={index} className='flex items-center gap-3 px-2 py-3'>
                  <Skeleton className='size-9 rounded-full' />
                  <div className='min-w-0 flex-1 space-y-2'>
                    <Skeleton className='h-3 w-3/5' />
                    <Skeleton className='h-3 w-4/5' />
                  </div>
                </div>
              ))}
            </div>
          ) : messages.length > 0 ? (
            <div className='divide-y divide-border/70'>
              {messages.map((message) => (
                <MessageRow
                  key={message.id}
                  message={message}
                  workspaceId={workspaceId}
                  onRead={(item) => markReadMutation.mutate(item)}
                />
              ))}
            </div>
          ) : (
            <div className='flex h-24 items-center justify-center text-muted-foreground'>
              <span className='sr-only'>{t('messages.empty')}</span>
              <MailOpen className='size-5' aria-hidden='true' />
            </div>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
