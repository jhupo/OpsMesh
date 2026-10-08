import { z } from 'zod'

export const capabilityKinds = ['expert', 'mcp', 'tool', 'skill'] as const
export type CapabilityKind = (typeof capabilityKinds)[number]
export const nodeKinds = [
  'start',
  'agent',
  'expert',
  'condition',
  'join',
  'approval',
  'end',
  'tool',
  'skill',
  'mcp',
  'knowledge',
  'memory',
] as const
export type NodeKind = (typeof nodeKinds)[number]
export const bindingKinds: readonly NodeKind[] = [
  'tool',
  'skill',
  'mcp',
  'knowledge',
  'memory',
]
const text = z.string().max(8000)
export const selectionSchema = z.object({
  id: z.string().max(160),
  name: z.string().max(240),
})
const parameterSchema = z.object({
  id: z.string().uuid(),
  name: z.string().max(80),
  type: z.enum(['string', 'number', 'integer', 'boolean']),
  required: z.boolean(),
  description: z.string().max(500),
})
export type Parameter = z.infer<typeof parameterSchema>
const nodeSchema = z.object({
  id: z.string().min(1).max(120),
  type: z.literal('capability'),
  position: z.object({ x: z.number().finite(), y: z.number().finite() }),
  data: z.object({
    kind: z.enum(nodeKinds),
    label: z.string().max(160),
    instructions: text,
    model: z.string().max(160),
    reference: z.string().max(240),
    referenceName: z.string().max(240),
    version: z.number().int().min(1),
    conditionPath: z.string().max(240),
    conditionOperator: z.enum(['equals', 'not_equals', 'contains', 'exists']),
    conditionValue: z.string().max(500),
    joinPolicy: z.enum(['all_success', 'all_selected']),
  }),
})
const edgeSchema = z.object({
  id: z.string().max(300),
  source: z.string(),
  target: z.string(),
  sourceHandle: z.string().nullable().optional(),
  targetHandle: z.string().nullable().optional(),
  data: z.object({ kind: z.enum(['flow', 'binding']) }),
})

// Authoring draft, deliberately NOT the backend WorkflowDefinition wire format.
// Local references and layout do not grant resource access or execute a workflow.
export const capabilityDraftSchema = z.object({
  format: z.literal('opsmesh-capability-draft'),
  schemaVersion: z.literal(1),
  id: z.string().uuid(),
  kind: z.enum(capabilityKinds),
  name: z.string().trim().min(1).max(160),
  description: z.string().max(2000),
  tags: z.array(z.string().max(40)).max(8),
  scope: z.enum(['private', 'team', 'workspace', 'public']),
  workspace: selectionSchema.nullable(),
  owner: selectionSchema.nullable(),
  updatedAt: z.string().datetime(),
  configuration: z.object({
    endpoint: z.string().max(2000),
    transport: z.enum(['streamable_http', 'sse', 'stdio']),
    credential: selectionSchema.nullable(),
    authentication: z.enum(['none', 'bearer', 'api_key', 'oauth2']),
    instructions: text,
    inputs: z.array(parameterSchema).max(32),
    outputs: z.array(parameterSchema).max(32),
    source: z.enum(['http', 'mcp', 'plugin']),
    reference: selectionSchema.nullable(),
    runtime: selectionSchema.nullable(),
    method: z.enum(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']),
    timeout: z.number().int().min(1).max(300),
    requiresApproval: z.boolean(),
    risk: z.enum(['low', 'medium', 'high']),
    dependencies: z.array(selectionSchema).max(32),
  }),
  graph: z.object({
    nodes: z.array(nodeSchema).max(128),
    edges: z.array(edgeSchema).max(512),
  }),
})
export type CapabilityDraft = z.infer<typeof capabilityDraftSchema>
export type ExpertNode = z.infer<typeof nodeSchema>
export type ExpertEdge = z.infer<typeof edgeSchema>

export function createNode(
  kind: NodeKind,
  label: string,
  position: { x: number; y: number }
): ExpertNode {
  return {
    id: crypto.randomUUID(),
    type: 'capability',
    position,
    data: {
      kind,
      label,
      instructions: '',
      model: '',
      reference: '',
      referenceName: '',
      version: 1,
      conditionPath: '',
      conditionOperator: 'equals',
      conditionValue: '',
      joinPolicy: 'all_success',
    },
  }
}

export function createDraft(kind: CapabilityKind): CapabilityDraft {
  return {
    format: 'opsmesh-capability-draft',
    schemaVersion: 1,
    id: crypto.randomUUID(),
    kind,
    name: '',
    description: '',
    tags: [],
    scope: 'private',
    workspace: null,
    owner: null,
    updatedAt: new Date().toISOString(),
    configuration: {
      endpoint: '',
      transport: 'streamable_http',
      credential: null,
      authentication: 'none',
      instructions: '',
      inputs: [],
      outputs: [],
      source: 'http',
      reference: null,
      runtime: null,
      method: 'GET',
      timeout: 30,
      requiresApproval: false,
      risk: 'low',
      dependencies: [],
    },
    graph: { nodes: [], edges: [] },
  }
}

export function exportDraft(draft: CapabilityDraft) {
  const blob = new Blob(
    [JSON.stringify(capabilityDraftSchema.parse(draft), null, 2)],
    { type: 'application/json' }
  )
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `opsmesh-${draft.kind}-${draft.id}.draft.json`
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function canConnect(
  nodes: ExpertNode[],
  edges: ExpertEdge[],
  source: string,
  target: string,
  sourceHandle: string | null,
  targetHandle: string | null
) {
  if (source === target || edges.length >= 512) return false
  const from = nodes.find((node) => node.id === source)
  const to = nodes.find((node) => node.id === target)
  if (!from || !to) return false
  if (
    edges.some(
      (edge) =>
        edge.source === source &&
        edge.target === target &&
        edge.sourceHandle === sourceHandle
    )
  )
    return false
  if (bindingKinds.includes(from.data.kind)) {
    return (
      sourceHandle === 'binding-out' &&
      targetHandle === 'binding-in' &&
      to.data.kind === 'agent'
    )
  }
  if (
    targetHandle !== 'in' ||
    bindingKinds.includes(to.data.kind) ||
    to.data.kind === 'start' ||
    from.data.kind === 'end'
  )
    return false
  if (
    from.data.kind === 'condition'
      ? !['yes', 'no'].includes(sourceHandle ?? '')
      : sourceHandle !== 'out'
  )
    return false
  const visited = new Set<string>()
  const stack = [target]
  while (stack.length) {
    const id = stack.pop()!
    if (id === source) return false
    if (visited.has(id)) continue
    visited.add(id)
    stack.push(
      ...edges
        .filter((edge) => edge.data.kind === 'flow' && edge.source === id)
        .map((edge) => edge.target)
    )
  }
  return true
}

export function validateGraph(graph: CapabilityDraft['graph']): string[] {
  const { nodes, edges } = graph
  const issues = new Set<string>()
  if (
    nodes.filter((node) => node.data.kind === 'start').length !== 1 ||
    !nodes.some((node) => node.data.kind === 'end')
  )
    issues.add('missingTerminals')
  if (!nodes.some((node) => ['agent', 'expert'].includes(node.data.kind)))
    issues.add('missingAgent')
  if (
    new Set(nodes.map((node) => node.id)).size !== nodes.length ||
    new Set(edges.map((edge) => edge.id)).size !== edges.length
  )
    issues.add('invalidEdges')
  edges.forEach((edge, index) => {
    if (
      !canConnect(
        nodes,
        edges.slice(0, index),
        edge.source,
        edge.target,
        edge.sourceHandle ?? null,
        edge.targetHandle ?? null
      )
    )
      issues.add('invalidEdges')
    const from = nodes.find((node) => node.id === edge.source)
    if (
      from &&
      (bindingKinds.includes(from.data.kind) ? 'binding' : 'flow') !==
        edge.data.kind
    )
      issues.add('invalidEdges')
  })
  const reached = new Set<string>()
  const stack = nodes
    .filter((node) => node.data.kind === 'start')
    .map((node) => node.id)
  while (stack.length) {
    const id = stack.pop()!
    if (reached.has(id)) continue
    reached.add(id)
    stack.push(
      ...edges
        .filter((edge) => edge.data.kind === 'flow' && edge.source === id)
        .map((edge) => edge.target)
    )
  }
  nodes.forEach((node) => {
    if (!node.data.label.trim()) issues.add('missingName')
    if (bindingKinds.includes(node.data.kind)) {
      if (node.data.kind !== 'memory' && !node.data.reference.trim())
        issues.add('missingReference')
      if (
        !edges.some(
          (edge) => edge.source === node.id && edge.data.kind === 'binding'
        )
      )
        issues.add('unboundResource')
    } else {
      if (!reached.has(node.id)) issues.add('disconnected')
      if (
        node.data.kind !== 'end' &&
        !edges.some(
          (edge) => edge.source === node.id && edge.data.kind === 'flow'
        )
      )
        issues.add('disconnected')
    }
    if (
      node.data.kind === 'agent' &&
      (!node.data.model.trim() || !node.data.instructions.trim())
    )
      issues.add('missingAgentConfig')
    if (node.data.kind === 'expert' && !node.data.reference.trim())
      issues.add('missingReference')
    if (
      node.data.kind === 'condition' &&
      (!node.data.conditionPath.trim() ||
        !['yes', 'no'].every((handle) =>
          edges.some(
            (edge) => edge.source === node.id && edge.sourceHandle === handle
          )
        ))
    )
      issues.add('missingCondition')
  })
  return [...issues]
}
