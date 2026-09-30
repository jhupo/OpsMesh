import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'

export function WorkspaceStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  return (
    <Badge
      variant='outline'
      className={
        status === 'active'
          ? 'border-foreground bg-foreground text-background'
          : status === 'archived'
            ? 'text-muted-foreground'
            : undefined
      }
    >
      {t(`platformAdmin.workspaces.status.${status}`, { defaultValue: status })}
    </Badge>
  )
}
