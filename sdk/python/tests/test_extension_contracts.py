"""S3 extension contract tests: manifest validation, operations, hooks, events, tools."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import JsonValue

from opsmesh_sdk.errors import UnknownEffectError, ValidationError
from opsmesh_sdk.extensions.channels import InboundMessage, SignatureVerifier
from opsmesh_sdk.extensions.events import EventEnvelope
from opsmesh_sdk.extensions.hooks import Block, Continue, HookInput, Patch, validate_decision
from opsmesh_sdk.extensions.operations import OperationResult, declare_operation
from opsmesh_sdk.extensions.tools import tool_schema_for_operation
from opsmesh_sdk.manifest import (
    EffectKind,
    HookStage,
    JsonSchema,
    OperationExport,
    PackageManifest,
    Permission,
    validate_manifest,
)

OPERATION_MANIFEST: dict[str, JsonValue] = {
    "package_name": "document-knowledge",
    "version": "1.0.0",
    "description": "Knowledge operations",
    "operations": [
        {
            "export_id": "search",
            "description": "Search libraries",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "effects": ["read"],
            "permissions": ["resources.files.read"],
        }
    ],
    "permissions": [
        {"name": "resources.files.read", "reason": "read library files"},
    ],
}


def test_manifest_validates_and_exposes_exports() -> None:
    manifest = validate_manifest(OPERATION_MANIFEST)
    assert manifest.package_name == "document-knowledge"
    assert manifest.export_ids == ("search",)
    assert manifest.operations[0].has_external_effect is True


def test_declared_schema_round_trips_through_its_exported_document() -> None:
    schema = JsonSchema(properties={"query": {"type": "string"}}, required=("query",))
    exported = schema.model_dump_schema()
    assert exported["additionalProperties"] is False
    assert exported["required"] == ["query"]
    assert JsonSchema.model_validate(exported) == schema


def test_manifest_rejects_undeclared_permission() -> None:
    payload = dict(OPERATION_MANIFEST)
    payload["operations"] = [
        {
            "export_id": "search",
            "description": "Search libraries",
            "effects": ["read"],
            "permissions": ["resources.files.write"],
        }
    ]
    with pytest.raises(ValidationError):
        validate_manifest(payload)


def test_manifest_rejects_duplicate_export_ids() -> None:
    payload = dict(OPERATION_MANIFEST)
    operation = dict(OPERATION_MANIFEST["operations"][0])  # type: ignore[index]
    payload["operations"] = [operation, operation]
    with pytest.raises(ValidationError):
        validate_manifest(payload)


def test_manifest_rejects_event_consuming_unknown_operation() -> None:
    payload = dict(OPERATION_MANIFEST)
    payload["events"] = [
        {
            "export_id": "on-run-finished",
            "description": "Consume run completions",
            "source_types": ["run"],
            "source_versions": ["1"],
            "operation_export_id": "missing",
            "idempotency_key_fields": ["event_id"],
        }
    ]
    with pytest.raises(ValidationError):
        validate_manifest(payload)


def test_declared_operation_validates_input_and_derives_tool_schema() -> None:
    @declare_operation(
        "echo",
        description="Echo the query",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        effects=(EffectKind.READ,),
    )
    async def echo(arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {"echo": arguments["query"]}

    schema = tool_schema_for_operation(echo)
    assert schema.name == "echo"
    assert schema.parameters["required"] == ["query"]

    async def run() -> Any:
        return await echo.invoke({"query": "hello"})

    assert asyncio.run(run()) == {"echo": "hello"}

    async def run_invalid() -> Any:
        return await echo.invoke({})

    with pytest.raises(Exception):  # noqa: B017 - jsonschema ValidationError
        asyncio.run(run_invalid())


def test_operation_result_rejects_needs_attention_without_effects() -> None:
    with pytest.raises(ValueError):
        OperationResult(value={"x": 1}, needs_attention=True, effects=(EffectKind.NONE,))


def test_operation_result_size_bound_is_enforced() -> None:
    with pytest.raises(UnknownEffectError):
        OperationResult(value={"payload": "x" * 2_000_000}, effects=(EffectKind.READ,))


def test_hook_decisions_are_typed_and_patches_are_whitelisted() -> None:
    assert validate_decision(Continue(), patch_fields=()) == Continue()
    decision = validate_decision(Patch(fields={"query": "safe"}), patch_fields=("query",))
    assert isinstance(decision, Patch)
    with pytest.raises(ValueError):
        validate_decision(Patch(fields={"system": "override"}), patch_fields=("query",))
    block = Block(reason="denied by policy")
    assert block.decision == "block"


def test_hook_input_carries_stage_and_occurrence() -> None:
    hook = HookInput(stage=HookStage.TOOL_BEFORE, binding_id="b-1", occurrence=3)
    assert hook.stage is HookStage.TOOL_BEFORE
    assert hook.occurrence == 3


def test_event_envelope_deduplication_key_is_stable() -> None:
    envelope = EventEnvelope(
        source_type="run",
        source_id="run-1",
        event_id="evt-1",
        event_version="1",
    )
    assert envelope.deduplication_key() == "run:evt-1:1"


def test_channel_signature_verifier_enforces_the_replay_window() -> None:
    verifier = SignatureVerifier("hmac-sha256", tolerance_seconds=60)
    fresh = InboundMessage(vendor="qq", raw_body=b"{}", delivered_at_unix=1_000)
    stale = InboundMessage(vendor="qq", raw_body=b"{}", delivered_at_unix=1)
    assert verifier.verify(fresh, secret_ref="slot", now_unix=1_030) is True
    assert verifier.verify(stale, secret_ref="slot", now_unix=1_000_000) is False


def test_operation_export_declares_effects() -> None:
    export = OperationExport(
        export_id="write-file",
        description="Write a file",
        effects=(EffectKind.FILE_WRITE,),
        permissions=(),
    )
    assert export.has_external_effect is True


def test_package_manifest_freezes_declarations() -> None:
    manifest = PackageManifest.model_validate(OPERATION_MANIFEST)
    with pytest.raises(Exception):  # noqa: B017 - frozen model
        manifest.version = "2.0.0"  # type: ignore[misc]


def test_permission_namespace_is_derived() -> None:
    permission = Permission(name="governance.audit.append", reason="audit")
    assert permission.namespace == "governance"
