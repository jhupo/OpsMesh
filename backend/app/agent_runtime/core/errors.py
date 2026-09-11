import re
from dataclasses import dataclass

from backend.app.agent_runtime.contracts import (
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeGuardrailResult,
)
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
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.event_type = event_type
        self.metadata = redact_sensitive_payload(metadata)
        self.retryable = retryable


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


class AgentRuntimeCancelledError(AgentRuntimePolicyError):
    def __init__(self) -> None:
        super().__init__(
            code="agent_runtime_cancelled",
            message="Agent runtime execution was cancelled",
            event_type="agent.run.cancelled",
            metadata={"propagated": True},
        )


class AgentRuntimeCapabilityError(AgentRuntimePolicyError):
    def __init__(
        self,
        capabilities: AgentRuntimeCapabilities,
        missing: tuple[AgentRuntimeCapability, ...],
    ) -> None:
        names = tuple(sorted(item.value for item in missing))
        super().__init__(
            code="agent_runtime_capability_unsupported",
            message=(
                f"Runtime adapter {capabilities.adapter} does not support: "
                + ", ".join(names)
            ),
            event_type="agent.capability.unsupported",
            metadata={
                "provider": capabilities.provider,
                "adapter": capabilities.adapter,
                "missing": list(names),
                "reasons": {
                    name: capabilities.unsupported_reasons.get(
                        name,
                        "The adapter does not advertise this capability.",
                    )
                    for name in names
                },
            },
        )


def normalize_agent_error(exc: Exception) -> NormalizedAgentError:
    if isinstance(exc, AgentRuntimePolicyError):
        return NormalizedAgentError(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
    message = str(exc) or "Agent runtime failed"
    message = _SENSITIVE_PROVIDER_CONFIG_PATTERN.sub("[redacted]", message)
    message = redact_sensitive_text(_SENSITIVE_URL_PATTERN.sub("[redacted]", message))
    return NormalizedAgentError(
        code=exc.__class__.__name__,
        message=message,
        retryable=True,
    )
