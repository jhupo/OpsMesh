from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.schema import Table

from backend.app.shared.db.base import Base
from backend.app.shared.pagination import PageParams


class AdminCatalogKind(StrEnum):
    PROJECT = "project"
    AGENT = "agent"
    TEAM = "team"
    TASK = "task"
    APPROVAL = "approval"
    RUN = "run"
    CAPABILITY = "capability"
    CAPABILITY_RESOURCE = "capability_resource"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"
    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    SKILL_INSTALL = "skill_install"
    MODEL_PROVIDER = "model_provider"
    PLUGIN = "plugin"
    PLUGIN_RELEASE = "plugin_release"
    PLUGIN_TRUST_KEY = "plugin_trust_key"
    MARKETPLACE_LISTING = "marketplace_listing"


@dataclass(frozen=True)
class AdminCatalogSpec:
    table_name: str
    workspace_column: str | None
    name_columns: tuple[str, ...]
    status_column: str | None
    owner_columns: tuple[str, ...] = ()
    metadata_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdminCatalogResource:
    resource_kind: AdminCatalogKind
    resource_id: UUID
    workspace_id: UUID | None
    name: str | None
    status: str | None
    owner_user_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    metadata: dict[str, object]


_SPECS: dict[AdminCatalogKind, AdminCatalogSpec] = {
    AdminCatalogKind.PROJECT: AdminCatalogSpec(
        "workspace_projects",
        "workspace_id",
        ("name", "slug"),
        "status",
        owner_columns=("created_by_user_id",),
        metadata_columns=("slug", "configuration_version"),
    ),
    AdminCatalogKind.AGENT: AdminCatalogSpec(
        "agent_profiles",
        "workspace_id",
        ("name", "role"),
        "status",
        metadata_columns=("role", "model", "version"),
    ),
    AdminCatalogKind.TEAM: AdminCatalogSpec(
        "agent_teams",
        "workspace_id",
        ("name", "team_type"),
        "status",
        metadata_columns=("team_type", "capability_policy_version"),
    ),
    AdminCatalogKind.TASK: AdminCatalogSpec(
        "tasks",
        "workspace_id",
        ("title", "domain_type"),
        "status",
        owner_columns=("created_by_user_id",),
        metadata_columns=("priority", "workspace_project_id", "agent_team_id"),
    ),
    AdminCatalogKind.APPROVAL: AdminCatalogSpec(
        "approvals",
        "workspace_id",
        ("approval_type",),
        "status",
        metadata_columns=("risk_level", "task_id", "agent_run_id"),
    ),
    AdminCatalogKind.RUN: AdminCatalogSpec(
        "agent_runs",
        "workspace_id",
        ("model",),
        "status",
        metadata_columns=("task_id", "agent_profile_id", "runtime_space_id"),
    ),
    AdminCatalogKind.CAPABILITY: AdminCatalogSpec(
        "capabilities",
        None,
        ("name", "key"),
        "status",
        metadata_columns=("category", "platform_blocked", "platform_previous_status"),
    ),
    AdminCatalogKind.CAPABILITY_RESOURCE: AdminCatalogSpec(
        "capability_resources",
        "workspace_id",
        ("name", "key"),
        "status",
        owner_columns=("created_by_user_id",),
        metadata_columns=(
            "resource_type",
            "access_mode",
            "version",
            "platform_blocked",
            "platform_previous_status",
        ),
    ),
    AdminCatalogKind.TOOL: AdminCatalogSpec(
        "tool_groups",
        None,
        ("name", "key"),
        "status",
        metadata_columns=("tool_names", "platform_blocked", "platform_previous_status"),
    ),
    AdminCatalogKind.MCP_SERVER: AdminCatalogSpec(
        "mcp_servers",
        "workspace_id",
        ("name",),
        "status",
        metadata_columns=(
            "server_type",
            "visibility",
            "health_status",
            "configuration_version",
            "platform_blocked",
            "platform_previous_status",
        ),
    ),
    AdminCatalogKind.MCP_TOOL: AdminCatalogSpec(
        "mcp_tool_allowlist",
        "workspace_id",
        ("tool_name", "title"),
        "status",
        metadata_columns=(
            "mcp_server_id",
            "risk_level",
            "requires_approval",
            "capability_key",
            "platform_blocked",
            "platform_previous_status",
        ),
    ),
    AdminCatalogKind.SKILL: AdminCatalogSpec(
        "skills",
        "owner_workspace_id",
        ("name", "key"),
        "status",
        metadata_columns=("version", "visibility", "platform_blocked", "platform_previous_status"),
    ),
    AdminCatalogKind.SKILL_INSTALL: AdminCatalogSpec(
        "workspace_skill_installs",
        "workspace_id",
        ("installed_name", "installed_key"),
        "status",
        owner_columns=("installed_by_user_id",),
        metadata_columns=(
            "skill_id",
            "installed_version",
            "source_owner_workspace_id",
            "source_visibility",
            "platform_blocked",
            "platform_previous_status",
        ),
    ),
    AdminCatalogKind.MARKETPLACE_LISTING: AdminCatalogSpec(
        "marketplace_listings",
        "workspace_id",
        ("name",),
        "status",
        owner_columns=("owner_user_id",),
        metadata_columns=(
            "listing_type",
            "visibility",
            "version",
            "source_resource_id",
            "platform_blocked",
            "platform_previous_status",
        ),
    ),
    AdminCatalogKind.MODEL_PROVIDER: AdminCatalogSpec(
        "model_provider_credentials",
        "workspace_id",
        ("name", "provider"),
        "status",
        owner_columns=("created_by_user_id",),
        metadata_columns=("provider", "default_model", "is_default", "health_status"),
    ),
    AdminCatalogKind.PLUGIN: AdminCatalogSpec(
        "plugin_installs",
        "workspace_id",
        ("plugin_key",),
        "status",
        metadata_columns=("current_version", "generation", "platform_blocked"),
    ),
    AdminCatalogKind.PLUGIN_RELEASE: AdminCatalogSpec(
        "plugin_releases",
        "workspace_id",
        ("version",),
        "status",
        metadata_columns=("install_id", "trust_key_id", "checksum"),
    ),
    AdminCatalogKind.PLUGIN_TRUST_KEY: AdminCatalogSpec(
        "plugin_trust_keys",
        "workspace_id",
        ("plugin_key", "key_id"),
        "status",
        metadata_columns=("key_id", "plugin_key"),
    ),
}


class AdminCatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_resources(
        self,
        kind: AdminCatalogKind,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[AdminCatalogResource], int]:
        table, columns, filters = self._query_parts(kind, workspace_id=workspace_id, status=status)
        statement: Select[Any] = select(*columns).select_from(table).where(*filters)
        order_column = table.c.created_at if "created_at" in table.c else table.c.id
        statement = statement.order_by(order_column.desc(), table.c.id.desc())
        total = int(
            self._session.scalar(select(func.count()).select_from(table).where(*filters)) or 0
        )
        rows = self._session.execute(statement.limit(page.limit).offset(page.offset)).mappings()
        return [self._resource(kind, row) for row in rows], total

    def get_resource(
        self,
        kind: AdminCatalogKind,
        resource_id: UUID,
        *,
        workspace_id: UUID | None,
    ) -> AdminCatalogResource | None:
        spec = _SPECS[kind]
        if (
            spec.workspace_column is not None
            and workspace_id is None
            and kind != AdminCatalogKind.SKILL
        ):
            raise ValueError("workspace_id is required for workspace-scoped catalog resources")
        table, columns, filters = self._query_parts(kind, workspace_id=workspace_id)
        if kind == AdminCatalogKind.SKILL and workspace_id is None:
            filters.append(table.c.owner_workspace_id.is_(None))
        row = (
            self._session.execute(
                select(*columns).select_from(table).where(*filters, table.c.id == resource_id)
            )
            .mappings()
            .first()
        )
        return self._resource(kind, row) if row is not None else None

    def _query_parts(
        self,
        kind: AdminCatalogKind,
        *,
        workspace_id: UUID | None,
        status: str | None = None,
    ) -> tuple[Table, list[ColumnElement[Any]], list[ColumnElement[bool]]]:
        spec = _SPECS[kind]
        table = Base.metadata.tables.get(spec.table_name)
        if table is None:
            raise ValueError(f"Catalog table is unavailable: {spec.table_name}")
        selected_names = {
            "id",
            "created_at",
            "updated_at",
            *(spec.name_columns),
            *(spec.owner_columns),
            *(spec.metadata_columns),
        }
        if spec.workspace_column is not None:
            selected_names.add(spec.workspace_column)
        if spec.status_column is not None:
            selected_names.add(spec.status_column)
        columns: list[ColumnElement[Any]] = [
            table.c[name] for name in sorted(selected_names) if name in table.c
        ]
        filters: list[ColumnElement[bool]] = []
        if spec.workspace_column is not None and workspace_id is not None:
            filters.append(table.c[spec.workspace_column] == workspace_id)
        if status is not None and spec.status_column is not None:
            filters.append(table.c[spec.status_column] == status)
        return table, columns, filters

    def _resource(self, kind: AdminCatalogKind, row: Any) -> AdminCatalogResource:
        spec = _SPECS[kind]
        value = row._mapping if hasattr(row, "_mapping") else row

        def first(columns: tuple[str, ...]) -> object | None:
            for column in columns:
                item = value.get(column)
                if item not in (None, ""):
                    return cast(object, item)
            return None

        workspace_value = value.get(spec.workspace_column) if spec.workspace_column else None
        owner_value = first(spec.owner_columns)
        metadata = {
            column: value[column]
            for column in spec.metadata_columns
            if column in value and value[column] is not None
        }
        return AdminCatalogResource(
            resource_kind=kind,
            resource_id=value["id"],
            workspace_id=workspace_value if isinstance(workspace_value, UUID) else None,
            name=str(first(spec.name_columns)) if first(spec.name_columns) is not None else None,
            status=str(value[spec.status_column])
            if spec.status_column and value.get(spec.status_column) is not None
            else None,
            owner_user_id=owner_value if isinstance(owner_value, UUID) else None,
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            metadata=metadata,
        )
