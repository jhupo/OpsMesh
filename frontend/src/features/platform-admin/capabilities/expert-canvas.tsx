import { useCallback, useState } from 'react'
import { useBlocker } from '@tanstack/react-router'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type NodeProps,
  type Node,
  type Connection,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Bot,
  BookOpen,
  BrainCircuit,
  Check,
  ChevronLeft,
  Download,
  GitBranch,
  GitMerge,
  Layers,
  Play,
  Plus,
  Save,
  ShieldCheck,
  Square,
  Trash2,
  Wrench,
  PlugZap,
  Sparkles,
  Settings2,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { type OptionSource } from '@/api/capability-options'
import { cn } from '@/lib/utils'
import { useTheme } from '@/context/theme-provider'
import { useIsMobile } from '@/hooks/use-mobile'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import {
  bindingKinds,
  nodeKinds,
  createNode,
  canConnect,
  exportDraft,
  validateGraph,
  capabilityDraftSchema,
  type CapabilityDraft,
  type ExpertNode,
  type ExpertEdge,
  type NodeKind,
} from './drafts'
import { ParameterFields } from './parameter-fields'
import { ResourceForm } from './resource-form'
import { ResourcePicker } from './resource-picker'

const icons = {
  start: Play,
  agent: Bot,
  expert: Layers,
  condition: GitBranch,
  join: GitMerge,
  approval: ShieldCheck,
  end: Square,
  tool: Wrench,
  skill: Sparkles,
  mcp: PlugZap,
  knowledge: BookOpen,
  memory: BrainCircuit,
}

function CapabilityNode({
  data,
  selected,
}: NodeProps<Node<ExpertNode['data']>>) {
  const { t } = useTranslation()
  const Icon = icons[data.kind]
  const binding = bindingKinds.includes(data.kind)
  return (
    <Card
      className={cn(
        'w-52 gap-3 rounded-lg py-4 shadow-none',
        selected && 'ring-2 ring-ring'
      )}
    >
      {!binding && data.kind !== 'start' && (
        <Handle
          type='target'
          position={Position.Left}
          id='in'
          aria-label={t('capabilityCenter.flowIn')}
        />
      )}
      <CardHeader className='gap-2 px-4'>
        <div className='flex items-center gap-2 text-muted-foreground'>
          <Icon className='size-4' aria-hidden />
          <span className='text-xs'>
            {t(`capabilityCenter.nodes.${data.kind}`)}
          </span>
        </div>
        <CardTitle className='truncate text-sm'>
          {data.label || t(`capabilityCenter.nodes.${data.kind}`)}
        </CardTitle>
      </CardHeader>
      {(data.model || data.reference) && (
        <CardContent className='truncate px-4 text-xs text-muted-foreground'>
          {data.model || data.referenceName}
        </CardContent>
      )}
      {data.kind === 'agent' && (
        <Handle
          type='target'
          position={Position.Bottom}
          id='binding-in'
          className='!size-2.5 !rounded-none'
          aria-label={t('capabilityCenter.binding')}
        />
      )}
      {binding ? (
        <Handle
          type='source'
          position={Position.Top}
          id='binding-out'
          className='!size-2.5 !rounded-none'
          aria-label={t('capabilityCenter.binding')}
        />
      ) : data.kind === 'condition' ? (
        <>
          <Handle
            type='source'
            position={Position.Right}
            id='yes'
            style={{ top: '35%' }}
            aria-label={t('capabilityCenter.yes')}
          />
          <Handle
            type='source'
            position={Position.Right}
            id='no'
            style={{ top: '75%' }}
            aria-label={t('capabilityCenter.no')}
          />
        </>
      ) : (
        data.kind !== 'end' && (
          <Handle
            type='source'
            position={Position.Right}
            id='out'
            aria-label={t('capabilityCenter.flowOut')}
          />
        )
      )}
    </Card>
  )
}
const nodeTypes = { capability: CapabilityNode }
const snapshot = (draft: CapabilityDraft) =>
  JSON.stringify({
    ...draft,
    graph: capabilityDraftSchema.shape.graph.parse(draft.graph),
  })

export function ExpertCanvas(props: {
  initial: CapabilityDraft
  onSave: (draft: CapabilityDraft) => boolean
  onClose: () => void
}) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  )
}

function Canvas({
  initial,
  onSave,
  onClose,
}: {
  initial: CapabilityDraft
  onSave: (draft: CapabilityDraft) => boolean
  onClose: () => void
}) {
  const { t } = useTranslation()
  const tr = (key: string) => t(`capabilityCenter.${key}`)
  const { resolvedTheme } = useTheme()
  const mobile = useIsMobile()
  const flow = useReactFlow<ExpertNode>()
  const [draft, setDraft] = useState(initial)
  const [saved, setSaved] = useState(snapshot(initial))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [palette, setPalette] = useState(false)
  const [closing, setClosing] = useState(false)
  const [metadataOpen, setMetadataOpen] = useState(false)
  const [outputPort, setOutputPort] = useState('yes')
  const [issues, setIssues] = useState<string[] | null>(null)
  const selected = draft.graph.nodes.find((node) => node.id === selectedId)
  const dirty = snapshot(draft) !== saved
  const blocker = useBlocker({
    shouldBlockFn: () => dirty,
    enableBeforeUnload: dirty,
    withResolver: true,
  })
  const changeGraph = (graph: CapabilityDraft['graph']) => {
    setDraft((current) => ({ ...current, graph }))
    setIssues(null)
  }
  const validConnection = useCallback(
    (connection: Connection | ExpertEdge) =>
      canConnect(
        draft.graph.nodes,
        draft.graph.edges,
        connection.source,
        connection.target,
        connection.sourceHandle ?? null,
        connection.targetHandle ?? null
      ),
    [draft.graph]
  )
  const add = (kind: NodeKind, position?: { x: number; y: number }) => {
    if (draft.graph.nodes.length >= 128) {
      toast.error(tr('nodeLimit'))
      return
    }
    const node = createNode(
      kind,
      tr(`nodes.${kind}`),
      position ??
        flow.screenToFlowPosition({
          x: window.innerWidth / 2,
          y: window.innerHeight / 2,
        })
    )
    changeGraph({ ...draft.graph, nodes: [...draft.graph.nodes, node] })
    setPalette(false)
    setSelectedId(node.id)
  }
  const updateNode = (patch: Partial<ExpertNode['data']>) =>
    changeGraph({
      ...draft.graph,
      nodes: draft.graph.nodes.map((node) =>
        node.id === selectedId
          ? { ...node, data: { ...node.data, ...patch } }
          : node
      ),
    })
  const save = () => {
    if (!draft.name.trim()) {
      toast.error(tr('missingName'))
      return
    }
    const next = {
      ...draft,
      name: draft.name.trim(),
      updatedAt: new Date().toISOString(),
    }
    if (onSave(next)) {
      setDraft(next)
      setSaved(snapshot(next))
    }
  }
  const inspector = selected && (
    <FieldGroup className='p-4'>
      <Field>
        <FieldLabel htmlFor='node-name'>{tr('name')}</FieldLabel>
        <Input
          id='node-name'
          value={selected.data.label}
          maxLength={160}
          onChange={(event) => updateNode({ label: event.target.value })}
        />
      </Field>
      {selected.data.kind === 'start' && (
        <ParameterFields
          label={tr('inputs')}
          value={draft.configuration.inputs}
          onChange={(inputs) =>
            setDraft({
              ...draft,
              configuration: { ...draft.configuration, inputs },
            })
          }
        />
      )}
      {selected.data.kind === 'end' && (
        <ParameterFields
          label={tr('outputs')}
          value={draft.configuration.outputs}
          onChange={(outputs) =>
            setDraft({
              ...draft,
              configuration: { ...draft.configuration, outputs },
            })
          }
        />
      )}
      {selected.data.kind === 'memory' && (
        <Field>
          <FieldLabel htmlFor='memory-instructions'>
            {tr('instructions')}
          </FieldLabel>
          <Textarea
            id='memory-instructions'
            value={selected.data.instructions}
            onChange={(event) =>
              updateNode({ instructions: event.target.value })
            }
          />
        </Field>
      )}
      {selected.data.kind === 'agent' && (
        <>
          <Field>
            <FieldLabel htmlFor='node-model'>{tr('model')}</FieldLabel>
            <Input
              id='node-model'
              value={selected.data.model}
              maxLength={160}
              onChange={(event) => updateNode({ model: event.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='node-instructions'>
              {tr('instructions')}
            </FieldLabel>
            <Textarea
              id='node-instructions'
              rows={8}
              value={selected.data.instructions}
              maxLength={8000}
              onChange={(event) =>
                updateNode({ instructions: event.target.value })
              }
            />
          </Field>
        </>
      )}
      {((selected.data.kind !== 'memory' &&
        bindingKinds.includes(selected.data.kind)) ||
        selected.data.kind === 'expert') && (
        <>
          <Field>
            <FieldLabel htmlFor='node-reference'>
              {tr('resourceReference')}
            </FieldLabel>
            <ResourcePicker
              label={tr('resourceReference')}
              source={
                (
                  {
                    expert: 'experts',
                    tool: 'tools',
                    skill: 'skills',
                    mcp: 'mcp',
                    knowledge: 'knowledge',
                  } as Partial<Record<NodeKind, OptionSource>>
                )[selected.data.kind] ?? 'knowledge'
              }
              workspaceId={draft.workspace?.id}
              value={
                selected.data.reference
                  ? {
                      id: selected.data.reference,
                      name: selected.data.referenceName,
                    }
                  : null
              }
              onChange={(value) =>
                updateNode({
                  reference: value?.id ?? '',
                  referenceName: value?.name ?? '',
                })
              }
            />
          </Field>
          <Field>
            <FieldLabel htmlFor='node-version'>{tr('version')}</FieldLabel>
            <Input
              id='node-version'
              type='number'
              min={1}
              step={1}
              value={selected.data.version}
              onChange={(event) => {
                const version = Number(event.target.value)
                if (Number.isInteger(version) && version >= 1)
                  updateNode({ version })
              }}
            />
          </Field>
        </>
      )}
      {selected.data.kind === 'condition' && (
        <>
          <Field>
            <FieldLabel htmlFor='condition-path'>
              {tr('conditionPath')}
            </FieldLabel>
            <Select
              value={selected.data.conditionPath}
              onValueChange={(conditionPath) => updateNode({ conditionPath })}
            >
              <SelectTrigger aria-label={tr('conditionPath')}>
                <SelectValue placeholder={tr('select')} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {draft.configuration.inputs
                    .filter((input) => input.name)
                    .map((input) => (
                      <SelectItem key={input.id} value={`inputs.${input.name}`}>
                        {tr('inputs')} · {input.name}
                      </SelectItem>
                    ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel>{tr('operator')}</FieldLabel>
            <Select
              value={selected.data.conditionOperator}
              onValueChange={(value) =>
                updateNode({
                  conditionOperator:
                    value as ExpertNode['data']['conditionOperator'],
                })
              }
            >
              <SelectTrigger aria-label={tr('operator')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {['equals', 'not_equals', 'contains', 'exists'].map(
                    (value) => (
                      <SelectItem key={value} value={value}>
                        {tr(value)}
                      </SelectItem>
                    )
                  )}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          {selected.data.conditionOperator !== 'exists' && (
            <Field>
              <FieldLabel htmlFor='condition-value'>{tr('value')}</FieldLabel>
              <Input
                id='condition-value'
                value={selected.data.conditionValue}
                onChange={(event) =>
                  updateNode({ conditionValue: event.target.value })
                }
              />
            </Field>
          )}
        </>
      )}
      {selected.data.kind === 'join' && (
        <Field>
          <FieldLabel>{tr('joinPolicy')}</FieldLabel>
          <Select
            value={selected.data.joinPolicy}
            onValueChange={(value) =>
              updateNode({
                joinPolicy: value as ExpertNode['data']['joinPolicy'],
              })
            }
          >
            <SelectTrigger aria-label={tr('joinPolicy')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {['all_success', 'all_selected'].map((value) => (
                  <SelectItem key={value} value={value}>
                    {tr(value)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
      )}
      <Field>
        <FieldLabel>{tr('connections')}</FieldLabel>
        {draft.graph.edges
          .filter(
            (edge) => edge.source === selected.id || edge.target === selected.id
          )
          .map((edge) => (
            <div key={edge.id} className='flex items-center gap-2 text-sm'>
              <span className='min-w-0 flex-1 truncate'>
                {
                  draft.graph.nodes.find((node) => node.id === edge.source)
                    ?.data.label
                }{' '}
                →{' '}
                {
                  draft.graph.nodes.find((node) => node.id === edge.target)
                    ?.data.label
                }
              </span>
              <Badge variant='outline'>
                {edge.data.kind === 'binding'
                  ? tr('bindingShort')
                  : tr('flowShort')}
              </Badge>
              <Button
                size='icon'
                variant='ghost'
                aria-label={tr('deleteConnection')}
                onClick={() =>
                  changeGraph({
                    ...draft.graph,
                    edges: draft.graph.edges.filter(
                      (item) => item.id !== edge.id
                    ),
                  })
                }
              >
                <Trash2 />
              </Button>
            </div>
          ))}
      </Field>
      {selected.data.kind !== 'end' && (
        <>
          {selected.data.kind === 'condition' && (
            <Field>
              <FieldLabel>{tr('outputPort')}</FieldLabel>
              <Select value={outputPort} onValueChange={setOutputPort}>
                <SelectTrigger aria-label={tr('outputPort')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {['yes', 'no'].map((value) => (
                      <SelectItem key={value} value={value}>
                        {tr(value)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
          )}
          <Field>
            <FieldLabel>{tr('targetNode')}</FieldLabel>
            <Select
              value=''
              onValueChange={(target) => {
                const sourceHandle = bindingKinds.includes(selected.data.kind)
                  ? 'binding-out'
                  : selected.data.kind === 'condition'
                    ? outputPort
                    : 'out'
                const targetHandle = bindingKinds.includes(selected.data.kind)
                  ? 'binding-in'
                  : 'in'
                const connection = {
                  source: selected.id,
                  target,
                  sourceHandle,
                  targetHandle,
                }
                if (validConnection(connection))
                  changeGraph({
                    ...draft.graph,
                    edges: [
                      ...draft.graph.edges,
                      {
                        ...connection,
                        id: crypto.randomUUID(),
                        data: {
                          kind:
                            sourceHandle === 'binding-out' ? 'binding' : 'flow',
                        },
                      },
                    ],
                  })
              }}
            >
              <SelectTrigger aria-label={tr('targetNode')}>
                <SelectValue placeholder={tr('connect')} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {draft.graph.nodes
                    .filter((node) =>
                      canConnect(
                        draft.graph.nodes,
                        draft.graph.edges,
                        selected.id,
                        node.id,
                        bindingKinds.includes(selected.data.kind)
                          ? 'binding-out'
                          : selected.data.kind === 'condition'
                            ? outputPort
                            : 'out',
                        bindingKinds.includes(selected.data.kind)
                          ? 'binding-in'
                          : 'in'
                      )
                    )
                    .map((node) => (
                      <SelectItem key={node.id} value={node.id}>
                        {node.data.label}
                      </SelectItem>
                    ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
        </>
      )}
      <Button
        variant='destructive'
        onClick={() => {
          changeGraph({
            nodes: draft.graph.nodes.filter((node) => node.id !== selected.id),
            edges: draft.graph.edges.filter(
              (edge) =>
                edge.source !== selected.id && edge.target !== selected.id
            ),
          })
          setSelectedId(null)
        }}
      >
        <Trash2 data-icon='inline-start' />
        {tr('deleteNode')}
      </Button>
      <Button variant='outline' onClick={() => setSelectedId(null)}>
        <Settings2 data-icon='inline-start' />
        {tr('done')}
      </Button>
    </FieldGroup>
  )
  return (
    <div className='flex min-h-0 flex-1 flex-col gap-3'>
      <div className='flex flex-wrap items-center gap-2'>
        <Button
          variant='ghost'
          size='icon'
          aria-label={tr('back')}
          onClick={() => (dirty ? setClosing(true) : onClose())}
        >
          <ChevronLeft />
        </Button>
        <Input
          aria-label={tr('name')}
          value={draft.name}
          maxLength={160}
          placeholder={tr('expertName')}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          className='w-44 sm:w-64'
        />
        <div className='ms-auto flex flex-wrap gap-2'>
          <Button
            variant='outline'
            size='icon'
            aria-label={tr('properties')}
            onClick={() => setMetadataOpen(true)}
          >
            <Settings2 />
          </Button>
          <Button
            variant='outline'
            size='sm'
            onClick={() => setIssues(validateGraph(draft.graph))}
          >
            <Check data-icon='inline-start' />
            {tr('validate')}
          </Button>
          <Button
            variant='outline'
            size='sm'
            disabled={!draft.name.trim()}
            onClick={() => exportDraft(draft)}
          >
            <Download data-icon='inline-start' />
            {tr('export')}
          </Button>
          <Button size='sm' onClick={save}>
            <Save data-icon='inline-start' />
            {tr('saveDraft')}
          </Button>
        </div>
      </div>
      <div className='flex items-center gap-4 text-xs text-muted-foreground'>
        <span>{tr('flow')}</span>
        <span>{tr('binding')}</span>
        <span className='ms-auto tabular-nums'>
          {t('capabilityCenter.nodeCount', { count: draft.graph.nodes.length })}
        </span>
      </div>
      <div
        className='relative flex min-h-[480px] flex-1 overflow-hidden rounded-xl border'
        style={{ height: 'calc(100dvh - 220px)' }}
      >
        <div className='relative min-w-0 flex-1'>
          <ReactFlow<ExpertNode, ExpertEdge>
            nodes={draft.graph.nodes}
            edges={draft.graph.edges.map((edge) => ({
              ...edge,
              type: 'smoothstep',
              markerEnd:
                edge.data.kind === 'flow'
                  ? { type: MarkerType.ArrowClosed }
                  : undefined,
              style:
                edge.data.kind === 'binding'
                  ? { strokeDasharray: '5 5' }
                  : undefined,
              label: ['yes', 'no'].includes(edge.sourceHandle ?? '')
                ? tr(edge.sourceHandle!)
                : undefined,
            }))}
            nodeTypes={nodeTypes}
            colorMode={resolvedTheme}
            fitView
            minZoom={0.2}
            maxZoom={2}
            onNodesChange={(changes) =>
              changeGraph({
                ...draft.graph,
                nodes: applyNodeChanges(changes, draft.graph.nodes),
              })
            }
            onEdgesChange={(changes) =>
              changeGraph({
                ...draft.graph,
                edges: applyEdgeChanges(changes, draft.graph.edges),
              })
            }
            onConnect={(connection) => {
              if (!validConnection(connection)) return
              changeGraph({
                ...draft.graph,
                edges: addEdge(
                  {
                    ...connection,
                    id: crypto.randomUUID(),
                    data: {
                      kind:
                        connection.sourceHandle === 'binding-out'
                          ? ('binding' as const)
                          : ('flow' as const),
                    },
                  },
                  draft.graph.edges
                ),
              })
            }}
            isValidConnection={validConnection}
            onNodeDoubleClick={(_, node) => setSelectedId(node.id)}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            onDragOver={(event) => {
              event.preventDefault()
              event.dataTransfer.dropEffect = 'move'
            }}
            onDrop={(event) => {
              event.preventDefault()
              const kind = event.dataTransfer.getData(
                'application/opsmesh-node'
              ) as NodeKind
              if (nodeKinds.includes(kind))
                add(
                  kind,
                  flow.screenToFlowPosition({
                    x: event.clientX,
                    y: event.clientY,
                  })
                )
            }}
            deleteKeyCode={['Backspace', 'Delete']}
            ariaLabelConfig={{
              'controls.zoomIn.ariaLabel': tr('zoomIn'),
              'controls.zoomOut.ariaLabel': tr('zoomOut'),
              'controls.fitView.ariaLabel': tr('fitView'),
              'controls.interactive.ariaLabel': tr('lockCanvas'),
              'minimap.ariaLabel': tr('minimap'),
            }}
          >
            <Background />
            <Controls />
            <MiniMap className='hidden md:block' pannable zoomable />
          </ReactFlow>
          <div className='absolute start-3 top-3'>
            <Popover open={palette} onOpenChange={setPalette}>
              <PopoverTrigger asChild>
                <Button variant='secondary'>
                  <Plus data-icon='inline-start' />
                  {tr('addNode')}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align='start'
                className='max-h-[65dvh] w-64 overflow-y-auto p-2'
              >
                <div className='grid grid-cols-2 gap-1'>
                  {nodeKinds.map((kind) => {
                    const Icon = icons[kind]
                    return (
                      <Button
                        key={kind}
                        variant='ghost'
                        className='justify-start'
                        draggable
                        onDragStart={(event) =>
                          event.dataTransfer.setData(
                            'application/opsmesh-node',
                            kind
                          )
                        }
                        onClick={() => add(kind)}
                      >
                        <Icon data-icon='inline-start' />
                        {tr(`nodes.${kind}`)}
                      </Button>
                    )
                  })}
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </div>
        {!mobile && selected && (
          <aside
            className='flex w-80 shrink-0 flex-col border-s bg-background xl:w-96'
            aria-label={tr('configuration')}
          >
            <div className='flex items-center justify-between border-b px-4 py-3'>
              <h2 className='text-sm font-semibold'>
                {tr(`nodes.${selected.data.kind}`)}
              </h2>
              <Button
                size='icon'
                variant='ghost'
                aria-label={tr('done')}
                onClick={() => setSelectedId(null)}
              >
                <X />
              </Button>
            </div>
            <div className='min-h-0 flex-1 overflow-y-auto'>{inspector}</div>
          </aside>
        )}
      </div>
      {issues !== null && (
        <div role='status' className='flex flex-wrap gap-2'>
          {(issues.length ? issues : ['graphValid']).map((issue) => (
            <Badge
              key={issue}
              variant={issues.length ? 'destructive' : 'secondary'}
            >
              {tr(issue)}
            </Badge>
          ))}
        </div>
      )}
      <Sheet
        open={mobile && !!selected}
        onOpenChange={(open) => !open && setSelectedId(null)}
      >
        <SheetContent
          className='w-full overflow-y-auto sm:max-w-md'
          aria-describedby={undefined}
        >
          <SheetHeader>
            <SheetTitle>
              {selected && tr(`nodes.${selected.data.kind}`)}
            </SheetTitle>
          </SheetHeader>
          {inspector}
        </SheetContent>
      </Sheet>
      {metadataOpen && (
        <ResourceForm
          initial={draft}
          onClose={() => setMetadataOpen(false)}
          onSave={(next) => {
            if (onSave(next)) {
              setDraft(next)
              setSaved(snapshot(next))
              setMetadataOpen(false)
            }
          }}
        />
      )}
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
    </div>
  )
}
