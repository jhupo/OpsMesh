"""Temporal restores the native exception caught by the SDK's Memory capability."""

from pathlib import Path

import pytest
from agents.sandbox.errors import WorkspaceReadNotFoundError
from temporalio.api.common.v1 import ActivityType, Payloads
from temporalio.api.failure.v1 import ActivityFailureInfo, ApplicationFailureInfo, Failure
from temporalio.converter import DefaultPayloadConverter
from temporalio.exceptions import ActivityError, ApplicationError

from opsmesh.workflows.engines.temporal.failures import (
    MISSING_WORKSPACE_FILE,
    WorkspaceFailureConverter,
)


@pytest.mark.parametrize("activity_type", ["opsmesh.instance-sandbox_session_read", "another.read"])
def test_missing_file_restoration_is_scoped_to_native_sandbox_reads(activity_type):
    converter = DefaultPayloadConverter()
    failure = Failure(
        message="Activity failed",
        activity_failure_info=ActivityFailureInfo(
            activity_type=ActivityType(name=activity_type), activity_id="read"
        ),
        cause=Failure(
            message="Missing file",
            application_failure_info=ApplicationFailureInfo(
                type=MISSING_WORKSPACE_FILE,
                non_retryable=True,
                details=Payloads(payloads=converter.to_payloads(["rollouts/first.jsonl"])),
            ),
        ),
    )
    error = WorkspaceFailureConverter().from_failure(failure, converter)
    if activity_type == "opsmesh.instance-sandbox_session_read":
        assert isinstance(error, WorkspaceReadNotFoundError)
        assert error.context["path"] == str(Path("rollouts/first.jsonl"))
        assert isinstance(error.cause, ActivityError)
    else:
        assert isinstance(error, ActivityError)


@pytest.mark.parametrize("wrapped", [False, True])
def test_native_missing_file_survives_provider_and_temporal_serialization(wrapped):
    payloads = DefaultPayloadConverter()
    converter = WorkspaceFailureConverter()
    missing = WorkspaceReadNotFoundError(path=Path("memory/memory_summary.md"))
    error = missing
    if wrapped:
        error = ApplicationError(str(missing), type=str(missing.error_code), non_retryable=True)
        error.__cause__ = missing
    cause = Failure()
    converter.to_failure(error, payloads, cause)
    assert cause.application_failure_info.type == MISSING_WORKSPACE_FILE
    assert cause.application_failure_info.non_retryable
    failure = Failure(
        message="Activity failed",
        activity_failure_info=ActivityFailureInfo(
            activity_type=ActivityType(name="opsmesh.instance-sandbox_session_read"),
            activity_id="read",
        ),
        cause=cause,
    )
    restored = converter.from_failure(failure, payloads)
    assert isinstance(restored, WorkspaceReadNotFoundError)
    assert restored.context["path"] == str(Path("memory/memory_summary.md"))


def test_untyped_missing_file_message_is_not_restored():
    payloads = DefaultPayloadConverter()
    converter = WorkspaceFailureConverter()
    error = ApplicationError("file not found: secret.txt", type="workspace_read_not_found")
    failure = Failure()
    converter.to_failure(error, payloads, failure)
    restored = converter.from_failure(failure, payloads)
    assert isinstance(restored, ApplicationError)
    assert restored.type == "workspace_read_not_found"
