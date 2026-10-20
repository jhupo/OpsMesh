"""Namespace export and restore with current authorization and quota rules."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.extensions.models import (
    ExtensionInstance,
    ExtensionNamespace,
    ExtensionState,
    Installation,
    Invocation,
    ManagedDeployment,
)
from opsmesh.extensions.schemas import StateMutation, StateSnapshot
from opsmesh.extensions.state import (
    apply_mutations,
    cleanup_namespace,
    cleanup_preview,
    lock_namespace,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def export_state(session: Session, namespace_id: UUID) -> dict[str, object]:
    namespace = lock_namespace(session, namespace_id)
    instance = session.get(ExtensionInstance, namespace.instance_id)
    installation = session.get(Installation, instance.installation_id) if instance else None
    if instance is None or installation is None:
        raise ConflictError("The namespace installation is unavailable")
    if namespace.used_bytes > 16 * 1_048_576:
        raise InvalidArgumentError("The namespace exceeds the snapshot limit")
    rows = list(
        session.scalars(
            select(ExtensionState)
            .where(ExtensionState.namespace_id == namespace.id)
            .order_by(ExtensionState.key)
            .limit(10_001)
        )
    )
    if len(rows) > 10_000:
        raise InvalidArgumentError("Use paginated export for namespaces above 10000 entries")
    snapshot = {
        "installation_id": str(installation.id),
        "version_id": str(installation.version_id),
        "revision": namespace.revision,
        "entries": [{"key": row.key, "value": row.value} for row in rows],
    }
    validate_snapshot(snapshot)
    return snapshot


def restore_state(
    session: Session,
    namespace_id: UUID,
    snapshot: dict[str, object],
    expected_revision: int,
) -> int:
    validate_snapshot(snapshot)
    prior = session.get(ExtensionNamespace, namespace_id)
    if prior is None:
        raise ConflictError("The namespace is unavailable")
    instance = session.get(ExtensionInstance, prior.instance_id, with_for_update=True)
    lock_namespace(session, namespace_id)
    installation = session.get(Installation, instance.installation_id) if instance else None
    if (
        instance is None
        or installation is None
        or snapshot.get("version_id") != str(installation.version_id)
    ):
        raise ConflictError("Restore requires the snapshot's installed capability version")
    if snapshot.get("installation_id") != str(installation.id):
        raise ConflictError("The snapshot references a different installation")
    entries = snapshot.get("entries")
    if not isinstance(entries, list) or len(entries) > 10000:
        raise InvalidArgumentError("Invalid snapshot state entries")
    mutations: list[StateMutation] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), dict):
            raise InvalidArgumentError("Invalid snapshot state entry")
        value = entry["value"]
        mutations.append(
            StateMutation(
                key=entry["key"], value=value.get("value"), delete=value.get("deleted", False)
            )
        )
    if instance.status != "disabled":
        raise ConflictError("Disable the extension instance before restoring state")
    if (
        session.scalar(
            select(ManagedDeployment.id)
            .where(
                ManagedDeployment.instance_id == instance.id,
                ManagedDeployment.status != "stopped",
            )
            .limit(1)
        )
        is not None
    ):
        raise ConflictError("Stop the resident deployment before restoring state")
    if (
        session.scalar(
            select(Invocation.id)
            .where(
                Invocation.instance_id == instance.id,
                Invocation.status.in_(("started", "needs_attention")),
            )
            .limit(1)
        )
        is not None
    ):
        raise ConflictError("Settle active or uncertain invocations before restoring state")
    preview = cleanup_preview(session, namespace_id)
    cleanup_namespace(
        session,
        namespace_id,
        expected_revision=expected_revision,
        preview_digest=preview["preview_digest"],
    )
    apply_mutations(
        session,
        namespace_id=namespace_id,
        entries=mutations,
        limit_bytes=instance.state_limit_bytes,
    )
    return len(mutations)


def validate_snapshot(snapshot: dict[str, object]) -> None:
    try:
        StateSnapshot.model_validate(snapshot)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The namespace snapshot is invalid or exceeds its bounds"
        ) from exc
