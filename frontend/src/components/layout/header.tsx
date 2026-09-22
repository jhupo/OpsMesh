import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { OpsMeshHeaderActions } from '@/components/opsmesh-header-actions'
import { ProjectSwitcher } from '@/features/projects/project-switcher'

type HeaderProps = {
  platformAdmin: boolean
}

export function Header({ platformAdmin }: HeaderProps) {
  return (
    <header className='z-40 h-16 shrink-0 border-b bg-background'>
      <div className='flex h-full items-center gap-3 px-4 sm:gap-4'>
        <SidebarTrigger variant='outline' className='max-md:scale-125' />
        {!platformAdmin && (
          <>
            <Separator orientation='vertical' className='h-6' />
            <ProjectSwitcher />
          </>
        )}
        <OpsMeshHeaderActions platformAdmin={platformAdmin} />
      </div>
    </header>
  )
}
