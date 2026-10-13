from datetime import UTC, datetime
from uuid import UUID

from opsmesh.runtime.instances.models import RuntimeEvent
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.shared.telemetry.request_context import current_evidence_context


def new_runtime_event(
    *,
    workspace_id: UUID,
    workspace_runtime_id: UUID,
    runtime_space_id: UUID | None,
    event_type: str,
    message: str,
    metadata: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> RuntimeEvent:
    evidence = current_evidence_context()
    return RuntimeEvent(
        workspace_id=workspace_id,
        workspace_runtime_id=workspace_runtime_id,
        runtime_space_id=runtime_space_id,
        event_type=event_type,
        message=redact_sensitive_text(message),
        request_id=evidence.get("request_id"),
        trace_id=evidence.get("trace_id"),
        span_id=evidence.get("span_id"),
        worker_id=evidence.get("worker_id"),
        runtime_id=str(workspace_runtime_id),
        event_metadata=redact_sensitive_payload(dict(metadata or {}) | evidence),
        created_at=created_at or datetime.now(UTC),
    )
