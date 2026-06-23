from __future__ import annotations

from backend.app.runtimes.models import RuntimeCommand, RuntimeLease


def command_failure_metadata(
    record: RuntimeCommand,
    reason: str,
    error: str,
) -> dict[str, object]:
    return {
        "command_id": str(record.id),
        "command_status": record.status,
        "reason": reason,
        "error": error[:512],
    }


def bounded_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:2_000]


def lease_metadata(lease: RuntimeLease | None) -> dict[str, object]:
    return {"runtime_lease_id": str(lease.id)} if lease is not None else {}


def positive_int_limit(value: object, fallback: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return fallback


def bounded_text(value: str, max_bytes: int) -> tuple[str, bool, int]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False, len(encoded)
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True, len(encoded)
