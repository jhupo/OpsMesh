import * as React from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  ChevronsUpDown,
  CircleAlert,
  Cloud,
  FolderKanban,
  Plus,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  createProject,
  projectsQueryOptions,
  type Project,
} from '@/api/projects'
import { type PageResponse } from '@/api/workspaces'
import { cn } from '@/lib/utils'
import { useProject } from '@/context/project-provider'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Spinner } from '@/components/ui/spinner'

type CreateProjectDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function CreateProjectDialog({ open, onOpenChange }: CreateProjectDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { activeWorkspace } = useWorkspace()
  const { selectProject } = useProject()
  const schema = React.useMemo(
    () =>
      z.object({
        name: z.string().trim().min(1, t('projects.nameRequired')).max(160),
        slug: z
          .string()
          .trim()
          .min(1, t('projects.slugRequired'))
          .max(80)
          .regex(/^[a-z0-9][a-z0-9-]*$/, t('projects.slugInvalid')),
      }),
    [t]
  )
  type FormValues = z.infer<typeof schema>
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', slug: '' },
  })
  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      createProject(activeWorkspace?.id as string, values),
    onSuccess: (project) => {
      const queryKey = projectsQueryOptions(activeWorkspace?.id).queryKey
      queryClient.setQueryData<PageResponse<Project>>(queryKey, (current) =>
        current
          ? {
              ...current,
              items: [...current.items, project],
              total: current.total + 1,
            }
          : { items: [project], total: 1, limit: 100, offset: 0 }
      )
      selectProject(project.id)
      form.reset()
      onOpenChange(false)
    },
  })

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      form.reset()
      mutation.reset()
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className='overscroll-contain'>
        <DialogHeader>
          <DialogTitle>{t('projects.create')}</DialogTitle>
          <DialogDescription className='sr-only'>
            {t('projects.create')}
          </DialogDescription>
        </DialogHeader>
        <form
          className='space-y-6'
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          noValidate
        >
          {mutation.error instanceof Error && (
            <Alert variant='destructive'>
              <CircleAlert />
              <AlertDescription>{mutation.error.message}</AlertDescription>
            </Alert>
          )}
          <FieldGroup>
            <Field data-invalid={Boolean(form.formState.errors.name)}>
              <FieldLabel htmlFor='project-name'>
                {t('projects.name')}
              </FieldLabel>
              <Input
                id='project-name'
                autoComplete='off'
                disabled={mutation.isPending}
                aria-invalid={Boolean(form.formState.errors.name)}
                {...form.register('name')}
              />
              <FieldError errors={[form.formState.errors.name]} />
            </Field>
            <Field data-invalid={Boolean(form.formState.errors.slug)}>
              <FieldLabel htmlFor='project-slug'>{t('common.slug')}</FieldLabel>
              <Input
                id='project-slug'
                autoCapitalize='none'
                autoComplete='off'
                spellCheck={false}
                disabled={mutation.isPending}
                aria-invalid={Boolean(form.formState.errors.slug)}
                {...form.register('slug')}
              />
              <FieldError errors={[form.formState.errors.slug]} />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button
              type='button'
              variant='outline'
              onClick={() => handleOpenChange(false)}
              disabled={mutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type='submit'
              disabled={!activeWorkspace || mutation.isPending}
              aria-busy={mutation.isPending}
            >
              {mutation.isPending && <Spinner data-icon='inline-start' />}
              {t('common.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function ProjectSwitcher() {
  const { t } = useTranslation()
  const { activeWorkspace } = useWorkspace()
  const { projects, activeProject, isPending, selectProject } = useProject()
  const [open, setOpen] = React.useState(false)
  const [createOpen, setCreateOpen] = React.useState(false)
  const label = activeProject?.name ?? t('projects.all')

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant='ghost'
            role='combobox'
            aria-expanded={open}
            aria-label={label}
            className='size-9 justify-between px-0 sm:h-9 sm:w-auto sm:max-w-44 sm:px-3'
          >
            <FolderKanban className='sm:hidden' aria-hidden='true' />
            <span className='hidden truncate sm:inline'>{label}</span>
            <ChevronsUpDown
              className='hidden size-3.5 opacity-60 sm:block'
              aria-hidden='true'
            />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align='start'
          sideOffset={8}
          className='w-[min(24rem,calc(100vw-1rem))] overflow-hidden overscroll-contain p-0'
        >
          <Command>
            <div className='relative'>
              <CommandInput
                placeholder={t('projects.find')}
                className='pe-12'
              />
              <kbd className='pointer-events-none absolute end-3 top-1/2 -translate-y-1/2 rounded border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground'>
                Esc
              </kbd>
            </div>
            <CommandList className='min-h-48'>
              {!isPending && (
                <CommandEmpty>
                  <div className='flex min-h-40 flex-col items-center justify-center gap-4 text-muted-foreground'>
                    <span className='flex size-9 items-center justify-center rounded-lg border bg-background'>
                      <Cloud className='size-4' aria-hidden='true' />
                    </span>
                    <span>{t('projects.empty')}</span>
                  </div>
                </CommandEmpty>
              )}
              {projects.length > 0 && (
                <CommandGroup>
                  <CommandItem
                    value={t('projects.all')}
                    onSelect={() => {
                      selectProject(null)
                      setOpen(false)
                    }}
                  >
                    <FolderKanban aria-hidden='true' />
                    <span className='min-w-0 flex-1 truncate'>
                      {t('projects.all')}
                    </span>
                    <Check
                      className={cn(
                        'size-4',
                        activeProject ? 'opacity-0' : 'opacity-100'
                      )}
                      aria-hidden='true'
                    />
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
                      <FolderKanban aria-hidden='true' />
                      <span className='min-w-0 flex-1 truncate'>
                        {project.name}
                      </span>
                      <Check
                        className={cn(
                          'size-4',
                          activeProject?.id === project.id
                            ? 'opacity-100'
                            : 'opacity-0'
                        )}
                        aria-hidden='true'
                      />
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              {isPending && activeWorkspace && (
                <div className='flex min-h-48 items-center justify-center'>
                  <Spinner />
                </div>
              )}
            </CommandList>
            <div className='border-t p-2'>
              <Button
                variant='ghost'
                className='w-full justify-start bg-muted/70'
                disabled={!activeWorkspace}
                onClick={() => {
                  setOpen(false)
                  setCreateOpen(true)
                }}
              >
                <Plus data-icon='inline-start' />
                {t('projects.create')}
              </Button>
            </div>
          </Command>
        </PopoverContent>
      </Popover>
      <CreateProjectDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  )
}
