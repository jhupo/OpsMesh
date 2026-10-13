"""Persisted native sessions and tool admission on real PostgreSQL.

The native SDK boundary double does not certify Docker execution.
"""

import asyncio
import os
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.contracts import AgentToolInterruption
from opsmesh.agents.execution.providers.openai.sandbox_activities import (
    AgentSandboxActivities,
    AgentSandboxToolOperation,
)
from opsmesh.agents.sessions.models import AgentSessionMetadata
from opsmesh.capabilities.catalog.effective import effective_catalog_fingerprint
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.identity.users.models import User
from opsmesh.orchestration.approvals.agent_tool_interruptions import AgentToolInterruptionService
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun, authorization_snapshot_fingerprint
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.snapshots import SandboxSnapshotStorage
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import ConflictError, PolicyDeniedError
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.native_sandbox import native_boundary

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def bound_sandbox(state):
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None and definition.session is not None
    with state.sessions() as session:
        runtime = WorkspaceRuntime(
            workspace_id=state.request.workspace_id,
            name="Authorized native environment",
            status="active",
            sandbox_backend_id="docker",
            sandbox_options={"image": "python:3.13"},
        )
        session.add(runtime)
        session.flush()
        run = session.get(AgentRun, state.request.resource_id)
        run.runtime_id = runtime.id
        team = AgentTeam(
            workspace_id=run.workspace_id,
            name="Native environment team",
            default_task_policy={"team_runtime": {"workspace_runtime_id": str(runtime.id)}},
        )
        session.add(team)
        session.flush()
        session.get(Task, run.task_id).agent_team_id = team.id
        snapshot = deepcopy(run.input["authorization_snapshot"])
        snapshot["runtime_binding"] = {
            "mode": "team_runtime",
            "workspace_id": str(run.workspace_id),
            "workspace_runtime_id": str(runtime.id),
            "runtime_space_id": None,
            "capability_resource_ids": [],
            "file_access_scope": {"mode": "gateway_only", "allowed_file_ids": []},
        }
        catalog = snapshot["capability_catalog"]
        catalog["team_id"] = str(team.id)
        catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        run.input = {**run.input, "authorization_snapshot": snapshot}
        run.session_key = None
        session.commit()
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    native, client, registry = native_boundary()
    native.supports_pty.return_value = False
    native.running.return_value = True
    admission = AgentSandboxActivities(state.sessions, Settings(environment="test"), registry)
    return definition, admission, native, client, registry


def test_native_environment_reuses_reference_across_turns_and_agent_session_scopes(
    native_execution,
):
    state = native_execution
    _, admission, native, client, _ = bound_sandbox(state)
    first = asyncio.run(admission.prepare(state.request))
    asyncio.run(admission.checkpoint(first))
    with state.sessions() as session:
        old = session.get(AgentRun, state.request.resource_id)
        persistent = session.scalar(
            select(AgentSessionMetadata).where(
                AgentSessionMetadata.workspace_id == old.workspace_id,
                AgentSessionMetadata.session_key == old.session_key,
            )
        )
        # The SDK transcript scope changes on handoff; its conversation/task scope
        # and native environment session remain the same.
        next_key = f"{old.workspace_id}:handoff-transcript"
        session.add(
            AgentSessionMetadata(
                workspace_id=old.workspace_id,
                session_key=next_key,
                scope_type=persistent.scope_type,
                scope_id=f"{persistent.scope_id.rsplit(':', 1)[0]}:{uuid4()}",
                task_id=old.task_id,
                agent_profile_id=old.agent_profile_id,
            )
        )
        second_run = AgentRun(
            workspace_id=old.workspace_id,
            task_id=old.task_id,
            agent_profile_id=old.agent_profile_id,
            runtime_id=old.runtime_id,
            input=deepcopy(old.input),
            session_key=next_key,
            status="running",
        )
        session.add(second_run)
        session.flush()
        second_id = second_run.id
        snapshot = second_run.input["authorization_snapshot"]
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        session.commit()
    request = state.request.model_copy(update={"resource_id": second_id, "execution_id": second_id})
    second = asyncio.run(admission.prepare(request))
    assert second.reference_id == first.reference_id
    assert second.state["session_id"] == first.state["session_id"]
    client.create.assert_awaited_once()
    client.delete.assert_not_awaited()
    with state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(SandboxReference)) == 1
        reference = session.get(SandboxReference, first.reference_id)
        assert reference.status == "running"
        assert "native-fixture" not in reference.encrypted_session_state
    admission.authorize_native(request.workflow_id, native.state)
    with pytest.raises(ResourceAccessDeniedError):
        admission.authorize_native(f"opsmesh:{uuid4()}:agent:{second_id}", native.state)


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("exec_command", {"cmd": "curl https://example.invalid/change"}),
        ("write_stdin", {"session_id": "pty-1", "chars": "deploy\n"}),
        (
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Add File: deploy.py\n+pass\n*** End Patch"},
        ),
    ],
)
def test_native_tools_require_matching_durable_approval_and_do_not_replay_unknown_outcome(
    native_execution, tool_name, arguments
):
    state = native_execution
    definition, admission, _, _, _ = bound_sandbox(state)
    with state.sessions() as session:
        workspace = session.get(Workspace, state.request.workspace_id)
        workspace.settings = {
            **workspace.settings,
            "approvals": {
                "rules": [
                    {"id": "native-review", "action": "runtime_command", "decision": "review"}
                ]
            },
        }
        session.commit()
    operation = AgentSandboxToolOperation(
        context=definition.context,
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id="native-call",
    )
    review = asyncio.run(admission.review(operation))
    assert review["decision"] == "require_approval"
    with pytest.raises(PolicyDeniedError):
        asyncio.run(admission.admit(operation))
    with state.sessions() as session:
        AgentToolInterruptionService(session, admission._secrets).persist(
            context=definition.context,
            requested_by_agent_profile_id=state.profile_id,
            interruptions=(
                AgentToolInterruption(
                    tool_call_id=operation.tool_call_id,
                    tool_name=tool_name,
                    tool_kind="sandbox",
                    arguments=arguments,
                    policy_decision=review,
                ),
            ),
        )
        session.commit()
        approval = session.scalar(
            select(Approval).where(Approval.agent_run_id == state.request.resource_id)
        )
        ApprovalDecisionService(session, admission._secrets).approve(
            approval, state.user_id, "Reviewed"
        )
        session.commit()
    approved = operation.model_copy(update={"approval_granted": True})
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(
            admission.admit(
                approved.model_copy(update={"arguments": {**arguments, "changed": True}})
            )
        )
    assert asyncio.run(admission.admit(approved)) is None
    cached = asyncio.run(admission.admit(approved))
    assert cached["error"]["code"] == "tool_execution_outcome_unknown"


def test_native_activity_reauthorization_denies_revoked_user_but_cleanup_preserves_session(
    native_execution,
):
    state = native_execution
    definition, admission, native, client, _ = bound_sandbox(state)
    binding = asyncio.run(admission.prepare(state.request))
    with state.sessions() as session:
        session.get(User, state.user_id).status = "disabled"
        session.get(AgentRun, state.request.resource_id).status = "cancelled"
        session.commit()
    with pytest.raises(ResourceAccessDeniedError):
        admission.authorize_native(state.request.workflow_id, native.state)
    with pytest.raises(ResourceAccessDeniedError):
        asyncio.run(
            admission.review(
                AgentSandboxToolOperation(
                    context=definition.context,
                    tool_name="exec_command",
                    arguments={"cmd": "pwd"},
                    tool_call_id="revoked-call",
                )
            )
        )
    asyncio.run(admission.checkpoint(binding))
    client.delete.assert_not_awaited()
    native.stop.assert_awaited_once()
    with state.sessions() as session:
        assert session.get(SandboxReference, binding.reference_id).status == "stopped"


@pytest.mark.parametrize("concurrent_delete", [False, True])
def test_native_checkpoint_does_not_recreate_deleted_environment(
    native_execution, concurrent_delete
):
    state = native_execution
    _, admission, native, client, _ = bound_sandbox(state)
    binding = asyncio.run(admission.prepare(state.request))
    client.resume.reset_mock()

    def mark_deleted():
        with state.sessions() as session:
            session.get(SandboxReference, binding.reference_id).status = "deleted"
            session.commit()

    if concurrent_delete:

        async def resume(saved):
            mark_deleted()
            native.state = native.state.model_copy(update={"container_id": "replacement"})
            return native

        client.resume.side_effect = resume
        with pytest.raises(ConflictError):
            asyncio.run(admission.checkpoint(binding))
        client.delete.assert_awaited_once_with(native)
    else:
        mark_deleted()
        asyncio.run(admission.checkpoint(binding))
        client.resume.assert_not_awaited()
        client.delete.assert_not_awaited()


def test_native_snapshot_key_survives_next_turn_but_keeps_workspace_scope():
    workspace_id, snapshot_id = uuid4(), str(uuid4())

    async def scenario():
        environment = ActivityEnvironment()

        async def key():
            return SandboxSnapshotStorage._key(snapshot_id)

        keys = []
        for scope in [workspace_id, workspace_id, uuid4()]:
            environment.info = replace(
                environment.info, workflow_id=f"opsmesh:{scope}:agent:{uuid4()}"
            )
            keys.append(await environment.run(key))
        assert keys[0] == keys[1] and keys[0] != keys[2]

    asyncio.run(scenario())


def test_native_resume_uses_latest_resource_after_worker_replacement(native_execution):
    state = native_execution
    _, admission, native, client, _ = bound_sandbox(state)
    binding = asyncio.run(admission.prepare(state.request))
    original = native.state.model_copy(deep=True)
    replacement = original.model_copy(update={"container_id": "replacement-resource"})
    admission.record_native_resume(state.request.workflow_id, original, replacement)
    authoritative = admission.authorize_native(state.request.workflow_id, original)
    assert authoritative.container_id == "replacement-resource"
    native.state = replacement
    client.resume.reset_mock()
    asyncio.run(admission.checkpoint(binding))
    assert client.resume.await_args.args[0].container_id == "replacement-resource"
    client.create.assert_awaited_once()
    client.delete.assert_not_awaited()


@pytest.mark.parametrize("placement", ["independent_task", "parallel_step"])
def test_native_checkpoint_and_cancel_do_not_stop_other_concurrent_scope(
    native_execution, placement
):
    state = native_execution
    _, admission, first_native, client, _ = bound_sandbox(state)
    second_native, _, _ = native_boundary()
    second_native.running.return_value = True
    first_state, second_state = first_native.state, second_native.state
    natives = {
        str(first_state.session_id): first_native,
        str(second_state.session_id): second_native,
    }
    client.create.side_effect = [first_native, second_native]
    client.resume.side_effect = lambda saved: natives[str(saved.session_id)]
    first = asyncio.run(admission.prepare(state.request))
    with state.sessions() as session:
        old = session.get(AgentRun, state.request.resource_id)
        snapshot = deepcopy(old.input["authorization_snapshot"])
        if placement == "independent_task":
            task = Task(
                workspace_id=old.workspace_id,
                title="Independent task",
                status="running",
                created_by_user_id=state.user_id,
                owner_agent_profile_id=old.agent_profile_id,
                agent_team_id=session.get(Task, old.task_id).agent_team_id,
                execution_identity=deepcopy(session.get(Task, old.task_id).execution_identity),
            )
            session.add(task)
            session.flush()
            snapshot["task_id"] = str(task.id)
            task_id, step_id = task.id, None
        else:
            step = TaskStep(
                workspace_id=old.workspace_id,
                task_id=old.task_id,
                title="Parallel step",
                assigned_agent_profile_id=old.agent_profile_id,
                status="running",
            )
            session.add(step)
            session.flush()
            task_id, step_id = old.task_id, step.id
            snapshot["task_step_id"] = str(step.id)
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        second_run = AgentRun(
            workspace_id=old.workspace_id,
            task_id=task_id,
            task_step_id=step_id,
            agent_profile_id=old.agent_profile_id,
            runtime_id=old.runtime_id,
            input={"authorization_snapshot": snapshot},
            session_key=old.session_key,
            status="running",
        )
        session.add(second_run)
        session.flush()
        second_id = second_run.id
        session.commit()
    request = state.request.model_copy(update={"resource_id": second_id, "execution_id": second_id})
    second = asyncio.run(admission.prepare(request))
    assert second.reference_id != first.reference_id
    with state.sessions() as session:
        session.get(AgentRun, state.request.resource_id).status = "cancelled"
        session.commit()
    asyncio.run(admission.checkpoint(first))
    first_native.stop.assert_awaited_once()
    second_native.stop.assert_not_awaited()
    admission.authorize_native(request.workflow_id, second_native.state)
    with state.sessions() as session:
        assert session.get(SandboxReference, first.reference_id).status == "stopped"
        assert session.get(SandboxReference, second.reference_id).status == "running"
    client.delete.assert_not_awaited()
