import re
from dataclasses import dataclass

from backend.app.security.redaction import redact_sensitive_text

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


def normalize_agent_error(exc: Exception) -> NormalizedAgentError:
    message = str(exc) or "Agent runtime failed"
    message = _SENSITIVE_PROVIDER_CONFIG_PATTERN.sub("[redacted]", message)
    message = redact_sensitive_text(_SENSITIVE_URL_PATTERN.sub("[redacted]", message))
    return NormalizedAgentError(
        code=exc.__class__.__name__,
        message=message,
        retryable=True,
    )
