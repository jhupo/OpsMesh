"""Publish bounded namespaced facts; subscriptions belong to orchestration."""

from typing import Any

from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.extensions.models import ExtensionEvent, Invocation
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.serialization import canonical_json, payload_digest


def publish_event(
    session: Session,
    *,
    invocation: Invocation,
    manifest: PackageManifest,
    operation_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> ExtensionEvent:
    if event_type not in manifest.published_events:
        raise InvalidArgumentError("The package does not declare this published event")
    if not operation_id or len(operation_id) > 240 or len(canonical_json(payload).encode()) > 65536:
        raise InvalidArgumentError("Event exceeds its operation or payload bound")
    fingerprint = payload_digest({"event_type": event_type, "payload": payload})
    existing = session.scalar(
        select(ExtensionEvent).where(
            ExtensionEvent.invocation_id == invocation.id,
            ExtensionEvent.operation_id == operation_id,
        )
    )
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise ConflictError("Event operation id was reused with different content")
        return existing
    event = ExtensionEvent(
        invocation_id=invocation.id,
        operation_id=operation_id,
        event_type=f"{manifest.package_name}.{event_type}",
        payload=payload,
        fingerprint=fingerprint,
    )
    session.add(event)
    session.flush()
    return event
