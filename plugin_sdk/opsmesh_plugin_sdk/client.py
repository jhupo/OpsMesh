"""Connector ingress over the authenticated public API; HTTP is owned by HTTPX."""

from uuid import UUID

import httpx

from opsmesh_plugin_sdk.contracts import AcceptedEvent, IncomingMessage


class AutomationClient:
    def __init__(self, client: httpx.Client, workspace_id: UUID, automation_id: UUID) -> None:
        """Client base_url must include /api/v1/ and its auth must carry a workspace token."""
        self._client = client
        self._path = f"workspaces/{workspace_id}/automations/{automation_id}/events"

    def submit(self, message: IncomingMessage) -> AcceptedEvent:
        response = self._client.post(self._path, json=message.model_dump(mode="json"))
        response.raise_for_status()
        return AcceptedEvent.model_validate(response.json())
