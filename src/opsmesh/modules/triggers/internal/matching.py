"""Bounded field selection with no executable templates or expression language."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.modules.triggers.api import TriggerDefinition, TriggerInvocation

MISSING = object()


def delivery_identity(trigger_id: str, event: OpsMeshEvent) -> str:
    return hashlib.sha256(f"{trigger_id}:{event.source}:{event.id}".encode()).hexdigest()


def select_field(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for name in path.split("."):
        if not isinstance(current, dict) or name not in current:
            return MISSING
        current = current[name]
    return current


def matches(definition: TriggerDefinition, event: OpsMeshEvent) -> bool:
    if definition.source != event.source or definition.event_type != event.type:
        return False
    document = event.model_dump(mode="json")
    return all(select_field(document, path) == value for path, value in definition.filters.items())


def event_fingerprint(event: OpsMeshEvent) -> str:
    # Transport timestamps may change on an authenticated retry of the same delivery.
    data = {"id": event.id, "source": event.source, "type": event.type, "payload": event.payload}
    try:
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise OpsMeshError("VALIDATION_ERROR", "Trigger event must be portable JSON") from exc
    if len(raw) > 65536:
        raise OpsMeshError("INPUT_TOO_LARGE", "Trigger event exceeds 64 KiB")
    return hashlib.sha256(raw).hexdigest()


def build_invocation(
    definition: TriggerDefinition, event: OpsMeshEvent, delivery_id: str
) -> TriggerInvocation:
    content = definition.input
    if content is None:
        payload: dict[str, Any] = event.payload
        if definition.input_mapping:
            document = event.model_dump(mode="json")
            payload = {}
            for name, path in definition.input_mapping.items():
                value = select_field(document, path)
                if value is MISSING:
                    raise OpsMeshError("VALIDATION_ERROR", "Trigger input mapping field is missing")
                payload[name] = value
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return TriggerInvocation(
        trigger_id=definition.id,
        trigger_revision=definition.revision,
        delivery_id=delivery_id,
        event_id=event.id,
        principal_id=definition.principal_id,
        agent_revision=definition.agent_revision,
        input=content,
        run_overrides=definition.run_overrides,
        session_policy=definition.session_policy,
        session_id=definition.session_id,
        reply_target=definition.reply_target,
        notify_on_failure=definition.notify_on_failure,
        notify_on_approval=definition.notify_on_approval,
        idempotency_key=f"trigger:{delivery_id}",
    )
