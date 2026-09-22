import * as React from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  createWorkspace,
  workspacesQueryOptions,
  type PageResponse,
  type Workspace,
} from '@/api/workspaces'
import { useWorkspace } from '@/context/workspace-provider'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
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
import { Spinner } from '@/components/ui/spinner'

type NewTeamDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function NewTeamDialog({ open, onOpenChange }: NewTeamDialogProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { selectWorkspace } = useWorkspace()
  const idempotencyKey = React.useRef(crypto.randomUUID())
  const schema = React.useMemo(
    () =>
      z.object({
        name: z.string().trim().min(1, t('teams.nameRequired')).max(160),
        slug: z
          .string()
          .trim()
          .min(1, t('teams.slugRequired'))
          .max(80)
          .regex(/^[a-z0-9][a-z0-9-]*$/, t('teams.slugInvalid')),
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
      createWorkspace(values, idempotencyKey.current),
    onSuccess: (workspace) => {
      queryClient.setQueryData<PageResponse<Workspace>>(
        workspacesQueryOptions().queryKey,
        (current) =>
          current
            ? {
                ...current,
                items: [...current.items, workspace],
                total: current.total + 1,
              }
            : { items: [workspace], total: 1, limit: 50, offset: 0 }
      )
      selectWorkspace(workspace.id)
      form.reset()
      idempotencyKey.current = crypto.randomUUID()
      onOpenChange(false)
    },
  })
  const nameField = form.register('name')

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      form.reset()
      mutation.reset()
      idempotencyKey.current = crypto.randomUUID()
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className='overscroll-contain'>
        <DialogHeader>
          <DialogTitle>{t('teams.new')}</DialogTitle>
          <DialogDescription className='sr-only'>
            {t('teams.new')}
          </DialogDescription>
        </DialogHeader>
        <form
          className='space-y-6'
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          onChange={() => {
            if (!mutation.isError) return
            mutation.reset()
            idempotencyKey.current = crypto.randomUUID()
          }}
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
              <FieldLabel htmlFor='team-name'>{t('teams.name')}</FieldLabel>
              <Input
                id='team-name'
                autoComplete='organization'
                disabled={mutation.isPending}
                aria-invalid={Boolean(form.formState.errors.name)}
                {...nameField}
              />
              <FieldError errors={[form.formState.errors.name]} />
            </Field>
            <Field data-invalid={Boolean(form.formState.errors.slug)}>
              <FieldLabel htmlFor='team-slug'>{t('common.slug')}</FieldLabel>
              <Input
                id='team-slug'
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
              disabled={mutation.isPending}
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
