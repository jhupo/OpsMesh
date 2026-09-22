import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

type ContentSectionProps = {
  title: string
  children: React.ReactNode
}

export function ContentSection({ title, children }: ContentSectionProps) {
  return (
    <Card className='min-w-0 gap-6 shadow-none'>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}
