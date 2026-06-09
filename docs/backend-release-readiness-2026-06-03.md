# Backend Release Readiness - 2026-06-03

## Current Goal

Prepare the backend for updated deployment on `192.168.2.17` and rerun the real-provider
team execution E2E after removing runtime fake runner behavior.

## Local State

- Branch: `master`
- Local branch is ahead of `origin/master` by 24 commits.
- Current working tree also contains uncommitted backend fixes and docs updates.
- Remote server `/opt/chaincloud-app/current` is not a git worktree.
- Remote server `/tmp/chaincloud-agent-team-codex-full` is an old source copy and still contains
  runtime fake runner code.

## Changes Prepared Locally

- Removed runtime runner backend selection and `CHAINCLOUD_AGENT_RUNNER_BACKEND`.
- Deleted production `backend/app/agent_runtime/fake.py`.
- API/worker execution now defaults to `OpenAIAgentsRunner`.
- Tests use explicit deterministic test runners instead of runtime fake configuration.
- Added task collaboration recovery plan/dry-run/apply APIs.
- Strengthened worker status-transition regression coverage for running-state flush before model calls.
- Enhanced `scripts/real-team-e2e.py` output with task breakdown, run lifecycle events, and task messages.
- Updated backend task table:
  - Multi-agent execution loop: local closure done, pending real-provider E2E on updated deployment.
  - MCP skill lifecycle/tool permission hardening: done for current SDK.
  - Workspace data lifecycle: done for current phase.

## Verification

Latest local checks:

```bash
uv run pytest backend/tests/test_worker_run_execution.py backend/tests/test_agent_runtime.py backend/tests/test_config.py backend/tests/test_deployment_assets.py backend/tests/test_workspace_api.py::test_api_team_task_e2e_runs_workers_and_accepts_delivery backend/tests/test_workspace_api.py::test_team_execution_loop_run_advances_actions_runs_and_finalization backend/tests/test_workspace_api.py::test_task_collaboration_recovery_plan_dry_run_and_apply_selected_actions backend/tests/test_workspace_api.py::test_task_delivery_decision_approves_or_requests_follow_up_and_redacts -q
uv run ruff check scripts/real-team-e2e.py backend/app/agent_runtime/factory.py backend/app/core/config.py backend/app/orchestration/runs.py backend/app/workers/handlers.py backend/app/workers/cli.py backend/app/tasks/collaboration_recovery.py backend/app/api/routes/workspace_resources.py backend/app/api/schemas/tasks.py backend/tests/test_agent_runtime.py backend/tests/test_worker_run_execution.py backend/tests/test_workspace_api.py backend/tests/test_config.py backend/tests/test_deployment_assets.py
git diff --check
```

Result:

- Targeted pytest passed.
- Ruff passed.
- `git diff --check` passed.

## Remote Real E2E Status

Current server-test deployment on `192.168.2.17` is release `61945a0`. The API
container is healthy and the worker starts under
`deploy/server/docker-compose.backend.yml`.

The real HTTP E2E creates a workspace, model provider credential, three agents, a team,
and a task, then decomposes the task into manager planning, researcher, analyst, and
manager summary work packages. The worker claims the first manager-planning run and
starts a real `gpt-5.5` request through the configured OpenAI-compatible provider.

The current blocker is upstream provider capacity, not task decomposition or queue
dispatch. The provider returns:

```text
503 No available accounts: no available accounts
```

The run records `run.claimed`, `run.context_built`, `model.request_started`,
`model.request_failed`, and `run.failed`. Provider usage audit now records
`model_provider.request_failed` with the task, step, agent, run, credential, model,
protocol, provider, and redacted failure reason. The HTTP E2E script treats this as a
failed E2E with `diagnosis=provider_request_failed`, rather than reporting missing
platform evidence.

## Release Blocker

There is no deployment blocker for release `61945a0`. The remaining blocker for full
real-provider completion is a provider key/account with available upstream capacity.

Rerun the HTTP E2E inside the API container:

```bash
docker exec -e CHAINCLOUD_HTTP_E2E_API_URL=http://127.0.0.1:8000/api/v1 \
  -e CHAINCLOUD_HTTP_E2E_API_KEY=... \
  -e CHAINCLOUD_HTTP_E2E_BASE_URL=https://dash.ovload.com/ \
  -e CHAINCLOUD_HTTP_E2E_MODEL=gpt-5.5 \
  -e CHAINCLOUD_HTTP_E2E_TIMEOUT_SECONDS=240 \
  chaincloud-api python /app/scripts/real-team-http-e2e.py
```

Do not print or store the real API key in logs or docs.
