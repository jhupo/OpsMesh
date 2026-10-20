"""Generic hook ordering and typed, stage-specific patch policy."""

from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from typing import Any

from opsmesh_sdk.extensions.hooks import Block, HookDecision, Patch
from pydantic import TypeAdapter

from opsmesh.shared.errors import ForbiddenError

DECISION: TypeAdapter[HookDecision] = TypeAdapter(HookDecision)
PATCH_FIELDS: dict[str, frozenset[str]] = {
    "context.prepare": frozenset({"context_blocks"}),
    "model.before": frozenset({"input", "temperature"}),
    "model.after": frozenset({"output"}),
    "tool.before": frozenset({"arguments"}),
    "tool.after": frozenset({"result"}),
}


async def apply_chain(
    *,
    stage: str,
    occurrence: str,
    payload: dict[str, Any],
    bindings: list[dict[str, Any]],
    invoke: Callable[[dict[str, Any], dict[str, Any], str], Awaitable[dict[str, Any]]],
    entered: list[str] | None = None,
) -> dict[str, Any]:
    ordered = sorted(bindings, key=lambda value: (value["order"], value["binding_id"]))
    if stage in {"model.after", "tool.after", "on_error", "finalize"}:
        ordered.reverse()
    active = [] if entered is None else list(entered)
    effective = dict(payload)
    for binding in ordered:
        if stage not in binding["stages"]:
            continue
        if stage in {"on_error", "finalize"} and binding["binding_id"] not in active:
            continue
        if stage in {"model.after", "tool.after"} and binding["binding_id"] not in active:
            # After-only hooks enter at their own boundary. Paired hooks unwind
            # only if their corresponding before stage was entered.
            before = "model.before" if stage == "model.after" else "tool.before"
            if before in binding["stages"]:
                continue
        operation_id = (
            "hook:" + sha256(f"{occurrence}:{binding['binding_id']}:{stage}".encode()).hexdigest()
        )
        arguments = {
            "stage": stage,
            "binding_id": binding["binding_id"],
            "occurrence": int(sha256(occurrence.encode()).hexdigest()[:8], 16),
            "payload": effective,
        }
        if binding["binding_id"] not in active:
            active.append(binding["binding_id"])
        try:
            decision = DECISION.validate_python(await invoke(binding, arguments, operation_id))
            if binding["failure_policy"] == "observe":
                if decision.decision != "continue":
                    raise ForbiddenError("An observer hook cannot patch or block execution")
            elif isinstance(decision, Block):
                raise ForbiddenError("An installed hook blocked execution", code="hook_blocked")
            elif isinstance(decision, Patch):
                if not set(decision.fields) <= PATCH_FIELDS.get(stage, frozenset()):
                    raise ForbiddenError("The hook patch exceeds the stage whitelist")
                if not set(decision.fields) <= set(binding["patch_fields"]):
                    raise ForbiddenError("The hook patch exceeds its declared whitelist")
                effective.update(decision.fields)
        except Exception:
            if binding["failure_policy"] != "observe":
                if stage not in {"on_error", "finalize"}:
                    for cleanup in ("on_error", "finalize"):
                        with suppress(Exception):
                            await apply_chain(
                                stage=cleanup,
                                occurrence=occurrence,
                                payload={"error_code": "operation_failed"}
                                if cleanup == "on_error"
                                else {"status": "failed"},
                                bindings=bindings,
                                invoke=invoke,
                                entered=active,
                            )
                raise
    return {"payload": effective, "entered": active}
