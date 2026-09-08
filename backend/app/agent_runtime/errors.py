import re
from dataclasses import dataclass

from backend.app.agent_runtime.contracts import AgentRuntimeGuardrailResult
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text

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
class NormalizedAgentError:
    code: str
    message: str
    retryable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class AgentRuntimePolicyError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        event_type: str,
        metadata: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.event_type = event_type
        self.metadata = redact_sensitive_payload(metadata)


class AgentRuntimeGuardrailBlockedError(AgentRuntimePolicyError):
    def __init__(self, result: AgentRuntimeGuardrailResult) -> None:
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


class AgentRuntimeOutputValidationError(AgentRuntimePolicyError):
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


def normalize_agent_error(exc: Exception) -> NormalizedAgentError:
    if isinstance(exc, AgentRuntimePolicyError):
        return NormalizedAgentError(
            code=exc.code,
            message=exc.message,
            retryable=False,
        )
    message = str(exc) or "Agent runtime failed"
    message = _SENSITIVE_PROVIDER_CONFIG_PATTERN.sub("[redacted]", message)
    message = redact_sensitive_text(_SENSITIVE_URL_PATTERN.sub("[redacted]", message))
    return NormalizedAgentError(
        code=exc.__class__.__name__,
        message=message,
        retryable=True,
    )
