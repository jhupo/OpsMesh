"""Exercise the composition boundary with real authorization, catalogs and usage ledgers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from opsmesh.framework.agents import (
    AgentRunCompleted,
    AgentSpecification,
    AgentToolBinding,
    CodeInterpreterBinding,
    FileSearchBinding,
    HandoffBinding,
    ModelUsageObservation,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.memory.api import MemoryNamespace
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.runtime.api import SubmitRunRequest
from opsmesh.modules.usage.api import BudgetLimit, PriceVersion

from .test_application import configured_application


async def queued_run(app, user, session):
    return await app.submit_run(
        principal_id=user.id,
        session_id=session.id,
        agent_ref=session.agent_ref,
        input="hello",
        idempotency_key="bindings",
    )


async def test_reviewed_memory_policy_covers_submission_and_persisted_execution(
    tmp_path, monkeypatch
):
    app, user, _ = await configured_application(tmp_path)
    try:
        await app.services.memory.create_namespace(MemoryNamespace(id="reviewed", owner=user.id))
        original = await app.services.agents.get_revision("helper@1")
        definition = await app.services.agents.create(
            original.model_copy(
                update={
                    "id": "unsafe-capture",
                    "memory_namespace_id": "reviewed",
                    "memory_generate": True,
                }
            ),
            user.id,
        )
        await app.services.agents.publish(definition.id, definition.revision)
        spec = await app.services.agents.get_revision("unsafe-capture@1")
        session = await app.services.sessions.open(user.id, spec.ref)
        reserve, capture = AsyncMock(), AsyncMock()
        monkeypatch.setattr(app.services.run_usage, "reserve", reserve)
        monkeypatch.setattr(app.services.memory, "append_source", capture)
        metadata = {"conversation_operation": "chat"}
        with pytest.raises(OpsMeshError) as error:
            await app.submit_run(
                principal_id=user.id,
                session_id=session.id,
                agent_ref=spec.ref,
                input="reviewed input",
                idempotency_key="blocked-submission",
                metadata=metadata,
            )
        assert error.value.code == "CONFIG_INVALID"
        assert await app.services.runtime.list_runs() == []

        # A durable Run submitted before an upgrade must meet the same policy on execution.
        queued = await app.services.runtime.submit(
            SubmitRunRequest(
                principal_id=user.id,
                session_id=session.id,
                agent=spec,
                input="reviewed input",
                idempotency_key="persisted-submission",
                metadata=metadata,
            )
        )
        blocked = await app.services.runtime.execute(queued.id)
        assert blocked.status == "failed"
        assert blocked.error.code == "CONFIG_INVALID"
        reserve.assert_not_awaited()
        capture.assert_not_awaited()
        assert await app.services.memory.list_jobs("reviewed", principal_id=user.id) == ()
    finally:
        await app.close()


async def test_hosted_tools_require_resource_grants_and_recheck_revocation(tmp_path):
    app, _, _ = await configured_application(tmp_path)
    context = None
    try:
        await app.services.accounts.create_role(
            AccessRole(
                name="hosted",
                grants=(
                    PermissionGrant(action="agent:run", resource="helper@1"),
                    PermissionGrant(action="hosted_tool:use"),
                    PermissionGrant(action="vector_store:use", resource="vs_allowed"),
                    PermissionGrant(action="provider_file:use", resource="file_allowed"),
                    PermissionGrant(action="hosted_container:use", resource="container_allowed"),
                ),
            )
        )
        user = await app.services.accounts.create_user(
            "operator", "test-passphrase", roles=("hosted",)
        )
        session = await app.services.sessions.open(user.id, "helper@1")
        context = await app.services.agent_bridge.context(await queued_run(app, user, session))
        authorize = context.services.authorize_hosted_tool
        await authorize(FileSearchBinding(vector_store_ids=("vs_allowed",)))
        await authorize(
            CodeInterpreterBinding(file_ids=("file_allowed",), container_id="container_allowed")
        )
        for denied in (
            FileSearchBinding(vector_store_ids=("vs_allowed", "vs_other")),
            CodeInterpreterBinding(file_ids=("file_other",)),
            CodeInterpreterBinding(container_id="container_other"),
        ):
            with pytest.raises(OpsMeshError) as error:
                await authorize(denied)
            assert error.value.code == "FORBIDDEN"
        await app.services.accounts.update_user(user.id, roles=())
        with pytest.raises(OpsMeshError) as error:
            await authorize(FileSearchBinding(vector_store_ids=("vs_allowed",)))
        assert error.value.code == "FORBIDDEN"
    finally:
        if context is not None:
            await context.resources.cleanup_resources()
        await app.close()


@pytest.mark.parametrize(
    "extension",
    [
        "prompt",
        "tool_enabled",
        "handoff_enabled",
        "handoff_filter",
        "handoff_callback",
        "agent_tool_enabled",
        "output_extractor",
    ],
)
async def test_missing_dynamic_extension_blocks_publication(tmp_path, extension):
    app, user, _ = await configured_application(tmp_path)
    try:
        base = await app.services.agents.get_revision("helper@1")
        changes = {"id": "extended"}
        if extension == "prompt":
            changes["prompt_ref"] = "not.deployed@1"
        elif extension == "tool_enabled":
            changes["tools"] = (base.tools[0].model_copy(update={"enabled_ref": "not.deployed@1"}),)
        elif extension.startswith("handoff"):
            field = {
                "handoff_enabled": "enabled_ref",
                "handoff_filter": "input_filter_ref",
                "handoff_callback": "callback_ref",
            }[extension]
            changes["handoffs"] = (
                HandoffBinding(agent_ref="helper@1", **{field: "not.deployed@1"}),
            )
        else:
            field = "enabled_ref" if extension == "agent_tool_enabled" else "output_extractor_ref"
            changes["agent_tools"] = (
                AgentToolBinding(
                    agent_ref="helper@1", name="delegate", **{field: "not.deployed@1"}
                ),
            )
        definition = await app.services.agents.create(base.model_copy(update=changes), user.id)
        with pytest.raises(OpsMeshError) as error:
            await app.services.agents.publish(definition.id, definition.revision)
        assert error.value.code == "EXTENSION_NOT_FOUND"
        assert (await app.services.agents.get(definition.id)).published_revision is None
    finally:
        await app.close()


async def test_usage_observation_blocks_next_model_request_after_budget_exhaustion(tmp_path):
    app, user, session = await configured_application(tmp_path)
    context = None
    try:
        await app.services.models.create(ModelProfile(id="metered", model="offline"))
        await app.services.usage.publish_price(
            PriceVersion(
                id="price",
                model_profile="metered@1",
                currency="USD",
                input_per_million=Decimal("100000"),
                output_per_million=Decimal("0"),
                effective_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await app.services.usage.set_budget(
            BudgetLimit(principal_id=user.id, currency="USD", amount=Decimal("0.10"))
        )
        run = await queued_run(app, user, session)
        context = await app.services.agent_bridge.context(run)
        request = context.services.authorize_model_request
        await request("metered@1")
        observation = {
            "call_id": "provider-response-1",
            "model_profile_ref": "metered@1",
            "input_tokens": 1,
        }
        await context.services.observe_usage(observation)
        await context.services.observe_usage(observation)
        assert (await app.services.usage.budget(user.id, "USD")).spent == Decimal("0.10")
        with pytest.raises(OpsMeshError) as error:
            await request("metered@1")
        assert error.value.code == "BUDGET_EXCEEDED"
    finally:
        if context is not None:
            await context.resources.cleanup_resources()
        await app.close()


async def test_usage_unavailable_never_becomes_known_aggregate_cost(tmp_path):
    app, user, session = await configured_application(tmp_path)
    try:
        run = await queued_run(app, user, session)
        await app.services.run_finalizer.finalize(
            run,
            AgentRunCompleted(
                output="known result, unknown usage",
                usage=ModelUsageObservation(requests=1, input_tokens=10, usage_available=False),
            ),
        )
        costs = await app.services.usage.costs(user.id, run.id)
        assert len(costs) == 1
        assert costs[0].status == "unavailable" and costs[0].amount is None
    finally:
        await app.close()


async def test_withdrawing_model_blocks_next_request_in_existing_context(tmp_path):
    app, user, session = await configured_application(tmp_path)
    context = None
    try:
        await app.services.models.create(ModelProfile(id="withdrawn", model="offline"))
        run = await queued_run(app, user, session)
        context = await app.services.agent_bridge.context(run)
        request = context.services.authorize_model_request
        await request("withdrawn@1")
        await app.services.models.availability.set_enabled(
            "withdrawn@1", False, actor_id=user.id, expected_revision=0
        )
        with pytest.raises(OpsMeshError) as failure:
            await request("withdrawn@1")
        assert failure.value.code == "RESOURCE_DISABLED"
    finally:
        if context is not None:
            await context.resources.cleanup_resources()
        await app.close()


async def test_final_outcome_recovery_does_not_reprepare_deleted_memory_or_reserve(
    tmp_path, monkeypatch
):
    app, user, _ = await configured_application(tmp_path)
    try:
        await app.services.memory.create_namespace(MemoryNamespace(id="forgotten", owner=user.id))
        specification = AgentSpecification(
            id="remembering",
            name="Remembering",
            backend="application_test",
            memory_namespace_id="forgotten",
            memory_generate=False,
        )
        definition = await app.services.agents.create(specification, user.id)
        await app.services.agents.publish(definition.id, definition.revision)
        session = await app.services.sessions.open(user.id, "remembering@1")
        run = await queued_run(app, user, session)
        await app.services.memory.forget("forgotten", principal_id=user.id)
        await app.services.usage.set_budget(
            BudgetLimit(principal_id=user.id, currency="USD", amount=Decimal("0"))
        )
        record = await app.services.runtime.runs.get(run.id)
        outcome = AgentRunCompleted(output="durable result")
        await app.services.runtime.runs.update(
            record.id,
            {**record.data, "final_outcome": outcome.model_dump(mode="json")},
            record.revision,
        )

        async def no_resources(*args, **kwargs):
            pytest.fail("Finalization recovery must not prepare inference resources")

        monkeypatch.setattr(app.services.resources, "prepare", no_resources)
        completed = await app.services.runtime.execute(run.id)
        assert completed.status == "completed", completed.error
        assert completed.output == "durable result"
        assert (await app.services.sessions.history(session.id))[-1].text == "durable result"
    finally:
        await app.close()
