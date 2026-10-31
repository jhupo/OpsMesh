"""Configuration audit facts omit input content and credential references."""

from typing import Literal

from opsmesh.framework.typing import JsonData

from .api import TriggerDefinition, TriggerDelivery


def configuration_event(
    definition: TriggerDefinition, actor_id: str, action: Literal["created", "updated", "deleted"]
) -> JsonData:
    return {
        "type": f"triggers.definition.{action}",
        "source": "triggers",
        "actor_ref": actor_id,
        "correlation_id": definition.id,
        "payload": {
            "trigger_id": definition.id,
            "revision": definition.revision,
            "principal_id": definition.principal_id,
            "agent_revision": definition.agent_revision,
            "enabled": definition.enabled,
        },
    }


def delivery_event(delivery: TriggerDelivery, principal_id: str) -> JsonData:
    return {
        "type": f"triggers.delivery.{delivery.status}",
        "source": "triggers",
        "actor_ref": principal_id,
        "correlation_id": delivery.trigger_id,
        "payload": {
            "delivery_id": delivery.id,
            "trigger_revision": delivery.trigger_revision,
            "event_id": delivery.event_id,
            "run_id": delivery.run_id,
            "error_code": delivery.error.code if delivery.error else None,
        },
    }
