"""Versioned external data contracts and explicit model/output projections."""

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from opsmesh.capabilities.references.schema import validate_json_value
from opsmesh.orchestration.automations.contracts import (
    AutomationConfiguration,
    AutomationMessage,
)
from opsmesh.orchestration.automations.models import AutomationEvent
from opsmesh.orchestration.definitions.data import resolve_workflow_bindings
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.security.redaction import redact_sensitive_payload


def model_message(config: AutomationConfiguration, message: AutomationMessage) -> dict[str, object]:
    if message.contract_version != config.contract_version:
        raise ValueError("Message contract version does not match automation")
    if len(message.model_dump_json().encode()) > 64_000:
        raise ValueError("Message exceeds 64000 bytes")
    if message.action in {"start", "follow_up"}:
        validate_json_value(message.data, config.input_schema, label="message data")
    return {
        "event_id": message.event_id,
        "text": message.text,
        "attachments": [item.model_dump(mode="json") for item in message.attachments],
        "data": {
            key: message.data[key] for key in config.model_input_fields if key in message.data
        },
    }


def validate_follow_up(config: AutomationConfiguration, message: AutomationMessage) -> None:
    projected = model_message(config, message)
    data = projected["data"]
    size = len(message.text) + (1 + len(json.dumps(data, ensure_ascii=False)) if data else 0)
    if not (message.text.strip() or data) or size > 4000:
        raise ValueError("Projected follow-up must contain 1 to 4000 characters")


@dataclass(frozen=True)
class AutomationOutput:
    value: dict[str, object]
    error: str | None = None


def external_output(
    session: Session, event: AutomationEvent, task: Task | None
) -> AutomationOutput:
    if event.result_payload:
        return AutomationOutput(redact_sensitive_payload(event.result_payload))
    if task is None or task.status != "completed":
        return AutomationOutput({})
    config = AutomationConfiguration.model_validate(event.configuration)
    try:
        value = (
            resolve_workflow_bindings(session, task, {"output": config.output_binding})["output"]
            if config.output_binding is not None
            else task.final_output or {}
        )
        if not isinstance(value, dict):
            raise ValueError("External output must be an object")
        safe = redact_sensitive_payload(value)
        if len(json.dumps(safe, ensure_ascii=True)) > 32_000:
            raise ValueError("External output exceeds limit")
        validate_json_value(safe, config.output_schema, label="automation output")
        return AutomationOutput(safe)
    except ValueError:
        return AutomationOutput({}, "output_contract_rejected")
