import * as React from 'react'
import { Check, ChevronsUpDown, FolderKanban } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useProject } from '@/context/project-provider'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

export function ProjectSwitcher() {
  const { t } = useTranslation()
  const { projects, activeProject, selectProject } = useProject()
  const [open, setOpen] = React.useState(false)
  const label = activeProject?.name ?? t('opsmesh.projects.all')

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant='ghost'
          role='combobox'
          aria-expanded={open}
          aria-label={label}
          className='size-9 justify-between px-0 sm:h-9 sm:w-auto sm:max-w-44 sm:px-3'
        >
          <FolderKanban className='sm:hidden' />
          <span className='hidden truncate sm:inline'>{label}</span>
          <ChevronsUpDown className='hidden opacity-60 sm:block' />
        </Button>
      </PopoverTrigger>
      <PopoverContent align='start' sideOffset={8} className='w-80 p-0'>
        <Command>
          <CommandInput placeholder={t('opsmesh.projects.find')} />
          <CommandList>
            <CommandEmpty>{t('opsmesh.projects.empty')}</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value={t('opsmesh.projects.all')}
                onSelect={() => {
                  selectProject(null)
                  setOpen(false)
                }}
              >
                <FolderKanban />
                <span className='min-w-0 flex-1 truncate'>
                  {t('opsmesh.projects.all')}
                </span>
                <Check className={cn(activeProject && 'opacity-0')} />
              </CommandItem>
              {projects.map((project) => (
                <CommandItem
                  key={project.id}
                  value={`${project.name} ${project.slug}`}
                  onSelect={() => {
                    selectProject(project.id)
                    setOpen(false)
                  }}
                >
                  <FolderKanban />
                  <span className='min-w-0 flex-1 truncate'>
                    {project.name}
                  </span>
                  <Check
                    className={cn(
                      activeProject?.id !== project.id && 'opacity-0'
                    )}
                  />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
