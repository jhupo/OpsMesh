from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.capabilities.catalog import (
    CapabilityResourceCreateRequest,
    CapabilityResourceUpdateRequest,
)
from backend.app.capabilities.models import (
    CapabilityResource,
    McpCredentialReference,
    McpServer,
)
from backend.app.capabilities.resource_validation import normalize_resource_locator
from backend.app.capabilities.schema_validation import (
    normalize_object_schema,
    reject_embedded_secrets,
    validate_parameters,
)
from backend.app.core.errors import DomainError, NotFoundError
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.files.models import WorkspaceFile
from backend.app.observability.audit_service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.runtime_manager.spaces.models import RuntimeSpace
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam

OwnedResource = TypeVar(
    "OwnedResource",
    WorkspaceFile,
    McpServer,
    McpCredentialReference,
    RuntimeSpace,
    WorkspaceRuntime,
)


class CapabilityResourceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_resources(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        include_disabled: bool = False,
    ) -> tuple[list[CapabilityResource], int]:
        statement = select(CapabilityResource).where(
            CapabilityResource.workspace_id == workspace_id
        )
        count_statement = (
            select(func.count())
            .select_from(CapabilityResource)
            .where(CapabilityResource.workspace_id == workspace_id)
        )
        if not include_disabled:
            statement = statement.where(CapabilityResource.status == "active")
            count_statement = count_statement.where(CapabilityResource.status == "active")
        total = int(self._session.scalar(count_statement) or 0)
        resources = list(
            self._session.scalars(
                statement.order_by(CapabilityResource.key.asc())
                .limit(page.limit)
                .offset(page.offset)
            ).all()
        )
        return resources, total

    def list_active_resources(self, workspace_id: UUID) -> list[CapabilityResource]:
        return list(
            self._session.scalars(
                select(CapabilityResource)
                .where(
                    CapabilityResource.workspace_id == workspace_id,
                    CapabilityResource.status == "active",
                )
                .order_by(CapabilityResource.key.asc())
            ).all()
        )

    def create_resource(
        self,
        workspace_id: UUID,
        data: CapabilityResourceCreateRequest,
        *,
        actor_user_id: UUID,
    ) -> CapabilityResource:
        locator, schema, defaults = self._validated_configuration(
            workspace_id=workspace_id,
            resource_type=data.resource_type,
            access_mode=data.access_mode,
            locator=data.locator,
            parameter_schema=data.parameter_schema,
            default_parameters=data.default_parameters,
        )
        resource = CapabilityResource(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            key=data.key,
            name=data.name.strip(),
            resource_type=data.resource_type,
            description=data.description.strip(),
            access_mode=data.access_mode,
            locator=locator,
            parameter_schema=schema,
            default_parameters=defaults,
        )
        self._session.add(resource)
        flush_or_raise_conflict(self._session, "Capability resource key already exists")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="capability_resource.created",
            target_type="capability_resource",
            target_id=resource.id,
            metadata=self._audit_snapshot(resource),
        )
        commit_or_raise_conflict(self._session, "Capability resource key already exists")
        self._session.refresh(resource)
        return resource

    def update_resource(
        self,
        workspace_id: UUID,
        resource_id: UUID,
        data: CapabilityResourceUpdateRequest,
        *,
        actor_user_id: UUID,
    ) -> CapabilityResource:
        resource = self._require_resource(workspace_id, resource_id, for_update=True)
        next_access_mode = data.access_mode or resource.access_mode
        next_locator = data.locator if data.locator is not None else resource.locator
        next_schema = (
            data.parameter_schema
            if data.parameter_schema is not None
            else resource.parameter_schema
        )
        next_defaults = (
            data.default_parameters
            if data.default_parameters is not None
            else resource.default_parameters
        )
        locator, schema, defaults = self._validated_configuration(
            workspace_id=workspace_id,
            resource_type=resource.resource_type,
            access_mode=next_access_mode,
            locator=next_locator,
            parameter_schema=next_schema,
            default_parameters=next_defaults,
        )
        before = self._audit_snapshot(resource)
        if data.name is not None:
            resource.name = data.name.strip()
        if data.description is not None:
            resource.description = data.description.strip()
        resource.access_mode = next_access_mode
        resource.locator = locator
        resource.parameter_schema = schema
        resource.default_parameters = defaults
        resource.version += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="capability_resource.updated",
            target_type="capability_resource",
            target_id=resource.id,
            metadata={"before": before, "after": self._audit_snapshot(resource)},
        )
        self._session.commit()
        self._session.refresh(resource)
        return resource

    def disable_resource(
        self,
        workspace_id: UUID,
        resource_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> CapabilityResource:
        resource = self._require_resource(workspace_id, resource_id, for_update=True)
        if resource.status != "disabled":
            resource.status = "disabled"
            resource.version += 1
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="capability_resource.disabled",
                target_type="capability_resource",
                target_id=resource.id,
                metadata=self._audit_snapshot(resource),
            )
            self._session.commit()
            self._session.refresh(resource)
        return resource

    def _validated_configuration(
        self,
        *,
        workspace_id: UUID,
        resource_type: str,
        access_mode: str,
        locator: dict[str, object],
        parameter_schema: dict[str, object],
        default_parameters: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        try:
            normalized_locator = normalize_resource_locator(
                resource_type,
                access_mode,
                locator,
            )
            schema = normalize_object_schema(parameter_schema)
            reject_embedded_secrets(schema, path="parameter_schema")
            defaults = dict(default_parameters)
            reject_embedded_secrets(defaults, path="default_parameters")
            validate_parameters(defaults, schema, label="default_parameters")
        except ValueError as exc:
            raise _configuration_error(str(exc)) from exc
        self._validate_locator_references(workspace_id, resource_type, normalized_locator)
        return normalized_locator, schema, defaults

    def _validate_locator_references(
        self,
        workspace_id: UUID,
        resource_type: str,
        locator: dict[str, object],
    ) -> None:
        if resource_type == "file_collection":
            file_ids = locator.get("file_ids")
            if not isinstance(file_ids, list):
                raise _configuration_error("File collection locator is invalid")
            for file_id in file_ids:
                self._require_owned(WorkspaceFile, workspace_id, UUID(str(file_id)), "file")
            return
        if resource_type == "memory_collection":
            self._validate_memory_scope_references(workspace_id, locator)
            return
        if resource_type in {"mcp_resource", "external_service"}:
            server = self._require_owned(
                McpServer,
                workspace_id,
                UUID(str(locator["mcp_server_id"])),
                "MCP server",
            )
            credential_id = locator.get("credential_reference_id")
            if credential_id is not None:
                credential = self._require_owned(
                    McpCredentialReference,
                    workspace_id,
                    UUID(str(credential_id)),
                    "MCP credential reference",
                )
                if credential.mcp_server_id not in {None, server.id}:
                    raise _configuration_error(
                        "MCP credential reference belongs to a different MCP server"
                    )
            return
        if resource_type == "runtime":
            if "runtime_space_id" in locator:
                self._require_owned(
                    RuntimeSpace,
                    workspace_id,
                    UUID(str(locator["runtime_space_id"])),
                    "runtime space",
                )
            else:
                self._require_owned(
                    WorkspaceRuntime,
                    workspace_id,
                    UUID(str(locator["workspace_runtime_id"])),
                    "workspace runtime",
                )

    def _validate_memory_scope_references(
        self,
        workspace_id: UUID,
        locator: dict[str, object],
    ) -> None:
        raw_scope_ids = locator.get("scope_ids")
        if raw_scope_ids is None:
            return
        raw_scope_types = locator.get("scope_types")
        if (
            not isinstance(raw_scope_ids, list)
            or not isinstance(raw_scope_types, list)
            or len(raw_scope_types) != 1
            or not isinstance(raw_scope_types[0], str)
        ):
            raise _configuration_error("Memory scope locator is invalid")
        scope_type = raw_scope_types[0]
        for raw_scope_id in raw_scope_ids:
            scope_id = UUID(str(raw_scope_id))
            if scope_type == "workspace":
                if scope_id != workspace_id:
                    raise _configuration_error(
                        "Referenced memory workspace scope was not found in this workspace"
                    )
                continue
            if scope_type == "team":
                owned_id = self._session.scalar(
                    select(AgentTeam.id).where(
                        AgentTeam.id == scope_id,
                        AgentTeam.workspace_id == workspace_id,
                    )
                )
            elif scope_type == "agent":
                owned_id = self._session.scalar(
                    select(AgentProfile.id).where(
                        AgentProfile.id == scope_id,
                        AgentProfile.workspace_id == workspace_id,
                    )
                )
            elif scope_type == "task":
                owned_id = self._session.scalar(
                    select(Task.id).where(
                        Task.id == scope_id,
                        Task.workspace_id == workspace_id,
                    )
                )
            elif scope_type == "run":
                owned_id = self._session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.id == scope_id,
                        AgentRun.workspace_id == workspace_id,
                    )
                )
            else:
                raise _configuration_error("Memory scope type is unsupported")
            if owned_id is None:
                raise _configuration_error(
                    f"Referenced memory {scope_type} scope was not found in this workspace"
                )

    def _require_owned(
        self,
        model: type[OwnedResource],
        workspace_id: UUID,
        resource_id: UUID,
        label: str,
    ) -> OwnedResource:
        resource = self._session.scalar(
            select(model).where(model.id == resource_id, model.workspace_id == workspace_id)
        )
        if resource is None:
            raise _configuration_error(f"Referenced {label} was not found in this workspace")
        return resource

    def _require_resource(
        self,
        workspace_id: UUID,
        resource_id: UUID,
        *,
        for_update: bool,
    ) -> CapabilityResource:
        statement = select(CapabilityResource).where(
            CapabilityResource.id == resource_id,
            CapabilityResource.workspace_id == workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        resource = self._session.scalar(statement)
        if resource is None:
            raise NotFoundError(
                "Capability resource not found",
                code="capability_resource_not_found",
            )
        return resource

    @staticmethod
    def _audit_snapshot(resource: CapabilityResource) -> dict[str, object]:
        return {
            "key": resource.key,
            "name": resource.name,
            "resource_type": resource.resource_type,
            "access_mode": resource.access_mode,
            "locator": dict(resource.locator),
            "parameter_schema": dict(resource.parameter_schema),
            "default_parameters": dict(resource.default_parameters),
            "version": resource.version,
            "status": resource.status,
        }


def _configuration_error(message: str) -> DomainError:
    return DomainError(
        message,
        code="capability_configuration_invalid",
        status_code=422,
    )
