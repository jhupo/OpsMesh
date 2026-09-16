"""Resolve bounded, workspace-scoped data references between workflow nodes."""

from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload_item
from backend.app.core.utils import walk_mapping
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowDataBinding

MAX_BINDING_BYTES = 16 * 1024
MAX_TOTAL_BINDING_BYTES = 64 * 1024
_MISSING = object()


class WorkflowDataBindingError(ValueError):
    """Raised when a required workflow data reference cannot be resolved."""


def resolve_workflow_inputs(
    session: Session,
    task: Task,
    step: TaskStep,
) -> dict[str, object]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    raw_bindings = dependencies.get("input_bindings", {})
    if raw_bindings in (None, {}):
        return {}
    if not isinstance(raw_bindings, dict):
        raise WorkflowDataBindingError("Workflow input_bindings must be an object")
    try:
        bindings = {
            str(name): WorkflowDataBinding.model_validate(value)
            for name, value in raw_bindings.items()
        }
    except ValidationError as exc:
        raise WorkflowDataBindingError("Workflow input binding is invalid") from exc

    steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == task.workspace_id,
            TaskStep.task_id == task.id,
        )
    ).all()
    by_package = {
        str(candidate.work_package_id): candidate
        for candidate in steps
        if candidate.work_package_id
    }
    by_id = {str(candidate.id): candidate for candidate in steps}
    resolved: dict[str, object] = {}
    total_size = 0
    for name, binding in bindings.items():
        value = _resolve_reference(binding.reference, task, by_package, by_id)
        if value is _MISSING:
            if binding.required:
                raise WorkflowDataBindingError(
                    f"Required workflow input '{name}' is unavailable: {binding.reference}"
                )
            value = binding.default
        safe_value = redact_sensitive_payload_item(value)
        size = _encoded_size(safe_value)
        if size > MAX_BINDING_BYTES:
            raise WorkflowDataBindingError(
                f"Workflow input '{name}' exceeds the {MAX_BINDING_BYTES}-byte limit"
            )
        total_size += size
        if total_size > MAX_TOTAL_BINDING_BYTES:
            raise WorkflowDataBindingError(
                f"Workflow inputs exceed the {MAX_TOTAL_BINDING_BYTES}-byte limit"
            )
        resolved[name] = safe_value
    return resolved


def _resolve_reference(
    reference: str,
    task: Task,
    by_package: dict[str, TaskStep],
    by_id: dict[str, TaskStep],
) -> object:
    parts = reference.split(".")
    if parts[0] == "task":
        roots = {
            "input": task.input,
            "generic_state": task.generic_state,
            "domain_state": task.domain_state,
            "final_output": task.final_output,
        }
        return (
            walk_mapping(roots.get(parts[1], _MISSING), parts[2:], missing=_MISSING)
            if len(parts) > 1
            else _MISSING
        )
    if parts[0] != "steps":
        return _MISSING
    if "output" in parts[1:]:
        marker_index = parts.index("output", 1)
        step_key = ".".join(parts[1:marker_index])
        step = by_package.get(step_key) or by_id.get(step_key)
        if step is None or step.status != "completed" or step.result_payload is None:
            return _MISSING
        return walk_mapping(step.result_payload, parts[marker_index + 1 :], missing=_MISSING)
    if parts[-1] == "result_summary":
        step_key = ".".join(parts[1:-1])
        step = by_package.get(step_key) or by_id.get(step_key)
        if step is None or step.status != "completed":
            return _MISSING
        return step.result_summary if step.result_summary is not None else _MISSING
    return _MISSING


def _encoded_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode())


__all__ = ["WorkflowDataBindingError", "resolve_workflow_inputs"]
