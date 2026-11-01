"""Immutable capability versions and an authorized, replay-safe tool gateway."""

import asyncio
import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.modules.capabilities.api import (
    CapabilityCatalog,
    CodeToolRegistry,
    ScriptTools,
    ToolAuthorization,
    ToolCallback,
    ToolDefinition,
)

from .serialization import canonical


class AuthorizedToolInvoker:
    def __init__(
        self,
        database: AsyncDatabase,
        catalog: CapabilityCatalog,
        code_tools: CodeToolRegistry,
        authorize: ToolAuthorization,
        *,
        scripts: ScriptTools | None = None,
    ) -> None:
        self._calls = VersionedRecordStore(database, "tool_calls")
        self._catalog = catalog
        self._code = code_tools
        self._authorize = authorize
        self._scripts = scripts

    async def invoke(
        self, tool_ref: str, arguments: dict[str, Any], *, principal_id: str, call_id: str
    ) -> Any:
        if not call_id or not principal_id:
            raise OpsMeshError("CONFIG_INVALID", "Principal and idempotency key are required")
        await self._authorize(principal_id, tool_ref)
        tool = await self._catalog.get_tool(tool_ref)
        if tool.allowed_principals and principal_id not in tool.allowed_principals:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Tool is scoped to a different principal")
        fingerprint = self._validate_arguments(tool, arguments)
        key = hashlib.sha256(canonical([principal_id, call_id])).hexdigest()
        prior = await self._calls.get(key)
        if prior is not None:
            return self._replay(prior, fingerprint)
        if tool.code_ref.startswith("script:"):
            if self._scripts is None:
                raise OpsMeshError("FEATURE_UNSUPPORTED", "Script execution is not configured")
            scripts = self._scripts

            async def script_callback(arguments: dict[str, Any]) -> Any:
                return await scripts.execute(
                    tool.code_ref.removeprefix("script:"), arguments, principal_id=principal_id
                )

            callback: ToolCallback = script_callback
        else:
            callback = self._code.resolve(tool.code_ref)
        record = await self._calls.create(
            key,
            {
                "principal_id": principal_id,
                "tool_ref": tool_ref,
                "fingerprint": fingerprint,
                "status": "running",
                "call_id": call_id,
            },
        )
        return await self._execute(record, tool, callback, arguments)

    @staticmethod
    def _validate_arguments(tool: ToolDefinition, arguments: dict[str, Any]) -> str:
        try:
            Draft202012Validator(tool.input_schema).validate(arguments)
            return hashlib.sha256(canonical([tool.ref, arguments])).hexdigest()
        except (ValidationError, TypeError, ValueError) as error:
            raise OpsMeshError("INPUT_INVALID", "Tool arguments do not match schema") from error

    @staticmethod
    def _replay(prior: StoredRecord, fingerprint: str) -> Any:
        if prior.data["fingerprint"] != fingerprint:
            raise OpsMeshError("CONFLICT", "Idempotency key was used for another call")
        if prior.data["status"] == "succeeded":
            return prior.data["result"]
        raise OpsMeshError(
            "TOOL_REPLAY_BLOCKED", "Call is pending or failed; do not replay side effects"
        )

    async def _execute(
        self,
        record: StoredRecord,
        tool: ToolDefinition,
        callback: ToolCallback,
        arguments: dict[str, Any],
    ) -> Any:
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                result = await callback(arguments)
            # Persist only portable results; never store a live SDK object.
            result = json.loads(canonical(result))
        except BaseException as error:
            code = (
                error.code
                if isinstance(error, OpsMeshError)
                else (
                    "TOOL_TIMEOUT" if isinstance(error, TimeoutError) else "TOOL_EXECUTION_FAILED"
                )
            )
            await self._calls.update(
                record.id,
                {**record.data, "status": "failed", "error": code},
                expected_revision=record.revision,
            )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise OpsMeshError(code, "Tool execution failed") from error
        await self._calls.update(
            record.id,
            {**record.data, "status": "succeeded", "result": result},
            expected_revision=record.revision,
        )
        return result
