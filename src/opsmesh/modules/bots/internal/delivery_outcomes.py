"""Classify durable outcomes without equating a retry hint with a safe message replay."""

from typing import NoReturn

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore

_LOCAL_REJECTIONS = frozenset(
    {
        "INVALID_REQUEST",
        "UNSUPPORTED_FEATURE",
        "CONFIG_INVALID",
        "BOT_DISABLED",
        "PLUGIN_DISABLED",
        "BOT_ADAPTER_UNAVAILABLE",
        "FORBIDDEN",
        "UNAUTHORIZED",
    }
)
_PROVIDER_REJECTIONS = frozenset(
    {"BOT_REQUEST_REJECTED", "BOT_AUTHENTICATION_FAILED", "BOT_API_FAILED"}
)
_SAFE_TRANSIENT_ERRORS = frozenset({"BOT_UNAVAILABLE", "BOT_RATE_LIMITED", "BOT_API_FAILED"})


def _failure_state(error: BaseException) -> str:
    if not isinstance(error, OpsMeshError):
        return "unknown"
    if error.code in _LOCAL_REJECTIONS:
        return "not_attempted"
    if error.retryable and error.code in _SAFE_TRANSIENT_ERRORS:
        return "retryable_failed"
    if error.code in _PROVIDER_REJECTIONS:
        return "rejected"
    # Cancellation, malformed receipts and arbitrary retryable adapter errors cannot
    # establish whether the external side effect happened.
    return "unknown"


class BotDeliveryOutcomes:
    def __init__(self, store: VersionedRecordStore) -> None:
        self.store = store

    async def record_failure(self, record: StoredRecord, error: BaseException) -> None:
        await self.store.update(
            record.id,
            {
                **record.data,
                "state": _failure_state(error),
                "error_code": error.code if isinstance(error, OpsMeshError) else "DELIVERY_UNKNOWN",
            },
            record.revision,
        )

    @staticmethod
    def raise_previous_failure(record: StoredRecord) -> NoReturn:
        state, code = record.data.get("state"), record.data.get("error_code")
        if state == "not_attempted" and code in _LOCAL_REJECTIONS:
            raise OpsMeshError(code, "The previous delivery was not attempted")
        if state == "rejected" and code in _PROVIDER_REJECTIONS:
            raise OpsMeshError(code, "The previous delivery was rejected by the provider")
        raise OpsMeshError(
            "DELIVERY_UNKNOWN", "Delivery is in progress or its outcome is uncertain"
        )

    @staticmethod
    def raise_current_failure(error: BaseException) -> NoReturn:
        if (
            isinstance(error, OpsMeshError)
            and error.retryable
            and _failure_state(error) == "unknown"
        ):
            raise OpsMeshError("DELIVERY_UNKNOWN", "Bot delivery outcome is uncertain") from error
        raise error
