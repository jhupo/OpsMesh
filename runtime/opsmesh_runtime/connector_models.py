"""Typed contracts used by the self-hosted MCP connector."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from .mcp_stdio_client import (
    MCP_SDK_PACKAGE,
    MCP_SDK_STDIO_ENTRYPOINT,
    RUNTIME_CONTRACT_VERSION,
    validate_request_contract,
)

JobPhase = Literal["claimed", "executing", "result_ready"]
CompletionStatus = Literal["completed", "failed"]
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENVIRONMENT_NAMES = frozenset({"OPSMESH_RUNTIME_CREDENTIAL"})


class ConnectorContractError(ValueError):
    """Raised when the control plane sends an unsupported connector contract."""


class ConnectorStateError(RuntimeError):
    """Raised when durable connector state is invalid or cannot transition."""


class ConnectorCredentialError(RuntimeError):
    """Raised when a self-hosted MCP credential cannot be resolved locally."""


class ConnectorApiError(RuntimeError):
    """Raised for a sanitized control-plane transport or response failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class McpJob:
    id: UUID
    request_payload: dict[str, object]

    @classmethod
    def from_api_payload(cls, payload: object) -> McpJob:
        if not isinstance(payload, dict):
            raise ConnectorApiError("Control plane returned an invalid MCP job")
        raw_id = payload.get("id")
        request_payload = payload.get("request_payload")
        if not isinstance(raw_id, str) or not isinstance(request_payload, dict):
            raise ConnectorApiError("Control plane returned an invalid MCP job")
        try:
            job_id = UUID(raw_id)
        except ValueError as exc:
            raise ConnectorApiError("Control plane returned an invalid MCP job") from exc
        return cls(id=job_id, request_payload=request_payload)


@dataclass(frozen=True)
class McpJobCompletion:
    status: CompletionStatus
    response_payload: dict[str, object] | None = None
    error_payload: dict[str, object] | None = None

    @classmethod
    def completed(cls, response_payload: dict[str, object]) -> McpJobCompletion:
        return cls(status="completed", response_payload=response_payload)

    @classmethod
    def failed(cls, *, code: str, message: str) -> McpJobCompletion:
        return cls(
            status="failed",
            error_payload={"code": code, "message": message, "retryable": False},
        )

    @classmethod
    def from_payload(cls, payload: object) -> McpJobCompletion:
        if not isinstance(payload, dict) or payload.get("status") not in {"completed", "failed"}:
            raise ConnectorStateError("Connector recovery completion is invalid")
        status: CompletionStatus = (
            "completed" if payload["status"] == "completed" else "failed"
        )
        response = payload.get("response_payload")
        error = payload.get("error_payload")
        if response is not None and not isinstance(response, dict):
            raise ConnectorStateError("Connector recovery response is invalid")
        if error is not None and not isinstance(error, dict):
            raise ConnectorStateError("Connector recovery error is invalid")
        return cls(status=status, response_payload=response, error_payload=error)

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "response_payload": self.response_payload,
            "error_payload": self.error_payload,
        }


@dataclass(frozen=True)
class PendingMcpJob:
    job: McpJob
    phase: JobPhase
    completion: McpJobCompletion | None


def request_from_job_payload(
    payload: dict[str, object],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    contract_version = payload.get("contract_version")
    sdk = payload.get("sdk")
    request = payload.get("request")
    if (
        contract_version != RUNTIME_CONTRACT_VERSION
        or payload.get("transport") != "stdio"
        or not isinstance(sdk, dict)
        or sdk.get("package") != MCP_SDK_PACKAGE
        or sdk.get("entrypoint") != MCP_SDK_STDIO_ENTRYPOINT
        or not isinstance(request, dict)
    ):
        raise ConnectorContractError("Unsupported self-hosted MCP job contract")
    try:
        validate_request_contract(request)
    except ValueError as exc:
        raise ConnectorContractError("Unsupported self-hosted MCP request contract") from exc
    if "env" in _mapping(request, "server"):
        raise ConnectorContractError(
            "Self-hosted MCP request must not contain expanded credential values"
        )
    environment_refs = _environment_refs(payload)
    if not environment_refs:
        return request
    available_environment = environment or {}
    resolved_environment: dict[str, str] = {}
    for target_name, source_name in environment_refs.items():
        value = available_environment.get(source_name)
        if value is None:
            raise ConnectorCredentialError(
                "Self-hosted MCP credential environment variable is unavailable"
            )
        resolved_environment[target_name] = value
    resolved_request = dict(request)
    server = dict(_mapping(request, "server"))
    server["env"] = resolved_environment
    resolved_request["server"] = server
    try:
        validate_request_contract(resolved_request)
    except ValueError as exc:
        raise ConnectorCredentialError(
            "Self-hosted MCP credential environment is invalid"
        ) from exc
    return resolved_request


def _environment_refs(payload: dict[str, object]) -> dict[str, str]:
    raw_references = payload.get("environment_refs")
    if raw_references is None:
        return {}
    if not isinstance(raw_references, dict) or any(
        not isinstance(target_name, str)
        or not isinstance(source_name, str)
        or not _ENVIRONMENT_NAME.fullmatch(target_name)
        or not _ENVIRONMENT_NAME.fullmatch(source_name)
        or target_name in _RESERVED_ENVIRONMENT_NAMES
        or source_name in _RESERVED_ENVIRONMENT_NAMES
        for target_name, source_name in raw_references.items()
    ):
        raise ConnectorContractError("Unsupported self-hosted MCP credential contract")
    return raw_references


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConnectorContractError("Unsupported self-hosted MCP request contract")
    return value
