# Execution ownership

`agents` owns reusable configurations and native SDK assembly. `execution` owns
persistent instance identities, private conversations, sandbox environments and
individual runs. `workflows` owns orchestration and its Temporal adapters.
The execution package does not import Temporal or orchestration modules.

An instance is a persisted logical Agent, not a serialized Python object. Creation
authorizes and freezes the configuration graph, including handoffs and Agent tools.
Each invocation constructs native SDK objects from that snapshot inside a bounded
resource lifespan. SDK connection cleanup does not retire the logical instance or
delete its independently owned environment.

```text
execution/
  preparation.py       authorized configuration graph snapshots
  contracts.py         detached execution and history values
  context.py           run and attempt identities
  bindings.py          native model authorization and quota binding
  runner.py            native build_agent / Runner invocation and approval resume
  model.py             native model calls, usage and effect receipts
  tools.py             authorized plugin invocation and effect receipts
  mcp.py               caller authorization around native MCP RPCs
  instances/           persistent logical Agent and SandboxAgent records / APIs
  runtime/             persistent sandbox operations and directory authority
  sessions/            instance-bound conversations and native SDK history
  runs/                admission, execution, approvals, results and audit events
```

## Lifecycles

- Configuration edits affect future instance creation. Existing instances retain
  their encrypted snapshots; credentials and resource authorization are resolved
  again at execution boundaries.
- Ordinary Agents require no environment. A configuration graph containing a
  SandboxAgent requires an explicitly created, owned, ready environment.
- Run completion, failure and cancellation release concurrency reservations and
  environment occupancy. They do not delete the instance, environment or directory.
- An interrupted run retains its environment occupancy while awaiting approval.
  A run with unconfirmed external effects retains occupancy until reconciliation.
- Instance deletion is explicit and blocked by unfinished runs. It does not cascade
  into private sessions or independently owned environments.
- Environment deletion is the explicit `close` operation. Runtime operations cannot
  race a run that owns the environment. Closing releases the environment quota;
  the mounted resource directory remains independently managed.
- Session reset/deletion requires an idle session. Reset clears native SDK history
  and increments its generation so stale references cannot write to the new history.

## Host assembly

`bootstrap/execution.py` wires the authorized definition loader, resource binding
factory, direct executor and orchestration dispatch callbacks. The built-in Agent
manager remains a configuration catalog; it is not the persistent instance store.
The standard API and Worker use the encrypted, published `agents/catalog` database
view. `OPSMESH_AGENT_CATALOG_PATH` supplies initial `agents` and `providers` only;
restarts never overwrite managed records. Credentials use `env:VARIABLE` or
write-only `secret:identifier` references. Each admitted Run freezes provider
parameters and prices. Trusted embeddings may inject different authorized loaders.
Model retries, durable event streaming and configured cost budgets are documented
in [platform management](../../../docs/platform-management.md).

Tool admission persists the descriptions, approval policies, plugin versions and
installation configuration digests. Invocation checks the admitted description
against the current plugin registry; changes require a new run. An interrupted
run cannot approve and execute a different plugin installation after reload.

Direct execution uses `RunExecutor` with no scheduler dependency. Sandbox execution
also needs an explicitly injected authorized `AgentSandboxClient`. The API process
does not acquire Docker authority by default; its direct sandbox endpoint returns
503 until such an executor is configured. The Temporal Worker owns the Docker client.

Before Runner resumes the borrowed environment, `runtime/preparation.py` combines
the graph's manifests and selected static skills. Conflicting declarations, extra
mounts and roots outside `/workspace` are rejected. Session start uses the SDK's
public `apply_manifest()` to install declared files; it does not clear unrelated
workspace contents. Declared files are refreshed at session start, including an
approval continuation. Each SDK operation has a distinct durable receipt even when
one sandbox Activity performs several installation steps. Cleanup closes only the
borrowed reference and leaves the persistent environment intact.
Persistent instances use the compute image's identity. A non-null `run_as` is
rejected when freezing an instance; a Run cannot switch users or provision them
through its borrowed manifest. The default layout preserves shared-directory
write permissions. Explicit entry permissions remain part of the declaration.

`runtime/helpers/` contains the trusted programs used for bounded file reads,
atomic writes, descriptor-based ACL changes and command environment setup. They
are packaged as inspectable Python modules and sent to the compute image's
`python3 -I -c`; they are not generated from user code. The image also requires
`setfacl`. Missing files preserve the SDK's `WorkspaceReadNotFoundError`, including
across Temporal Activities, so native Memory can create its first rollout file.

The trusted host supplies Principal and InvocationSource through ExecutionContext;
HTTP run input cannot choose them. Run snapshots retain the source while account roles
are reloaded at invocation boundaries. Function tools filter discovery and authorize
execution, including cached receipts. MCP factories and RPCs use the same caller policy.
Plugins own business data permissions; model instructions only expose minimal caller facts.
Native approval state uses an explicit context serializer and restore-time context override.
See [gateway and identity contracts](../../../.agents/references/gateway-and-identity.md).

`workflows/engines/temporal/agent_*` provides the Temporal adapter. The official Agents
plugin runs native Runner logic in the Workflow; model, tool, MCP, sandbox and history
I/O execute in Activities. Dispatch intents are staged in the same PostgreSQL
transaction as the run or sandbox operation. Approved continuations retain the live
native RunState inside the same Workflow and recover it through replay.
Approval decisions wake that Workflow through an Outbox Signal. Capacity waits
back off to 60 seconds and Continue-As-New before admission to bound history;
approved continuations keep their live SDK state and use a bounded admission wait.

The default Temporal binding adapter handles registered function tools, skills,
manifest entries and model references. Dynamic instructions, prompts, input/output
guardrails and Agent lifecycle hooks use native SDK proxies backed by Activities.
Registrations require an immutable `revision`, and Agents in a callback-bearing
graph require unique names. Activities receive a copy of the native callback context
and Agent configuration; mutations and factory state do not flow back into Runner.
Hooks are notifications, not a way to mutate the running Agent. Callback results
must be JSON serializable. Other callback contracts, including Python type factories
and lazy generators, are rejected during admission rather than executed in Workflow
code. Plugin hook revisions include package versions and installation configuration.
An omitted MCP
approval setting uses `always` in this adapter because a factory's default policy
cannot be inspected through the public SDK API in Workflow code. Declare the desired
policy on the MCP binding for consistent behavior across execution drivers.

Temporal's encrypted codec limits each encoded payload to 1,900,000 bytes and a
payload batch to 3,500,000 bytes. The check includes encryption and encoding overhead;
admission validates the complete result before transport and releases a failed Run's
reservations. Large resources must be reduced before submission.

## HTTP sequence

Paths below are relative to the configured API prefix.

1. For a SandboxAgent, `POST /execution/environments` with an allowed `image` and
   optional owned `directory_id`. Poll the returned environment until `ready`.
2. `POST /execution/instances` with `agent_id`, `request_key`, optional `name` and
   the sandbox `environment_id` when required.
3. Optionally `POST /execution/sessions` with `instance_id` for private history.
4. `POST /execution/runs` with `instance_id`, `request_key`, `input`, optional
   `session_id`, and `driver` (`direct` or a registered orchestration adapter).
5. Direct runs execute through `POST /execution/runs/{id}/execute`; a queued
   Temporal run is dispatched through the transactional outbox.
6. Read status, `/result` and paginated `/events`. For interruptions, submit the
   current `approval_id` and `allow` to `/approval`. Direct runs need another
   explicit `/execute`; the existing Temporal Workflow observes the decision.
7. Cancel a run through `/cancel`. Delete an instance through
   `DELETE /execution/instances/{id}`. Close an environment separately through
   `POST /execution/environments/{id}/operations` with
   `{"request_key": "close-1", "action": "close"}`.

Model calls require a positive `max_tokens` bound from configuration or provider
metadata. Media, hosted tools, remote history and prompts additionally require a
declared context limit. A plain model call has an `inference` receipt: if its usage
is unconfirmed, the Run stops as failed/cancelled with `model_usage_unknown` and
releases Session ordering and concurrency capacity. Token/cost reservations remain
held until usage is confirmed or an administrator reconciles them with evidence.
The next Session Run can proceed without blindly retrying the failed inference.
Hosted tools, remote history/prompts, opaque request overrides, history writes and
tool effects retain conservative `needs_attention` protection when uncertain.
Administrators resolve stopped operations through the run reconciliation endpoint
with evidence and confirmed token usage when applicable. Existing unknown `model`
receipts are not reclassified automatically.
Custom native tools and direct MCP resolvers must provide their own effect receipts
when they perform external writes. A crashed direct process requires operator
recovery; this executor does not claim automatic process-level recovery.

## Migration and verification limits

`migrations/versions/0002_execution_instances.py` introduces instances, instance
references, execution drivers, attempt identities and encrypted run events. Existing
revision-bound rows remain historical records and are not silently converted into
new authorized instances. The migration requires an explicit data migration to
downgrade and has not been applied to a live database in this change.

Orchestration adapters now live in `workflows/engines/temporal`; bootstrap injects
their dispatch functions without introducing an execution-to-workflows dependency.
The workflow module owns its definitions, bound instances and graph Runs, while
each invoked Agent has a separate execution Run. Legacy revision-bound graph and
provider adapters have been removed from API/Worker assembly. See the workflows
README for migration boundaries; old Temporal histories are not automatically upgraded.

No tests or acceptance runs were requested. PostgreSQL migration, real Temporal
replay/cancellation/Worker restart, Docker persistence and external model/MCP behavior
remain unverified until explicitly exercised against those services.
