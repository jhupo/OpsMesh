"""Explicit reasoning effort survives public configuration into native Agent settings."""

import asyncio
from uuid import uuid4

import pytest
from opsmesh_sdk.api.orchestration import ModelConfiguration as SDKModelConfiguration
from opsmesh_sdk.host.models import ModelPrompt
from pydantic import ValidationError

from opsmesh.orchestration.execution.agents import build_agents
from opsmesh.orchestration.execution.contracts import (
    ExecutionRequest,
    ModelConfiguration,
    PreparedRun,
    RunReference,
)


@pytest.mark.parametrize(
    "effort", [None, "none", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_explicit_effort_builds_native_agent(effort):
    model = SDKModelConfiguration(connection_id=uuid4(), name="test", reasoning_effort=effort)
    request = ExecutionRequest.model_validate(
        {
            "session_id": uuid4(),
            "input": "hello",
            "entry_agent": "main",
            "agents": {"main": {"name": "Main", "model": model.model_dump(mode="json")}},
        }
    )
    run_id = uuid4()
    prepared = PreparedRun(
        reference=RunReference(execution_id=run_id, run_id=run_id, workflow_id=f"run:{run_id}"),
        principal_id=uuid4(),
        request=request,
    )
    tasks: set[asyncio.Task] = set()
    native = build_agents(prepared, tasks)
    assert native.model_settings.temperature is None
    if effort is None:
        assert native.model_settings.reasoning is None
    else:
        assert native.model_settings.reasoning.effort == effort


def test_unknown_effort_is_rejected_at_server_and_host_boundaries():
    with pytest.raises(ValidationError):
        ModelConfiguration(connection_id=uuid4(), name="test", reasoning_effort="invented")
    with pytest.raises(ValidationError):
        ModelPrompt(blocks=(), reasoning_effort="invented")
