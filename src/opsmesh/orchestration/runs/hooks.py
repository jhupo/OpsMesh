"""Execution timing and durable Activity boundaries for frozen extension hooks."""

from contextlib import suppress
from typing import Any
from uuid import UUID

from temporalio import activity

from opsmesh.orchestration.contracts import ExecutionBindings
from opsmesh.orchestration.execution.hooks import HookOccurrence
from opsmesh.shared.errors import ConflictError


async def apply_hooks(bindings: ExecutionBindings, occurrence: HookOccurrence) -> dict[str, Any]:
    if bindings.extensions is None:
        raise ConflictError("Installed hook execution is not assembled")
    return await bindings.extensions.hooks(**occurrence.model_dump())


async def finish_hooks(
    bindings: ExecutionBindings,
    *,
    run_id: UUID,
    occurrence: str,
    entered: list[str],
    failed: bool,
) -> None:
    bridge = bindings.extensions
    if bridge is None or not entered:
        return
    if failed:
        with suppress(Exception):
            await bridge.hooks(
                run_id=run_id,
                occurrence=occurrence,
                stage="on_error",
                payload={"error_code": "operation_failed"},
                entered=entered,
            )
    try:
        await bridge.hooks(
            run_id=run_id,
            occurrence=occurrence,
            stage="finalize",
            payload={"status": "failed" if failed else "completed"},
            entered=entered,
        )
    except Exception:
        if not failed:
            raise


class HookActivities:
    def __init__(self, bindings: ExecutionBindings) -> None:
        self.bindings = bindings

    @activity.defn(name="orchestration.hooks")
    async def invoke(self, occurrence: HookOccurrence) -> dict[str, Any]:
        return await apply_hooks(self.bindings, occurrence)
