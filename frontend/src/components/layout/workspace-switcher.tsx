import { Check, ChevronsUpDown, Building2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useWorkspace } from '@/context/workspace-provider'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
} from '@/components/ui/sidebar'

export function WorkspaceSwitcher() {
  const { t } = useTranslation()
  const { workspaces, activeWorkspace, selectWorkspace } = useWorkspace()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              tooltip={t('navigation.selectWorkspace')}
              aria-label={t('navigation.selectWorkspace')}
            >
              <Building2 />
              <span>
                {activeWorkspace?.name ?? t('navigation.selectWorkspace')}
              </span>
              <ChevronsUpDown className='ms-auto' />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align='start'
            className='max-h-80 w-64 overflow-y-auto'
          >
            <DropdownMenuGroup>
              {workspaces.map((workspace) => (
                <DropdownMenuItem
                  key={workspace.id}
                  onSelect={() => selectWorkspace(workspace.id)}
                >
                  <span className='truncate'>{workspace.name}</span>
                  {workspace.id === activeWorkspace?.id && (
                    <Check className='ms-auto' />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
