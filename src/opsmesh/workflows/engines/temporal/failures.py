"""Preserve the native SDK's missing-file contract across the Activity boundary."""

from pathlib import Path

from agents.sandbox.errors import WorkspaceReadNotFoundError
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DefaultFailureConverterWithEncodedAttributes, PayloadConverter
from temporalio.exceptions import ActivityError, ApplicationError

MISSING_WORKSPACE_FILE = "opsmesh.workspace_read_not_found"


class WorkspaceFailureConverter(DefaultFailureConverterWithEncodedAttributes):
    def to_failure(
        self, exception: BaseException, payload_converter: PayloadConverter, failure: Failure
    ) -> None:
        missing: BaseException | None = exception
        if isinstance(exception, ApplicationError):
            missing = exception.__cause__
        if isinstance(missing, WorkspaceReadNotFoundError):
            # The native sandbox provider wraps terminal SDK errors before outer
            # interceptors see them. Preserve the typed path at serialization.
            exception = ApplicationError(
                "Workspace file does not exist",
                str(missing.context["path"]),
                type=MISSING_WORKSPACE_FILE,
                non_retryable=True,
            )
        super().to_failure(exception, payload_converter, failure)

    def from_failure(self, failure: Failure, payload_converter: PayloadConverter) -> BaseException:
        error = super().from_failure(failure, payload_converter)
        if (
            isinstance(error, ActivityError)
            and error.activity_type == "opsmesh.instance-sandbox_session_read"
            and isinstance(error.__cause__, ApplicationError)
            and error.__cause__.type == MISSING_WORKSPACE_FILE
        ):
            details = error.__cause__.details
            if len(details) == 1 and isinstance(details[0], str):
                return WorkspaceReadNotFoundError(path=Path(details[0]), cause=error)
        return error
