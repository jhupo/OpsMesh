"""Resolve hosted credentials only at authorized native MCP boundaries."""

from collections.abc import Mapping

from opsmesh.capabilities.mcp.models import MCPCredentialReference
from opsmesh.capabilities.mcp.transport.stdio_credentials import _merge_environment
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService


def resolve_hosted_credentials(
    credentials: list[MCPCredentialReference], secrets: SecretEncryptionService | None
) -> tuple[dict[str, str], dict[str, str]]:
    environment: dict[str, str] = {}
    headers: dict[str, str] = {}
    for credential in credentials:
        if credential.provider != "hosted" or credential.encrypted_secret_payload is None:
            raise PolicyDeniedError("MCP server has an unavailable hosted credential")
        if secrets is None:
            raise PolicyDeniedError("MCP credential decryption is unavailable")
        try:
            payload = secrets.decrypt_payload(
                credential.encrypted_secret_payload,
                key_id=credential.encryption_key_id,
            )
        except ValueError as exc:
            raise PolicyDeniedError("MCP server credential cannot be decrypted") from exc
        raw_environment = payload.get("env")
        if isinstance(raw_environment, dict):
            _merge_environment(environment, raw_environment)
        raw_headers = payload.get("headers")
        if isinstance(raw_headers, dict):
            _merge_strings(headers, raw_headers, "headers")
        bearer = payload.get("bearer_token")
        api_key = payload.get("api_key")
        if isinstance(bearer, str) and bearer:
            _merge_strings(headers, {"authorization": f"Bearer {bearer}"}, "headers")
        if isinstance(api_key, str) and api_key:
            _merge_strings(
                headers, {payload.get("api_key_header", "x-api-key"): api_key}, "headers"
            )
        if (
            not isinstance(raw_environment, dict)
            and not isinstance(raw_headers, dict)
            and not bearer
            and not api_key
        ):
            raise PolicyDeniedError("MCP hosted credential has no supported values")
    return environment, headers


def _merge_strings(destination: dict[str, str], values: Mapping[object, object], kind: str) -> None:
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or "\r" in key + value
            or "\n" in key + value
            or key.lower() in {name.lower() for name in destination}
        ):
            raise PolicyDeniedError(f"MCP {kind} credentials contain invalid or duplicate values")
        destination[key] = value
