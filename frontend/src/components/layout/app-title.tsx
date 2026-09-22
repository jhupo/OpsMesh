import { Link } from '@tanstack/react-router'
import { Command } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'

export function AppTitle() {
  const { setOpenMobile } = useSidebar()
  const { t } = useTranslation()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size='lg'
          className='hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
          asChild
        >
          <Link
            to='/admin/$section'
            params={{ section: 'overview' }}
            onClick={() => setOpenMobile(false)}
            className='flex min-w-0 flex-1 items-center gap-2'
          >
            <span className='flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground'>
              <Command className='size-4' />
            </span>
            <span className='grid min-w-0 flex-1 text-start text-sm leading-tight group-data-[collapsible=icon]:hidden'>
              <span className='truncate font-semibold' translate='no'>
                OpsMesh
              </span>
              <span className='truncate text-xs'>
                {t('platformAdmin.role')}
              </span>
            </span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
