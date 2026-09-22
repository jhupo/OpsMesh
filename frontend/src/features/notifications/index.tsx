import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  BellOff,
  CalendarDays,
  Command,
  Info,
  LayoutPanelTop,
  Settings2,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  notificationCountsQueryOptions,
  notificationsQueryOptions,
  markNotificationRead,
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

const iconByType: Record<string, LucideIcon> = {
  event: CalendarDays,
  calendar: CalendarDays,
  setting: Settings2,
  settings: Settings2,
  launch: LayoutPanelTop,
  command: Command,
}

const iconToneBySeverity: Record<string, string> = {
  critical: 'bg-destructive/10 text-destructive',
  error: 'bg-destructive/10 text-destructive',
  warning: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  success: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  info: 'bg-sky-500/10 text-sky-600 dark:text-sky-400',
}

function formatNotificationTime(value: string, language: string) {
  return new Intl.DateTimeFormat(language, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}

function NotificationRow({
  item,
  workspaceId,
  onRead,
}: {
  item: NotificationItem
  workspaceId: string | undefined
  onRead: (item: NotificationItem) => void
}) {
  const { i18n } = useTranslation()
  const Icon = iconByType[item.notification_type.toLowerCase()] ?? Info
  const tone =
    iconToneBySeverity[item.severity.toLowerCase()] ??
    'bg-muted text-muted-foreground'

  return (
    <button
      type='button'
      className={cn(
        'flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/70 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-inset disabled:cursor-default disabled:hover:bg-transparent',
        !item.read_at && 'bg-muted/45'
      )}
      onClick={() => {
        if (workspaceId && !item.read_at) onRead(item)
      }}
      disabled={Boolean(item.read_at)}
      aria-label={item.title}
    >
      <span
        className={cn(
          'mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full',
          tone
        )}
      >
        <Icon className='size-4' aria-hidden='true' />
      </span>
      <span className='min-w-0 flex-1'>
        <span className='flex items-start justify-between gap-3'>
          <span className='truncate text-sm font-medium'>{item.title}</span>
          <time
            dateTime={item.created_at}
            className='shrink-0 text-xs text-muted-foreground'
          >
            {formatNotificationTime(item.created_at, i18n.language)}
          </time>
        </span>
        {item.body && (
          <span className='mt-0.5 block truncate text-xs text-muted-foreground'>
            {item.body}
          </span>
        )}
      </span>
    </button>
  )
}

function NotificationList({
  workspaceId,
  items,
  pending,
  onRead,
}: {
  workspaceId: string | undefined
  items: NotificationItem[]
  pending: boolean
  onRead: (item: NotificationItem) => void
}) {
  const { t } = useTranslation()

  if (pending) {
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

  if (items.length === 0) {
    return (
      <div className='flex h-24 items-center justify-center text-muted-foreground'>
        <span className='sr-only'>{t('notifications.empty')}</span>
        <BellOff className='size-5' aria-hidden='true' />
      </div>
    )
  }

  return (
    <div className='divide-y divide-border/70'>
      {items.map((item) => (
        <NotificationRow
          key={item.id}
          item={item}
          workspaceId={workspaceId}
          onRead={onRead}
        />
      ))}
    </div>
  )
}

function useWorkspaceNotifications() {
  const queryClient = useQueryClient()
  const { activeWorkspace } = useWorkspace()
  const workspaceId = activeWorkspace?.id
  const { data: notifications, isPending } = useQuery(
    notificationsQueryOptions(workspaceId)
  )
  const { data: counts } = useQuery(notificationCountsQueryOptions(workspaceId))
  const markReadMutation = useMutation({
    mutationFn: (item: NotificationItem) =>
      markNotificationRead(workspaceId as string, item.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['notifications', workspaceId],
      })
    },
  })
  const unreadCount = counts?.unread_count ?? 0
  const loading = Boolean(workspaceId) && isPending
  const itemCount = notifications?.items.length ?? 0

  return {
    workspaceId,
    items: notifications?.items ?? [],
    unreadCount,
    loading,
    itemCount,
    markRead: (item: NotificationItem) => markReadMutation.mutate(item),
  }
}

export function NotificationFeed({ className }: { className?: string }) {
  const { workspaceId, items, loading, markRead } = useWorkspaceNotifications()

  return (
    <ScrollArea className={cn('h-[min(32rem,60vh)]', className)}>
      <NotificationList
        workspaceId={workspaceId}
        items={items}
        pending={loading}
        onRead={markRead}
      />
    </ScrollArea>
  )
}

export function NotificationCenter() {
  const { t } = useTranslation()
  const { workspaceId, items, unreadCount, loading, itemCount, markRead } =
    useWorkspaceNotifications()
  const hasContent = loading || itemCount > 0
  const listHeight = loading ? 288 : Math.min(Math.max(itemCount * 64, 96), 352)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='relative rounded-full'
          aria-label={t('common.notifications')}
        >
          <Bell aria-hidden='true' />
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
          <h2 className='text-base font-semibold'>
            {t('common.notifications')}
          </h2>
          {unreadCount > 0 && (
            <span className='rounded-full bg-foreground px-2.5 py-1 text-xs font-medium text-background'>
              {unreadCount} {t('notifications.new')}
            </span>
          )}
        </div>
        <ScrollArea style={{ height: listHeight }}>
          <NotificationList
            workspaceId={workspaceId}
            items={items}
            pending={loading}
            onRead={markRead}
          />
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
