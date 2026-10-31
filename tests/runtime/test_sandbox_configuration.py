"""Native background work must retain platform admission and durable artifacts."""

import pytest

from opsmesh.framework.agents import AgentSpecification, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError


@pytest.mark.parametrize("options", [{"type": "memory"}, {"type": "memory", "generate": {}}])
def test_native_generation_cannot_duplicate_managed_generation(options):
    with pytest.raises(ValueError, match="cannot both generate"):
        AgentSpecification(
            id="a",
            name="A",
            memory_generate=True,
            sandbox=SandboxConfiguration(provider="docker", capability_options=(options,)),
        )


def test_native_read_only_memory_can_coexist_with_managed_generation():
    specification = AgentSpecification(
        id="assistant",
        name="Assistant",
        memory_generate=True,
        sandbox=SandboxConfiguration(
            provider="docker", capability_options=({"type": "memory", "generate": None},)
        ),
    )
    assert specification.sandbox.capability_options[0]["generate"] is None


def test_mutated_native_memory_options_are_rechecked_by_backend():
    from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox import capabilities

    configuration = SandboxConfiguration(capability_options=({"type": "memory", "generate": None},))
    configuration.capability_options[0].pop("generate")
    with pytest.raises(OpsMeshError) as error:
        capabilities(configuration)
    assert error.value.code == "DEPENDENCY_UNAVAILABLE"
