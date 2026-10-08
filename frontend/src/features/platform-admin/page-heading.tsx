import { Separator } from '@/components/ui/separator'

export function PlatformPageHeading({
  title,
  actions,
  separator = true,
}: {
  title: string
  actions?: React.ReactNode
  separator?: boolean
}) {
  return (
    <>
      <div className='flex flex-wrap items-start justify-between gap-4'>
        <div className='flex min-w-0 flex-col gap-1'>
          <h1 className='text-2xl font-bold tracking-tight md:text-3xl'>
            {title}
          </h1>
        </div>
        {actions}
      </div>
      {separator && <Separator className='my-4 lg:my-6' />}
    </>
  )
}

export function PlatformSectionHeading({ title }: { title: string }) {
  return (
    <div className='mb-5'>
      <div className='flex flex-col gap-0.5'>
        <h2 className='text-lg font-semibold tracking-tight'>{title}</h2>
      </div>
      <Separator className='mt-4' />
    </div>
  )
}
