from __future__ import annotations

import re

from backend.app.capabilities.mcp.types import McpExecutionError
from backend.app.capabilities.models import McpCredentialReference
from backend.app.secrets.service import SecretEncryptionService

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SELF_HOSTED_ENV_PROVIDER = "self_hosted_env"
_SELF_HOSTED_ENV_PREFIX = "env:"
_RESERVED_SELF_HOSTED_ENV_NAMES = frozenset({"OPSMESH_RUNTIME_CREDENTIAL"})
_MAX_ENVIRONMENT_VARIABLES = 128
_MAX_ENVIRONMENT_BYTES = 65_536


def hosted_stdio_environment(
    credential_refs: list[McpCredentialReference],
    *,
    secret_service: SecretEncryptionService | None,
) -> dict[str, str]:
    environment: dict[str, str] = {}
    for credential in credential_refs:
        if credential.provider != "hosted" or credential.encrypted_secret_payload is None:
            raise McpExecutionError(
                "MCP stdio credential is unavailable in the Docker runtime",
                code="mcp_stdio_credential_unavailable",
            )
        if secret_service is None:
            raise McpExecutionError(
                "Hosted MCP credential decryption is not configured",
                code="mcp_hosted_credential_unavailable",
            )
        try:
            payload = secret_service.decrypt_payload(
                credential.encrypted_secret_payload,
                key_id=credential.encryption_key_id,
            )
        except ValueError as exc:
            raise McpExecutionError(
                "Hosted MCP credential could not be decrypted",
                code="mcp_hosted_credential_unavailable",
            ) from exc
        raw_environment = payload.get("env")
        if not isinstance(raw_environment, dict) or not raw_environment:
            raise McpExecutionError(
                "Hosted MCP stdio credential must contain a non-empty env mapping",
                code="mcp_stdio_credential_invalid",
            )
        _merge_environment(environment, raw_environment)
    return environment


def self_hosted_stdio_environment_refs(
    credential_refs: list[McpCredentialReference],
) -> dict[str, str]:
    references: dict[str, str] = {}
    for credential in credential_refs:
        if (
            credential.provider != _SELF_HOSTED_ENV_PROVIDER
            or not credential.external_ref.startswith(_SELF_HOSTED_ENV_PREFIX)
        ):
            raise McpExecutionError(
                "MCP stdio credential is unavailable on the self-hosted runtime",
                code="mcp_self_hosted_credential_unavailable",
            )
        environment_name = credential.external_ref.removeprefix(_SELF_HOSTED_ENV_PREFIX)
        _validate_environment_name(environment_name)
        if environment_name in _RESERVED_SELF_HOSTED_ENV_NAMES:
            raise McpExecutionError(
                "MCP stdio credential cannot reference a reserved connector variable",
                code="mcp_self_hosted_credential_forbidden",
            )
        if environment_name in references:
            raise McpExecutionError(
                "MCP stdio credentials define the same environment variable more than once",
                code="mcp_stdio_credential_conflict",
            )
        references[environment_name] = environment_name
        _enforce_environment_limits(references)
    return references


def _merge_environment(
    destination: dict[str, str],
    raw_environment: dict[object, object],
) -> None:
    for raw_name, raw_value in raw_environment.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise McpExecutionError(
                "Hosted MCP stdio credential env must contain string names and values",
                code="mcp_stdio_credential_invalid",
            )
        _validate_environment_name(raw_name)
        if raw_name in destination:
            raise McpExecutionError(
                "MCP stdio credentials define the same environment variable more than once",
                code="mcp_stdio_credential_conflict",
            )
        destination[raw_name] = raw_value
        _enforce_environment_limits(destination)


def _validate_environment_name(name: str) -> None:
    if not _ENVIRONMENT_NAME.fullmatch(name):
        raise McpExecutionError(
            "MCP stdio credential environment variable name is invalid",
            code="mcp_stdio_credential_invalid",
        )


def _enforce_environment_limits(environment: dict[str, str]) -> None:
    encoded_bytes = sum(
        len(name.encode("utf-8")) + len(value.encode("utf-8"))
        for name, value in environment.items()
    )
    if (
        len(environment) > _MAX_ENVIRONMENT_VARIABLES
        or encoded_bytes > _MAX_ENVIRONMENT_BYTES
    ):
        raise McpExecutionError(
            "MCP stdio credential environment exceeds configured limits",
            code="mcp_stdio_credential_too_large",
        )
