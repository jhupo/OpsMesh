# Runtime execution modes

Managed Docker runtimes support three execution modes. The mode is selected when a workspace
runtime is created and is frozen on the runtime record.

| Mode | Container lifecycle | Run workspace | Intended use |
| --- | --- | --- | --- |
| `isolated` | A child container and volume are created for each run and removed at terminal state. | A fresh `/workspace/runs/<run_id>` volume. | Untrusted code and strict isolation. |
| `pooled` | A pre-provisioned container is leased for one run at a time and returned after reset. | A run-specific `/workspace/runs/<run_id>` tree in the member volume. | Normal runs where startup cost matters. |
| `persistent` | The dedicated runtime container stays alive across runs. | A run-specific tree is retained by policy. | Long-lived development or interactive sessions. |

## Pool membership

Each runtime created with `execution_mode=pooled` is one pre-provisioned pool member. Members with the
same `pool_key`, workspace, runtime template, runtime space, limits, network policy, and hardening
policy form a pool. A run is authorized against a pool anchor; the runtime service leases an available
member from that pool. If `pool_key` is omitted, the runtime is a single-member pool.

The pool does not share a task's filesystem state. A logical run runtime records the parent anchor and
the concrete pool member. The member's Docker container ID is reused, while the run's project files,
working directory, credentials, process group, and tool context remain run-scoped.

## Lease and reset invariants

- A member has at most one active pool lease. Lease acquisition uses a row lock and records the run ID.
- A pooled run cannot be authorized unless its logical child, member, container ID, workspace, runtime
  space, and active lease all match the frozen authorization binding.
- Terminal cleanup kills non-init processes, removes the run workspace tree, releases the member lease,
  and marks the logical child deleted. No pooled cleanup removes the member's persistent Docker volume.
- If reset fails, the member is destroyed and is never returned to the pool. Cleanup evidence remains
  durable for operations and audit review.
- Persistent project workspace cleanup is explicitly `not_required`; retaining that state is the mode's
  contract. The persistent runtime lease is released when the run reaches a terminal state.

## API

`POST /workspaces/{workspace_id}/runtimes` accepts:

```json
{
  "template_id": "...",
  "name": "python-pool-1",
  "execution_mode": "pooled",
  "pool_key": "python-default"
}
```

Create several members with the same `pool_key` to increase concurrency. The existing runtime CPU,
memory, disk, process, network, runtime-space, and image policies apply to every member. There is no
cross-tenant pool: pool selection always includes the workspace boundary and exact security policy.
