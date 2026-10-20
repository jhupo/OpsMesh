"""Installed acceptance package; no server imports or direct external network."""

import asyncio
import os

from opsmesh_sdk.extensions.hooks import Block, Continue
from opsmesh_sdk.extensions.operations import OperationResult, declare_operation
from opsmesh_sdk.extensions.package import ExtensionExports, Hook
from opsmesh_sdk.manifest import (
    AuditAction,
    EffectKind,
    HookExport,
    HookStage,
    JsonSchema,
    PackageManifest,
    Permission,
)

PERMISSIONS = (
    "host.state.read",
    "host.state.write",
    "host.events.publish",
    "host.models.complete",
    "audit.append",
    "audit.read",
    "host.jobs",
)


def exports(context):
    @declare_operation(
        "state",
        description="Store one state value through the authenticated host",
        permissions=PERMISSIONS[:3],
        effects=(EffectKind.WRITE,),
    )
    async def store(arguments):
        assert os.geteuid() != 0
        assert not os.path.exists("/var/run/docker.sock")
        assert not any("DATABASE_URL" in key or "SESSION" in key for key in os.environ)
        from opsmesh_sdk.host.events import EventDraft

        await context.state.set("answer", arguments)
        await context.events.publish(
            EventDraft(event_type="changed", summary={"message": "State saved"}),
            idempotency_key="acceptance-event",
        )
        return OperationResult(
            value={"stored": arguments, "uid": os.geteuid()}, effects=(EffectKind.WRITE,)
        )

    @declare_operation(
        "sleep",
        description="Bounded cancellation occurrence",
        timeout_seconds=20,
        permissions=PERMISSIONS[:2],
        effects=(EffectKind.WRITE,),
    )
    async def sleep(arguments):
        record = await context.state.get("sleep_count")
        await context.state.set("sleep_count", (record.value or 0) + 1)
        await asyncio.sleep(6)
        return OperationResult(value={"done": True}, effects=(EffectKind.WRITE,))

    @declare_operation(
        "model",
        description="Metered native host completion",
        permissions=("host.models.complete",),
        timeout_seconds=120,
        effects=(EffectKind.MODEL_CALL,),
    )
    async def model(arguments):
        from opsmesh_sdk.host.models import ModelPrompt
        from opsmesh_sdk.values import ContentBlock

        result = await context.models.complete(
            context.configuration["model_connection"],
            ModelPrompt(
                blocks=(ContentBlock(block_type="text", text="Reply only S8_OK"),),
                max_output_tokens=2048,
                reasoning_effort="medium",
            ),
        )
        return OperationResult(
            value=result.model_dump(mode="json"), effects=(EffectKind.MODEL_CALL,)
        )

    async def guard(argument):
        if argument.stage == "input_guardrail":
            return Block(reason="Acceptance input rejected")
        return Continue()

    @declare_operation(
        "audit",
        description="Public SDK delegated audit",
        permissions=("audit.append", "audit.read"),
        effects=(EffectKind.WRITE,),
    )
    async def audit(arguments):
        from opsmesh_sdk.api.platform import ExtensionAuditAppend

        platform = context.platform.group("platform")
        receipt = await platform.audit.record(
            ExtensionAuditAppend(
                action="state.saved",
                target_type="value",
                target_id="answer",
                summary="Acceptance audit",
                details={},
                idempotency_key="audit-state",
            )
        )
        event = await platform.audit.read(receipt.event_id)
        assert str(event.invocation_id) == str(context.subject.invocation_id)
        return OperationResult(value={"action": event.action}, effects=(EffectKind.WRITE,))

    @declare_operation(
        "job",
        description="Submit the unique core flow engine",
        permissions=("host.jobs",),
        effects=(EffectKind.WRITE,),
    )
    async def job(arguments):
        from opsmesh_sdk.host.jobs import JobSubmission

        reference = await context.jobs.submit(
            JobSubmission(
                definition_revision=arguments["revision_id"],
                entry_step="flow",
                input={},
                idempotency_key="job-once",
            )
        )
        return OperationResult(value=reference.model_dump(mode="json"), effects=(EffectKind.WRITE,))

    @declare_operation("plan", description="Return a proposed bounded graph")
    async def plan(arguments):
        return OperationResult(value={"graph": arguments["graph"]})

    @declare_operation("probe", description="Confirm installed project tool execution")
    async def probe(arguments):
        return OperationResult(value={"message": "PROJECT_TOOL_OK"})

    hook = Hook(
        HookExport(
            export_id="guard", description="Acceptance guard", stages=(HookStage.INPUT_GUARDRAIL,)
        ),
        guard,
    )

    async def trace(argument):
        return Continue()

    async def block_output(argument):
        return Block(reason="Acceptance output rejected")

    output_guard = Hook(
        HookExport(
            export_id="output_guard",
            description="Acceptance output guard",
            stages=(HookStage.OUTPUT_GUARDRAIL,),
        ),
        block_output,
    )

    tracing = Hook(
        HookExport(
            export_id="trace",
            description="Acceptance occurrence receipts",
            stages=(
                HookStage.MODEL_BEFORE,
                HookStage.MODEL_AFTER,
                HookStage.TOOL_BEFORE,
                HookStage.TOOL_AFTER,
                HookStage.FINALIZE,
                HookStage.ON_ERROR,
            ),
        ),
        trace,
    )
    metadata = PackageManifest(
        package_name="s8_acceptance",
        version="1.0.0",
        description="S8 isolated acceptance",
        permissions=tuple(Permission(name=name, reason="Acceptance") for name in PERMISSIONS),
        published_events=("changed",),
        audit_actions=(AuditAction(action="state.saved", target_types=("value",)),),
        configuration_schema=JsonSchema(properties={"model_connection": {"type": "string"}}),
    )
    return ExtensionExports(
        metadata,
        operations=(store, sleep, model, audit, job, plan, probe),
        hooks=(hook, tracing, output_guard),
    )
