"""JSON lifecycle facts emitted transactionally by the runtime."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def lifecycle_event(run_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "type": f"runtime.run.{kind}",
        "schema_version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": "runtime",
        "correlation_id": run_id,
        "subject": {"run_id": run_id},
        "payload": payload,
    }
