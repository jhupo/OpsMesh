"""Workspace-owned orchestration definitions and task-plan admission."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.domains.orchestration.models import OrchestrationDefinition, OrchestrationRevision
from backend.app.domains.orchestration.workflows.definitions.commands import (
    OrchestrationDefinitionCreate,
    OrchestrationDefinitionUpdate,
    OrchestrationEditScope,
)
from backend.app.domains.orchestration.workflows.definitions.conditions import (
    condition_step_references,
)
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowNode
from backend.app.domains.orchestration.workflows.definitions.validation import (
    DefinitionValidationError,
    DefinitionValidationService,
    nodes_from_definition,
)
from backend.app.observability.audit.service import AuditService


class OrchestrationDefinitionError(ValueError):
    def __init__(self, message: str, *, code: str = "orchestration_invalid") -> None:
        super().__init__(message)
        self.code = code


class OrchestrationDefinitionService:
    """Manage versioned, workspace-scoped user orchestration definitions."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._validator = DefinitionValidationService(session)

    def list_definitions(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[OrchestrationDefinition], int]:
        statement = select(OrchestrationDefinition).where(
            OrchestrationDefinition.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(OrchestrationDefinition.status == status)
        statement = statement.order_by(
            OrchestrationDefinition.updated_at.desc(),
            OrchestrationDefinition.id.desc(),
        )
        return page_scalars(self._session, statement, page)

    def get_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> OrchestrationDefinition | None:
        return self._session.scalar(
            select(OrchestrationDefinition).where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == orchestration_definition_id,
            )
        )

    def create_definition(
        self,
        workspace_id: UUID,
        request: OrchestrationDefinitionCreate,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        nodes = self._validate_nodes(workspace_id, request.nodes)
        definition = OrchestrationDefinition(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            key=request.key,
            name=request.name,
            description=request.description,
            definition={"definition_version": 1, "nodes": nodes},
            version=1,
            status="draft",
        )
        self._session.add(definition)
        flush_or_raise_conflict(self._session, "Orchestration key already exists")
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.created",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            commit_or_raise_conflict(self._session, "Orchestration key already exists")
            self._session.refresh(definition)
        return definition

    def list_revisions(
        self,
        workspace_id: UUID,
        definition_id: UUID,
        page: PageParams,
    ) -> tuple[list[OrchestrationRevision], int]:
        if self.get_definition(workspace_id, definition_id) is None:
            raise OrchestrationDefinitionError(
                "Orchestration definition not found", code="orchestration_not_found"
            )
        return page_scalars(
            self._session,
            select(OrchestrationRevision)
            .where(
                OrchestrationRevision.workspace_id == workspace_id,
                OrchestrationRevision.definition_id == definition_id,
            )
            .order_by(OrchestrationRevision.version.desc()),
            page,
        )

    def get_revision(
        self,
        workspace_id: UUID,
        definition_id: UUID,
        version: int,
    ) -> OrchestrationRevision | None:
        return self._session.scalar(
            select(OrchestrationRevision).where(
                OrchestrationRevision.workspace_id == workspace_id,
                OrchestrationRevision.definition_id == definition_id,
                OrchestrationRevision.version == version,
            )
        )

    def update_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        request: OrchestrationDefinitionUpdate,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
        allow_locked_edits: bool = False,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        changes = request.model_dump(exclude_unset=True)
        if request.expected_version != definition.version:
            raise OrchestrationDefinitionError(
                "Definition changed; reload before editing", code="orchestration_version_mismatch"
            )
        next_nodes = request.nodes if request.nodes is not None else self._stored_nodes(definition)
        serialized_nodes = self._validate_nodes(workspace_id, next_nodes)
        self._enforce_edit_scope(
            before=self._stored_nodes(definition),
            after=next_nodes,
            edit_scope=request.edit_scope,
            allow_locked_edits=allow_locked_edits,
        )
        before_version = definition.version
        if request.name is not None:
            definition.name = request.name
        if request.description is not None:
            definition.description = request.description
        definition.definition = {
            "definition_version": 1,
            "nodes": serialized_nodes,
        }
        definition.version = before_version + 1
        definition.status = "draft"
        definition.published_at = None
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.updated",
            target_id=definition.id,
            metadata={
                "key": definition.key,
                "version": definition.version,
                "previous_version": before_version,
                "changed_fields": sorted(changes),
            },
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def _enforce_edit_scope(
        self,
        *,
        before: list[WorkflowNode],
        after: list[WorkflowNode],
        edit_scope: OrchestrationEditScope | None,
        allow_locked_edits: bool,
    ) -> None:
        before_by_id = {node.package_id: node for node in before}
        after_by_id = {node.package_id: node for node in after}
        locked_ids = {node.package_id for node in before if node.locked}
        changed_locked_nodes = {
            package_id
            for package_id in locked_ids
            if before_by_id[package_id] != after_by_id.get(package_id)
        }
        before_edges = self._workflow_edges(before)
        after_edges = self._workflow_edges(after)
        changed_incident_edges = {
            edge
            for edge in before_edges.symmetric_difference(after_edges)
            if edge[0] in locked_ids or edge[1] in locked_ids
        }
        if not changed_locked_nodes and not changed_incident_edges:
            return
        if not allow_locked_edits:
            raise OrchestrationDefinitionError(
                "Locked workflow regions require an authorized privileged editor",
                code="orchestration_locked_region",
            )
        scope = edit_scope
        authorized_nodes = set(scope.node_ids) if scope is not None else set()
        authorized_edges = set(scope.edge_ids) if scope is not None else set()
        missing_nodes = changed_locked_nodes - authorized_nodes
        missing_edges = {
            self._edge_key(source, target)
            for source, target in changed_incident_edges
        } - authorized_edges
        if missing_nodes or missing_edges:
            details: list[str] = []
            if missing_nodes:
                details.append("nodes=" + ",".join(sorted(missing_nodes)))
            if missing_edges:
                details.append("edges=" + ",".join(sorted(missing_edges)))
            raise OrchestrationDefinitionError(
                "Locked workflow changes fall outside the authorized edit scope: "
                + "; ".join(details),
                code="orchestration_locked_region",
            )

    @staticmethod
    def _workflow_edges(nodes: list[WorkflowNode]) -> set[tuple[str, str]]:
        edges: set[tuple[str, str]] = set()
        for node in nodes:
            for dependency in node.depends_on:
                edges.add((dependency, node.package_id))
            for dependency in condition_step_references(
                node.condition.model_dump(mode="json", by_alias=True, exclude_none=True)
                if node.condition is not None
                else None
            ):
                edges.add((dependency, node.package_id))
        return edges

    @staticmethod
    def _edge_key(source: str, target: str) -> str:
        return f"{source}->{target}"

    def validate_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> tuple[OrchestrationDefinition, list[str]]:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        try:
            self._validate_nodes(workspace_id, nodes_from_definition(definition))
        except OrchestrationDefinitionError as exc:
            return definition, [f"{exc.code}: {exc}"]
        return definition, []

    def publish_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
        allow_locked_edits: bool = False,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        self._validate_nodes(workspace_id, nodes_from_definition(definition))
        if definition.status == "archived":
            raise OrchestrationDefinitionError(
                "Edit the archived definition before publishing a new revision",
                code="orchestration_archived",
            )
        if definition.status == "published":
            return definition
        self._session.add(
            OrchestrationRevision(
                workspace_id=workspace_id,
                definition_id=definition.id,
                version=definition.version,
                name=definition.name,
                definition=deepcopy(definition.definition),
            )
        )
        definition.status = "published"
        definition.published_at = datetime.now(UTC)
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.published",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def archive_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> OrchestrationDefinition:
        definition = self._require_definition(workspace_id, orchestration_definition_id)
        definition.status = "archived"
        definition.published_at = None
        self._session.flush([definition])
        self._record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="orchestration.archived",
            target_id=definition.id,
            metadata={"key": definition.key, "version": definition.version},
        )
        if commit:
            self._session.commit()
            self._session.refresh(definition)
        return definition

    def _validate_nodes(
        self,
        workspace_id: UUID,
        nodes: list[WorkflowNode],
    ) -> list[dict[str, object]]:
        try:
            return self._validator.validate_nodes(workspace_id, nodes)
        except DefinitionValidationError as exc:
            raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc

    @staticmethod
    def _stored_nodes(
        definition: OrchestrationDefinition | OrchestrationRevision,
    ) -> list[WorkflowNode]:
        try:
            return nodes_from_definition(definition)
        except DefinitionValidationError as exc:
            raise OrchestrationDefinitionError(str(exc), code=exc.code) from exc

    def _require_definition(
        self,
        workspace_id: UUID,
        orchestration_definition_id: UUID,
    ) -> OrchestrationDefinition:
        definition = self._session.scalar(
            select(OrchestrationDefinition)
            .where(
                OrchestrationDefinition.workspace_id == workspace_id,
                OrchestrationDefinition.id == orchestration_definition_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if definition is None:
            raise OrchestrationDefinitionError(
                "Orchestration definition not found",
                code="orchestration_not_found",
            )
        return definition

    def _record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        target_id: UUID,
        metadata: dict[str, object],
        target_type: str | None = None,
    ) -> None:
        service = AuditService(self._session)
        resolved_target_type = target_type or (
            "task" if action.startswith("task.") else "orchestration_definition"
        )
        if actor_user_id is None:
            service.record_system_action(
                workspace_id=workspace_id,
                action=action,
                target_type=resolved_target_type,
                target_id=target_id,
                metadata=metadata,
            )
        else:
            service.record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action=action,
                target_type=resolved_target_type,
                target_id=target_id,
                metadata=metadata,
            )


__all__ = ["OrchestrationDefinitionError", "OrchestrationDefinitionService"]
