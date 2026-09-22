import { cn } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

type ContentSectionProps = {
  title: string
  children: React.ReactNode
  className?: string
}

export function ContentSection({
  title,
  children,
  className,
}: ContentSectionProps) {
  return (
    <Card className={cn('min-w-0 gap-6 shadow-none', className)}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}
