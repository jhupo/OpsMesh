"""Managed memory uses distinct metered runs and does not alias namespace-local IDs."""

import json

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentRunCompleted,
    ModelUsageObservation,
)
from opsmesh.modules.memory.api import MemoryNamespace, MemoryPolicy, MemorySource
from opsmesh.modules.models.api import ModelProfile


class MemoryTestBackend:
    id = "openai_agents"
    version = "test"
    features = AgentBackendCapabilities(structured_output=True)
    calls: list[str]

    def __init__(self, calls):
        self.calls = calls

    async def run(self, spec, input, context, **options):
        self.calls.append(context.run_id)
        await context.services.observe_usage(
            {
                "call_id": "response",
                "model_profile_ref": "memory@1",
                "input_tokens": 2,
                "output_tokens": 2,
            }
        )
        output = {key: str(input) for key in spec.output_schema["required"]}
        return AgentRunCompleted(
            output=output,
            history=({"role": "assistant", "content": json.dumps(output)},),
            usage=ModelUsageObservation(
                requests=1, input_tokens=2, output_tokens=2, total_tokens=4
            ),
        )


@pytest.mark.asyncio
async def test_equal_source_ids_in_different_namespaces_do_not_reuse_model_runs(tmp_path):
    app = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    calls = []
    # Replace only the test provider boundary; all catalogs, workers and storage remain real.
    app.services.extensions._backends["openai_agents"] = lambda: MemoryTestBackend(calls)
    await app.initialize(admin_username="admin", admin_password="test-passphrase")
    user = (await app.services.accounts.list_users())[0]
    await app.services.models.create(ModelProfile(id="memory", model="offline-test"))
    policy = MemoryPolicy(extract_model_profile="memory@1", consolidate_model_profile="memory@1")
    try:
        for namespace, text in (("alpha", "Apples"), ("beta", "Oranges")):
            await app.services.memory.create_namespace(
                MemoryNamespace(id=namespace, owner=user.id, policy=policy)
            )
            await app.services.memory.append_source(
                MemorySource(
                    id="same",
                    namespace_id=namespace,
                    conversation_id="conversation",
                    run_id="source-run",
                    text=text,
                ),
                principal_id=user.id,
            )
            job = await app.services.memory.request_generation(
                namespace, principal_id=user.id, idempotency_key="generate"
            )
            outcome = await app.services.memory.run_job(job.id)
            assert outcome.status == "succeeded", outcome
            assert (await app.services.memory.run_job(job.id)).status == "succeeded"
        alpha = await app.services.memory.read("alpha", principal_id=user.id)
        beta = await app.services.memory.read("beta", principal_id=user.id)
        assert "Apples" in alpha.summary and "Oranges" not in alpha.summary
        assert "Oranges" in beta.summary and "Apples" not in beta.summary
        assert len(set(calls)) == len(calls) == 4
        costs = await app.services.usage.costs(user.id)
        assert len(costs) == 4 and all(cost.status == "unpriced" for cost in costs)
    finally:
        await app.close()
