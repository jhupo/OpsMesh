"""Freeze executable node configuration at admission, separate from mutable scheduling state."""

from copy import deepcopy

from opsmesh.orchestration.tasks.models import TaskStep

NODE_DEFINITION_FIELDS = (
    "node_type",
    "tool_name",
    "arguments",
    "input_bindings",
    "output_schema",
    "subworkflow_definition_id",
    "subworkflow_version",
)


def node_definition_snapshot(step: TaskStep) -> dict[str, object]:
    dependencies = step.dependencies
    if not isinstance(dependencies, dict):
        raise ValueError("Workflow node dependencies must be an object")
    return {
        key: deepcopy(dependencies[key]) for key in NODE_DEFINITION_FIELDS if key in dependencies
    }
