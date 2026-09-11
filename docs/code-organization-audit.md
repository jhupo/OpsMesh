# Backend application organization audit

## Scope

This audit covers `backend/app`, not only `orchestration`. The repository currently has many
package directories whose only purpose is to contain one or two implementation files. That adds
import depth and makes ownership harder to discover without improving runtime isolation.

## Findings

- `agent_runtime` is split across `core`, `adapters`, `openai`, `claude`, and `sandbox`; these are
  provider/runtime concerns and should be grouped under one provider/runtime boundary.
- `runtime_manager` is split across `core`, `backends`, `lifecycle`, and `pool`, while related
  runtime entities also exist in `runtime_spaces` and `runtimes`.
- Observability is spread across `audit`, `costs`, and `telemetry` despite sharing event, trace,
  and usage concerns.
- Storage concerns are spread across `files`, `artifacts`, `exports`, and `projects`.
- `orchestration` contains several small packages (`models_layer`, `planning`, `policies`,
  `state`, `steps`, `workflows`, and `runs`) with overlapping execution/planning ownership.
- `teams/project_space`, `workers/queue`, and `tools/product_tools` are small nested packages
  that should remain files or direct children of their owning domain until they have independent
  lifecycle or extension boundaries.

## Target boundaries

The target is a two-level domain structure. A package is retained only when it represents an
independent lifecycle, dependency boundary, or extension point.

```text
backend/app/
├── api/
├── auth/
├── agents/
├── agent_runtime/
├── capabilities/
├── orchestration/
├── projects/
├── tasks/
├── teams/
├── runs/
├── runtime/
├── storage/
├── observability/
├── operations/
└── workers/
```

Planned consolidations:

| Current areas | Target | Reason |
| --- | --- | --- |
| `agent_runtime/core`, `adapters`, `openai`, `claude`, `sandbox` | `agent_runtime/providers` and `agent_runtime/runtime` | Keep SDK/provider extension points without five nested boundaries. |
| `runtime_manager/*`, `runtime_spaces`, `runtimes` | `runtime` | One owner for runtime backends, pools, leases, and execution policy. |
| `audit`, `costs`, `telemetry`, related notifications | `observability` | Shared event, trace, usage, and audit lifecycle. |
| `files`, `artifacts`, `exports`, project file I/O | `storage` plus `projects` | Separate physical storage from project domain behavior. |
| `orchestration/planning`, `workflows`, `steps`, `policies`, `state` | `orchestration/workflows` and `orchestration/runs` | Remove overlapping micro-packages. |
| `orchestration/run_request` | `orchestration/requests` | Naming reflects request construction rather than an implementation detail. |
| `orchestration/models_layer` | `orchestration/models` | Replace generic refactor name with domain ownership. |

## Guardrails for the refactor

1. Do not merge modules solely because they are short; preserve a package when it has a true
   lifecycle, public extension contract, or materially different dependency direction.
2. Move one domain group at a time, update imports explicitly, run focused tests, and commit each
   group independently.
3. Do not introduce compatibility shims or duplicate module paths. The old path is removed after
   each move.
4. Keep API route grouping and migration history stable unless a route or schema boundary is
   actually changing.
