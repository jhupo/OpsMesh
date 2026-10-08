import { useState } from 'react'
import { useBlocker } from '@tanstack/react-router'
import { FileText, Settings2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import TabsVertical from '@/components/shadcn-space/tabs/tabs-06'
import { capabilityDraftSchema, type CapabilityDraft } from './drafts'
import { ParameterFields } from './parameter-fields'
import { ResourcePicker } from './resource-picker'

export function ResourceForm({
  initial,
  onClose,
  onSave,
}: {
  initial: CapabilityDraft
  onClose: () => void
  onSave: (draft: CapabilityDraft) => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`capabilityCenter.${key}`)
  const [draft, setDraft] = useState(initial)
  const [tab, setTab] = useState('basic')
  const [closing, setClosing] = useState(false)
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)
  const blocker = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: dirty,
    withResolver: true,
  })
  const config = draft.configuration
  const setConfig = (patch: Partial<CapabilityDraft['configuration']>) =>
    setDraft({ ...draft, configuration: { ...config, ...patch } })
  const choice = (
    label: string,
    value: string,
    values: string[],
    onChange: (value: string) => void
  ) => (
    <Field>
      <FieldLabel>{tr(label)}</FieldLabel>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className='w-full' aria-label={tr(label)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {values.map((item) => (
              <SelectItem key={item} value={item}>
                {tr(item)}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>
  )
  const submit = () => {
    if (!draft.name.trim()) {
      setTab('basic')
      toast.error(tr('missingName'))
      return
    }
    if (config.endpoint) {
      try {
        const url = new URL(config.endpoint)
        if (
          !['http:', 'https:'].includes(url.protocol) ||
          url.username ||
          url.password ||
          url.search ||
          url.hash
        )
          throw new Error()
      } catch {
        setTab('configuration')
        toast.error(tr('invalidEndpoint'))
        return
      }
    }
    if (
      [config.inputs, config.outputs].some(
        (fields) =>
          fields.some(
            (field) => !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(field.name)
          ) || new Set(fields.map((field) => field.name)).size !== fields.length
      )
    ) {
      setTab('configuration')
      toast.error(tr('invalidParameters'))
      return
    }
    const parsed = capabilityDraftSchema.safeParse({
      ...draft,
      updatedAt: new Date().toISOString(),
    })
    if (!parsed.success) {
      toast.error(tr('invalidDraft'))
      return
    }
    onSave(parsed.data)
  }
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => {
          if (!open) {
            if (dirty) setClosing(true)
            else onClose()
          }
        }}
      >
        <DialogContent
          className='flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl'
          aria-describedby={undefined}
        >
          <DialogHeader className='px-6 pt-6 pb-3'>
            <DialogTitle>{tr(draft.kind)}</DialogTitle>
          </DialogHeader>
          <form
            id='capability-config'
            className='min-h-0 overflow-y-auto px-6 pb-6'
            onSubmit={(event) => {
              event.preventDefault()
              submit()
            }}
          >
            <TabsVertical
              items={[
                { id: 'basic', label: tr('basic'), icon: FileText },
                {
                  id: 'configuration',
                  label: tr('configuration'),
                  icon: Settings2,
                },
              ]}
              value={tab}
              onValueChange={setTab}
            >
              {tab === 'basic' ? (
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor='cap-name'>{tr('name')}</FieldLabel>
                    <Input
                      id='cap-name'
                      value={draft.name}
                      maxLength={160}
                      onChange={(event) =>
                        setDraft({ ...draft, name: event.target.value })
                      }
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor='cap-description'>
                      {tr('description')}
                    </FieldLabel>
                    <Textarea
                      id='cap-description'
                      rows={3}
                      value={draft.description}
                      maxLength={2000}
                      onChange={(event) =>
                        setDraft({ ...draft, description: event.target.value })
                      }
                    />
                  </Field>
                  <FieldGroup className='grid gap-4 sm:grid-cols-2'>
                    <Field>
                      <FieldLabel>{tr('workspace')}</FieldLabel>
                      <ResourcePicker
                        label={tr('workspace')}
                        source='workspaces'
                        value={draft.workspace}
                        onChange={(workspace) =>
                          setDraft({
                            ...draft,
                            workspace,
                            configuration: {
                              ...config,
                              credential: null,
                              reference: null,
                              runtime: null,
                              dependencies: [],
                            },
                            graph: {
                              ...draft.graph,
                              nodes: draft.graph.nodes.map((node) => ({
                                ...node,
                                data: { ...node.data, reference: '' },
                              })),
                            },
                          })
                        }
                      />
                    </Field>
                    <Field>
                      <FieldLabel>{tr('owner')}</FieldLabel>
                      <ResourcePicker
                        label={tr('owner')}
                        source='users'
                        value={draft.owner}
                        onChange={(owner) => setDraft({ ...draft, owner })}
                      />
                    </Field>
                    {choice(
                      'scope',
                      draft.scope,
                      ['private', 'team', 'workspace', 'public'],
                      (scope) =>
                        setDraft({
                          ...draft,
                          scope: scope as CapabilityDraft['scope'],
                        })
                    )}
                  </FieldGroup>
                </FieldGroup>
              ) : (
                <FieldGroup>
                  {draft.kind === 'mcp' &&
                    choice(
                      'transport',
                      config.transport,
                      ['streamable_http', 'sse', 'stdio'],
                      (transport) =>
                        setConfig({
                          transport: transport as typeof config.transport,
                          endpoint: '',
                          runtime: null,
                        })
                    )}
                  {draft.kind === 'tool' &&
                    choice(
                      'source',
                      config.source,
                      ['http', 'mcp', 'plugin'],
                      (source) =>
                        setConfig({
                          source: source as typeof config.source,
                          reference: null,
                          endpoint: '',
                        })
                    )}
                  {((draft.kind === 'mcp' && config.transport !== 'stdio') ||
                    (draft.kind === 'tool' && config.source === 'http')) && (
                    <>
                      {draft.kind === 'tool' &&
                        choice(
                          'method',
                          config.method,
                          ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                          (method) =>
                            setConfig({
                              method: method as typeof config.method,
                            })
                        )}
                      <Field>
                        <FieldLabel htmlFor='cap-endpoint'>
                          {tr('endpoint')}
                        </FieldLabel>
                        <Input
                          id='cap-endpoint'
                          type='url'
                          value={config.endpoint}
                          maxLength={2000}
                          onChange={(event) =>
                            setConfig({ endpoint: event.target.value })
                          }
                        />
                      </Field>
                      {choice(
                        'authentication',
                        config.authentication,
                        ['none', 'bearer', 'api_key', 'oauth2'],
                        (authentication) =>
                          setConfig({
                            authentication:
                              authentication as typeof config.authentication,
                            credential: null,
                          })
                      )}
                      {config.authentication !== 'none' && (
                        <Field>
                          <FieldLabel>{tr('credential')}</FieldLabel>
                          <ResourcePicker
                            label={tr('credential')}
                            source='credentials'
                            workspaceId={draft.workspace?.id}
                            value={config.credential}
                            onChange={(credential) => setConfig({ credential })}
                          />
                        </Field>
                      )}
                    </>
                  )}
                  {draft.kind === 'mcp' && config.transport === 'stdio' && (
                    <Field>
                      <FieldLabel>{tr('runtimeReference')}</FieldLabel>
                      <ResourcePicker
                        label={tr('runtimeReference')}
                        source='runtime'
                        workspaceId={draft.workspace?.id}
                        value={config.runtime}
                        onChange={(runtime) => setConfig({ runtime })}
                      />
                    </Field>
                  )}
                  {draft.kind === 'tool' && config.source !== 'http' && (
                    <Field>
                      <FieldLabel>{tr('resourceReference')}</FieldLabel>
                      <ResourcePicker
                        label={tr('resourceReference')}
                        source='tools'
                        workspaceId={draft.workspace?.id}
                        value={config.reference}
                        onChange={(reference) => setConfig({ reference })}
                      />
                    </Field>
                  )}
                  {draft.kind === 'skill' && (
                    <Field>
                      <FieldLabel htmlFor='cap-instructions'>
                        {tr('skillContent')}
                      </FieldLabel>
                      <Textarea
                        id='cap-instructions'
                        className='min-h-48'
                        value={config.instructions}
                        maxLength={8000}
                        onChange={(event) =>
                          setConfig({ instructions: event.target.value })
                        }
                      />
                    </Field>
                  )}
                  {draft.kind !== 'mcp' && (
                    <>
                      <ParameterFields
                        label={tr('inputs')}
                        value={config.inputs}
                        onChange={(inputs) => setConfig({ inputs })}
                      />
                      <ParameterFields
                        label={tr('outputs')}
                        value={config.outputs}
                        onChange={(outputs) => setConfig({ outputs })}
                      />
                    </>
                  )}
                  <FieldGroup className='grid gap-4 sm:grid-cols-2'>
                    <Field>
                      <FieldLabel htmlFor='cap-timeout'>
                        {tr('timeout')}
                      </FieldLabel>
                      <Input
                        id='cap-timeout'
                        type='number'
                        min={1}
                        max={300}
                        value={config.timeout}
                        onChange={(event) =>
                          setConfig({ timeout: Number(event.target.value) })
                        }
                      />
                    </Field>
                    {choice(
                      'risk',
                      config.risk,
                      ['low', 'medium', 'high'],
                      (risk) => setConfig({ risk: risk as typeof config.risk })
                    )}
                  </FieldGroup>
                  <Field orientation='horizontal'>
                    <FieldLabel htmlFor='cap-approval'>
                      {tr('requiresApproval')}
                    </FieldLabel>
                    <Switch
                      id='cap-approval'
                      checked={config.requiresApproval}
                      onCheckedChange={(requiresApproval) =>
                        setConfig({ requiresApproval })
                      }
                    />
                  </Field>
                </FieldGroup>
              )}
            </TabsVertical>
          </form>
          <DialogFooter className='border-t px-6 py-4'>
            <Button
              variant='outline'
              onClick={() => (dirty ? setClosing(true) : onClose())}
            >
              {tr('cancel')}
            </Button>
            <Button type='submit' form='capability-config'>
              {tr('saveDraft')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={closing || blocker.status === 'blocked'}
        onOpenChange={(open) => {
          setClosing(open)
          if (!open) blocker.reset?.()
        }}
      >
        <AlertDialogContent aria-describedby={undefined}>
          <AlertDialogHeader>
            <AlertDialogTitle>{tr('discard')}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tr('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                blocker.status === 'blocked' ? blocker.proceed() : onClose()
              }
            >
              {tr('leave')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
