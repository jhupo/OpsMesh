"""Per-run configuration is typed, immutable in storage, and stable across approvals."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from opsmesh.framework.agents import AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import (
    ModelSettingsOverrides,
    RunOverrides,
    SandboxRunOverrides,
    SubmitRunRequest,
    resolve_run_agent,
)
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from tests.runtime.test_runtime import ControlledBackend, create_runtime


def published():
    return AgentSpecification(
        id="published",
        name="Published",
        backend="controlled",
        workspace_id="original",
        model_settings={
            "temperature": 0.7,
            "retry": {"max_retries": 2, "backoff": {"initial_delay": 1.0, "max_delay": 20.0}},
            "extra_args": {"prompt_cache_key": "published"},
        },
    )


def test_run_can_disable_tools_without_changing_the_published_agent():
    source = published().model_copy(update={"tool_choice": "auto"})
    effective = resolve_run_agent(source, RunOverrides(tool_choice="none"))
    assert effective.tool_choice == "none" and effective.reset_tool_choice is False
    assert source.tool_choice == "auto" and source.reset_tool_choice is True
    for unsafe in ("auto", "required", "some_tool"):
        with pytest.raises(ValidationError):
            RunOverrides.model_validate({"tool_choice": unsafe})


async def test_runs_share_revision_but_not_overrides_or_mutable_configuration(tmp_path):
    runtime, db = await create_runtime(tmp_path, ControlledBackend())
    specification = published()
    original = specification.model_dump(mode="json")
    try:
        requests = [
            SubmitRunRequest(
                principal_id="user",
                session_id=f"session-{index}",
                agent=specification,
                input="hello",
                idempotency_key=f"run-{index}",
                overrides=RunOverrides(
                    workspace_id=f"workspace-{index}",
                    model_settings=ModelSettingsOverrides(
                        temperature=temperature,
                        retry={"backoff": {"max_delay": float(index + 3)}},
                    ),
                ),
            )
            for index, temperature in enumerate((0.1, 1.2))
        ]
        first, second = [await runtime.submit(request) for request in requests]
        assert specification.model_dump(mode="json") == original
        assert first.agent.ref == second.agent.ref == specification.ref
        assert first.published_agent_ref == specification.ref
        assert first.published_agent_fingerprint == second.published_agent_fingerprint
        assert first.effective_agent_fingerprint != second.effective_agent_fingerprint
        assert first.agent.workspace_id == "workspace-0"
        assert second.agent.workspace_id == "workspace-1"
        assert first.agent.model_settings["temperature"] == 0.1
        assert second.agent.model_settings["temperature"] == 1.2
        assert first.agent.model_settings["retry"] == {
            "max_retries": 2,
            "backoff": {"initial_delay": 1.0, "max_delay": 3.0},
        }
        first.agent.model_settings["retry"]["backoff"]["max_delay"] = 99
        specification.model_settings["temperature"] = 0.9
        assert (await runtime.get_run(first.id)).agent.model_settings["retry"]["backoff"][
            "max_delay"
        ] == 3.0
        assert (await runtime.get_run(second.id)).agent.model_settings["temperature"] == 1.2
        with pytest.raises(OpsMeshError, match="different input"):
            await runtime.submit(requests[0].model_copy(update={"overrides": RunOverrides()}))
    finally:
        await db.close()


async def test_approval_resume_uses_frozen_effective_settings_and_source(tmp_path):
    backend = ControlledBackend(pause=True)
    runtime, db = await create_runtime(tmp_path, backend)
    specification = published()
    try:
        run = await runtime.submit(
            SubmitRunRequest(
                principal_id="user",
                session_id="session",
                agent=specification,
                input="hello",
                idempotency_key="approve",
                overrides=RunOverrides(
                    workspace_id="run-workspace", model_settings={"temperature": 0.2}
                ),
            )
        )
        waiting = await runtime.execute(run.id)
        assert waiting.status == "waiting_approval"
        specification.model_settings["temperature"] = 1.8
        worker = PersistentAgentRuntime(db, runtime.extensions, runtime.context_factory)
        restored = await worker.get_run(run.id)
        assert restored.agent.workspace_id == "run-workspace"
        assert restored.agent.model_settings["temperature"] == 0.2
        assert restored.effective_agent_fingerprint == run.effective_agent_fingerprint
        decided = await worker.decide(run.id, "approve1", True, restored.revision)
        await worker.resume(run.id, decided.revision)
        completed = await worker.execute(run.id)
        assert completed.status == "completed"
        assert completed.overrides == run.overrides
        assert completed.agent == run.agent
    finally:
        await db.close()


async def test_modified_effective_configuration_is_rejected_before_execution(tmp_path):
    backend = ControlledBackend()
    runtime, db = await create_runtime(tmp_path, backend)
    try:
        run = await runtime.submit(
            SubmitRunRequest(
                principal_id="user",
                session_id="session",
                agent=published(),
                input="hello",
                idempotency_key="tamper",
                overrides=RunOverrides(model_settings={"temperature": 0.1}),
            )
        )
        record = await runtime.runs.get(run.id)
        changed = deepcopy(record.data)
        changed["agent"]["model_settings"]["temperature"] = 1.9
        await runtime.runs.update(run.id, changed, record.revision)
        failed = await runtime.execute(run.id)
        assert failed.status == "failed" and failed.error.code == "CHECKPOINT_INCOMPATIBLE"
        assert backend.calls == 0
    finally:
        await db.close()


def test_sandbox_source_selection_clears_old_sources_without_mutating_revision():
    agent = published().model_copy(
        update={"sandbox": SandboxConfiguration(snapshot_ref="old", snapshot_path="/trusted/old")}
    )
    workspace = resolve_run_agent(agent, RunOverrides(workspace_id="new"))
    assert workspace.sandbox.snapshot_ref is None and workspace.sandbox.snapshot_path is None
    snapshot = resolve_run_agent(
        agent, RunOverrides(sandbox=SandboxRunOverrides(snapshot_ref="new"))
    )
    assert snapshot.sandbox.snapshot_ref == "new" and snapshot.agent_tools == agent.agent_tools
    assert snapshot.workspace_id is None and snapshot.sandbox.snapshot_path is None
    resumed = resolve_run_agent(agent, RunOverrides(sandbox={"session_ref": "stored-session"}))
    assert resumed.sandbox.session_ref == "stored-session" and resumed.sandbox.snapshot_ref is None
    assert agent.workspace_id == "original" and agent.sandbox.snapshot_ref == "old"
    with pytest.raises(OpsMeshError, match="require a sandbox"):
        resolve_run_agent(published(), RunOverrides(sandbox={"snapshot_ref": "new"}))


@pytest.mark.parametrize(
    "payload",
    [
        {"model_settings": {"max_tokens": -1}},
        {"model_settings": {"temperature": float("nan")}},
        {"model_settings": {"retry": {"max_retries": 6}}},
        {"model_settings": {"retry": {"policy": "allow_unsafe_replay"}}},
        {"model_settings": {"extra_args": {"model": "different"}}},
        {"model_settings": {"extra_headers": {"Authorization": "secret"}}},
        {"model_settings": {"tool_choice": "none"}},
        {"sandbox": {"session_state": {"host": "/"}}},
        {"sandbox": {"snapshot_path": "C:/Users"}},
        {"sandbox": {"snapshot_ref": "one", "session_ref": "two"}},
        {"sandbox": {"snapshot_ref": "one"}, "workspace_id": "two"},
    ],
)
def test_invalid_or_unsafe_overrides_are_rejected(payload):
    with pytest.raises(ValidationError):
        RunOverrides.model_validate(payload)


def test_actual_sdk_settings_merge_profile_agent_and_run_without_losing_retry():
    pytest.importorskip("agents")
    from opsmesh.modules.runtime.internal.backends.openai_agents.models import resolve_settings

    profile = {
        "settings": {
            "retry": {"max_retries": 1, "backoff": {"jitter": False, "multiplier": 3}},
            "extra_args": {"service_tier": "default"},
        }
    }
    original = deepcopy(profile)
    effective = resolve_run_agent(
        published(),
        RunOverrides(
            model_settings={
                "retry": {"backoff": {"max_delay": 4}},
                "extra_args": {"service_tier": "flex"},
            }
        ),
    )
    settings = resolve_settings(effective, profile)
    assert settings.retry.max_retries == 2
    assert settings.retry.backoff.jitter is False
    assert settings.retry.backoff.multiplier == 3
    assert settings.retry.backoff.initial_delay == 1
    assert settings.retry.backoff.max_delay == 4
    assert settings.extra_args == {"service_tier": "flex", "prompt_cache_key": "published"}
    assert profile == original
    with pytest.raises(OpsMeshError) as error:
        resolve_settings(effective, {"settings": {"extra_args": {"temperature": 0.5}}})
    assert error.value.code == "MODEL_SETTINGS_CONFLICT"
    with pytest.raises(OpsMeshError) as error:
        resolve_run_agent(
            published(), RunOverrides(model_settings={"retry": {"backoff": {"max_delay": 0.1}}})
        )
    assert error.value.code == "MODEL_SETTINGS_CONFLICT"


async def test_actual_sdk_runner_receives_distinct_run_settings():
    pytest.importorskip("agents")
    from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
    from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
    from tests.runtime.test_openai_backend import ScriptedModel, context, message

    model = ScriptedModel([[message("one")], [message("two")]])
    backend = OpenAIAgentsBackend(
        RuntimeExtensionRegistry(), model_resolver=lambda spec, ctx: model
    )
    source = published().model_copy(update={"backend": "openai_agents"})
    for temperature in (0.1, 0.9):
        specification = resolve_run_agent(
            source, RunOverrides(model_settings={"temperature": temperature, "max_tokens": 123})
        )
        result = await backend.run(specification, "hello", context())
        assert result.output in {"one", "two"}
    settings = [entry[1]["model_settings"] for entry in model.inputs]
    assert [item.temperature for item in settings] == [0.1, 0.9]
    assert all(item.max_tokens == 123 and item.retry.max_retries == 2 for item in settings)
    assert source.model_settings["temperature"] == 0.7
