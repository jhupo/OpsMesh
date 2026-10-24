"""Management provenance and process-local execution claims."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from opsmesh_sdk.channel_management import ConversationRef, ManagementRequest
from pydantic import BaseModel, ConfigDict

from opsmesh.plugins.base import PreparedResource
from opsmesh.shared.serialization import payload_digest


class OperationOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["admin", "tool", "filter", "event"]
    actor_id: UUID
    resource_id: str | None = None
    generation: str | None = None
    source_id: UUID | None = None


@dataclass(frozen=True)
class OperationClaim:
    id: UUID
    request: ManagementRequest
    origin: OperationOrigin
    requester_id: str | None
    requester_roles: frozenset[str]
    approver_id: UUID | None = None


def reference_key(value: ConversationRef) -> str:
    return payload_digest({"scope": value.scope, "kind": value.kind, "id": value.external_id})


def resource_generation(resource: PreparedResource) -> str:
    return str(resource.metadata["generation"])


def resolve_generation(resources: dict[str, PreparedResource], identifier: str) -> str | None:
    resource = resources.get(identifier)
    return resource_generation(resource) if resource else None
