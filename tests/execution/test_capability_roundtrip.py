"""Frozen sandbox definitions survive their persisted JSON representation."""

import pytest

from opsmesh.agents.config.capabilities import SandboxCapabilityConfig
from opsmesh.execution.preparation import AgentSnapshot


@pytest.mark.parametrize("kind", ["filesystem", "shell", "memory", "compaction"])
def test_sandbox_snapshot_roundtrips_capability_defaults(kind):
    snapshot = AgentSnapshot.model_validate(
        {
            "root_id": "sandbox",
            "definitions": {
                "sandbox": {
                    "type": "sandbox",
                    "name": "Sandbox",
                    "capabilities": [{"type": kind}],
                }
            },
        }
    )
    restored = AgentSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot


def test_non_skills_capability_rejects_custom_skills_path():
    with pytest.raises(ValueError, match="skills_path belongs only"):
        SandboxCapabilityConfig(type="shell", skills_path="custom-skills")
