from dataclasses import dataclass


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
    return NormalizedAgentError(
        code=exc.__class__.__name__,
        message=str(exc) or "Agent runtime failed",
        retryable=True,
    )

