import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  markNotificationRead,
  notificationCountsQueryOptions,
  notificationsQueryOptions,
  type NotificationItem,
} from '@/api/notifications'
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

export function NotificationCenter() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { activeWorkspace } = useWorkspace()
  const workspaceId = activeWorkspace?.id
  const { data, isPending } = useQuery(notificationsQueryOptions(workspaceId))
  const { data: counts } = useQuery(notificationCountsQueryOptions(workspaceId))
  const items = data?.items ?? []
  const unreadCount = counts?.unread_count ?? 0
  const loading = Boolean(workspaceId) && isPending
  const markRead = useMutation({
    mutationFn: (item: NotificationItem) =>
      markNotificationRead(workspaceId as string, item.id),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['notifications', workspaceId],
      }),
  })

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='relative rounded-full'
          aria-label={t('opsmesh.notifications')}
        >
          <Bell />
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
          loading || items.length > 0 ? 'w-90' : 'w-64'
        )}
      >
        <div className='flex items-center justify-between border-b px-4 py-3'>
          <h2 className='font-semibold'>{t('opsmesh.notifications')}</h2>
          {unreadCount > 0 && (
            <span className='rounded-full bg-foreground px-2 py-0.5 text-xs text-background'>
              {unreadCount}
            </span>
          )}
        </div>
        <ScrollArea
          className={cn(loading ? 'h-72' : items.length ? 'h-80' : 'h-24')}
        >
          {loading ? (
            <LoadingRows />
          ) : items.length ? (
            <div className='divide-y'>
              {items.map((item) => (
                <button
                  key={item.id}
                  type='button'
                  disabled={Boolean(item.read_at)}
                  className={cn(
                    'flex w-full gap-3 px-4 py-3 text-start hover:bg-muted/70 disabled:cursor-default',
                    !item.read_at && 'bg-muted/45'
                  )}
                  onClick={() => markRead.mutate(item)}
                >
                  <span className='min-w-0 flex-1'>
                    <span className='block truncate text-sm font-medium'>
                      {item.title}
                    </span>
                    {item.body && (
                      <span className='block truncate text-xs text-muted-foreground'>
                        {item.body}
                      </span>
                    )}
                  </span>
                  <time className='shrink-0 text-xs text-muted-foreground'>
                    {new Intl.DateTimeFormat(i18n.language, {
                      hour: 'numeric',
                      minute: '2-digit',
                    }).format(new Date(item.created_at))}
                  </time>
                </button>
              ))}
            </div>
          ) : (
            <div className='flex h-24 items-center justify-center text-muted-foreground'>
              <BellOff />
              <span className='sr-only'>{t('opsmesh.notifications')}</span>
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
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className='flex items-center gap-3 px-2 py-3'>
          <Skeleton className='size-9 rounded-full' />
          <div className='min-w-0 flex-1 space-y-2'>
            <Skeleton className='h-3 w-3/5' />
            <Skeleton className='h-3 w-4/5' />
          </div>
        </div>
      ))}
    </div>
  )
}
