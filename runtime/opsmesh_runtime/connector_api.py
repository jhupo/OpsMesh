"""HTTP boundary for the OpsMesh self-hosted MCP connector."""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .connector_models import (
    ConnectorApiError,
    McpJob,
    McpJobCompletion,
)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class HttpMcpJobApi:
    def __init__(
        self,
        *,
        api_url: str,
        credential: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = _validated_api_url(api_url)
        if not credential.strip():
            raise ValueError("Runtime credential is required")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-Runtime-Authorization": f"Bearer {credential.strip()}"},
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def heartbeat(
        self,
        capabilities: dict[str, object],
        attestation: dict[str, object] | None = None,
    ) -> None:
        heartbeat_payload: dict[str, object] = {
            "status": "online",
            "capabilities": capabilities,
        }
        if attestation is not None:
            heartbeat_payload["attestation"] = attestation
        payload = self._request(
            "POST",
            "self-hosted/heartbeat",
            json=heartbeat_payload,
        )
        if not isinstance(payload, dict) or payload.get("status") != "online":
            raise ConnectorApiError("Control plane returned an invalid heartbeat response")

    def poll_mcp_job(self) -> McpJob | None:
        payload = self._request("GET", "self-hosted/mcp-jobs/next")
        return None if payload is None else McpJob.from_api_payload(payload)

    def claim_mcp_job(self, job_id: UUID) -> None:
        payload = self._request("POST", f"self-hosted/mcp-jobs/{job_id}/claim")
        if (
            not isinstance(payload, dict)
            or payload.get("id") != str(job_id)
            or payload.get("status") != "claimed"
        ):
            raise ConnectorApiError("Control plane returned an invalid MCP claim response")

    def complete_mcp_job(self, job_id: UUID, completion: McpJobCompletion) -> None:
        payload = self._request(
            "POST",
            f"self-hosted/mcp-jobs/{job_id}/complete",
            json=completion.to_payload(),
        )
        if (
            not isinstance(payload, dict)
            or payload.get("id") != str(job_id)
            or payload.get("status") != completion.status
        ):
            raise ConnectorApiError("Control plane returned an invalid MCP completion response")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpMcpJobApi:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> object:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ConnectorApiError("Control plane request failed", retryable=True) from exc
        if not response.is_success:
            status_code = response.status_code
            raise ConnectorApiError(
                f"Control plane request failed with HTTP status {status_code}",
                status_code=status_code,
                retryable=status_code in {408, 429} or status_code >= 500,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorApiError(
                "Control plane returned invalid JSON",
                retryable=True,
            ) from exc


def _validated_api_url(api_url: str) -> str:
    normalized = api_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("API URL must be an absolute HTTP(S) URL without credentials or query")
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("Remote self-hosted connector API URLs must use HTTPS")
    return f"{normalized}/"
