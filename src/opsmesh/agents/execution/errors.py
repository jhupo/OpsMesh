import re
from dataclasses import dataclass

from opsmesh.agents.execution.contracts import AgentGuardrailResult
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text

_SENSITIVE_URL_PATTERN = re.compile(
    r"https?://[^\s,'\";}]+",
    re.IGNORECASE,
)
_SENSITIVE_PROVIDER_CONFIG_PATTERN = re.compile(
    r"\b(?:base[_ -]?url|endpoint[_ -]?url|remote[_ -]?url)\s*[:=]\s*"
    r"['\"]?[^\s,'\";}]+['\"]?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AgentFailureDetails:
    code: str
    message: str
    retryable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class AgentExecutionPolicyError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        event_type: str,
        metadata: dict[str, object],
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.event_type = event_type
        self.metadata = redact_sensitive_payload(metadata)
        self.retryable = retryable


class AgentModelProviderError(RuntimeError):
    """A vendor SDK failed at the model boundary, with a redacted diagnostic."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> None:
        safe_message = _safe_error_message(message)
        super().__init__(safe_message)
        self.code = code
        self.message = safe_message
        self.retryable = retryable
        self.metadata = redact_sensitive_payload(metadata or {})


class AgentGuardrailBlockedError(AgentExecutionPolicyError):
    def __init__(self, result: AgentGuardrailResult) -> None:
        super().__init__(
            code="agent_guardrail_blocked",
            message=f"{result.stage.capitalize()} guardrail {result.name} blocked the run",
            event_type="agent.guardrail.blocked",
            metadata={
                "stage": result.stage,
                "guardrail_name": result.name,
                "guardrail_kind": result.kind,
                "blocking": result.blocking,
                "evidence": result.evidence,
            },
        )


class AgentOutputValidationError(AgentExecutionPolicyError):
    def __init__(
        self,
        *,
        schema_name: str,
        schema_version: str | None,
        validator: str,
    ) -> None:
        super().__init__(
            code="agent_output_validation_failed",
            message=f"Agent output failed schema {schema_name} validation",
            event_type="agent.output.invalid",
            metadata={
                "schema_name": schema_name,
                "schema_version": schema_version,
                "validator": validator,
            },
        )


class AgentExecutionCancelledError(AgentExecutionPolicyError):
    def __init__(self) -> None:
        super().__init__(
            code="agent_runtime_cancelled",
            message="Agent runtime execution was cancelled",
            event_type="agent.run.cancelled",
            metadata={"propagated": True},
        )


def normalize_agent_error(exc: Exception) -> AgentFailureDetails:
    if isinstance(exc, AgentExecutionPolicyError):
        return AgentFailureDetails(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
    if isinstance(exc, AgentModelProviderError):
        return AgentFailureDetails(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
    return AgentFailureDetails(
        code=exc.__class__.__name__,
        message=_safe_error_message(str(exc)),
        retryable=False,
    )


def _safe_error_message(message: str) -> str:
    safe_message = message or "Agent runtime failed"
    safe_message = _SENSITIVE_PROVIDER_CONFIG_PATTERN.sub("[redacted]", safe_message)
    return redact_sensitive_text(_SENSITIVE_URL_PATTERN.sub("[redacted]", safe_message))
