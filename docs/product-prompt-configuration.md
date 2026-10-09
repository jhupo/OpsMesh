# Configured execution instructions

OpsMesh has no built-in Manager, planning, acceptance, runtime-guidance or approval
prompt templates. Agent Profile instructions are user-owned database content and are
frozen in the run authorization snapshot. Node descriptions and output schemas come
from the admitted workflow. Runtime context supplies facts and authorized tool metadata.
There is no prompt configuration API, prompt binding table or default Agent.

## Conversation execution

A conversation stores messages and creates durable tasks for its configured target.
Agent and auto modes execute the selected Agent Profile. Auto mode can select the
workspace-configured conversation entry Agent, but does not require delegation tools
or inject routing instructions. An agent with authorized discovery/delegation tools
may delegate according to its own instructions. Delegated tasks retain their durable
queue, authorization, result and recovery behavior.

Team mode executes its explicitly assigned entry Agent. It can additionally specify
`orchestration_definition_id` and `orchestration_version` to execute a published
workflow against that team. Workflow invocation requires resource permission and is
rechecked during task execution. A version without a definition is rejected.

Team tasks default to `input.planning_mode = "direct"`. `"explicit"` executes supplied
`work_packages`. `"agent"` explicitly asks the assigned team planner for structured
work packages. Generated plans retain their own review policies; the platform does
not append acceptance steps or rewrite reviews. Explicit `final_acceptance` nodes
retain decision processing; all other nodes return ordinary results. Missing optional
planning or acceptance phases do not trigger recovery.

## Approvals

Workspace `settings.approvals` controls approval selection. Model review runs only
when selected by that configuration and uses its configured model and instructions.
SDK interruption/resume and platform authorization remain active.

For Claude, `settings.approvals.claude_permission_mode` accepts `default` or `auto`.
The setting is frozen in the run snapshot and cannot be overridden by Agent settings.
In `auto`, platform deny/review decisions remain enforced. A platform allow proceeds
to SDK permission evaluation rather than skipping its classifier. SDK native auto
availability depends on the deployed Claude runtime/account/model; it has not been
verified against a live provider here. No permission-bypass fallback is used.

OpenAI uses SDK approval callbacks and RunState interruption/resume. The application
provides business decisions; there is no assumption of a built-in general model reviewer.
