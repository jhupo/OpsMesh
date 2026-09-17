from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.agents.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEntry,
    WorkspaceMemoryVersion,
    memory_content_fingerprint,
)
from backend.app.domains.workspace.data_transfer.contracts import WorkspaceImportConflict
from backend.app.domains.workspace.data_transfer.importers.context import (
    WorkspaceMetadataImportContext,
    _dict_field,
    _int_field,
    _optional_string_field,
    _string_field,
)


class WorkspaceMemoryMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_memory(self, context: WorkspaceMetadataImportContext) -> None:
        if not context.request.import_memory:
            return
        source_config = context.request.export.memory_configurations[:1]
        if source_config and self._session.scalar(
            select(WorkspaceMemoryConfiguration.id).where(
                WorkspaceMemoryConfiguration.workspace_id == context.workspace.id
            )
        ) is None:
            item = source_config[0]
            self._session.add(
                WorkspaceMemoryConfiguration(
                    id=uuid4(),
                    workspace_id=context.workspace.id,
                    embedding_enabled=False,
                    embedding_model=_string_field(
                        item, "embedding_model", "text-embedding-3-small"
                    ),
                    embedding_dimensions=max(_int_field(item, "embedding_dimensions", 1536), 1),
                    retrieval_policy=_dict_field(item, "retrieval_policy"),
                    lifecycle_policy=_dict_field(item, "lifecycle_policy"),
                    version=max(_int_field(item, "version", 1), 1),
                    updated_by_user_id=context.user_id,
                )
            )
            context.created_counts["memory_configurations"] += 1

        for item in context.request.export.memory_entries[
            : context.request.max_items_per_collection
        ]:
            scope_type = _string_field(item, "scope_type", "workspace")
            source_scope_id = _string_field(item, "scope_id")
            scope_id = self._mapped_scope(context, scope_type, source_scope_id)
            if scope_id is None:
                context.skipped_counts["memory_entries"] += 1
                context.warnings.append(
                    f"Skipped memory entry {_string_field(item, 'id')}: "
                    "scope requires explicit remapping"
                )
                continue
            source_id = _string_field(item, "id")
            memory_key = _optional_string_field(item, "memory_key")
            if memory_key and self._session.scalar(
                select(WorkspaceMemoryEntry.id).where(
                    WorkspaceMemoryEntry.workspace_id == context.workspace.id,
                    WorkspaceMemoryEntry.memory_layer
                    == _string_field(item, "memory_layer", "semantic"),
                    WorkspaceMemoryEntry.scope_type == scope_type,
                    WorkspaceMemoryEntry.scope_id == scope_id,
                    WorkspaceMemoryEntry.memory_key == memory_key,
                )
            ) is not None:
                context.skipped_counts["memory_entries"] += 1
                context.conflict_plan.append(
                    WorkspaceImportConflict(
                        collection="memory_entries",
                        source_id=source_id,
                        field="memory_key",
                        source_value=memory_key,
                        strategy="reject",
                        severity="error",
                        message=(
                            f"Memory key {memory_key!r} already exists in the target "
                            "scope; import requires an explicit source or target change."
                        ),
                    )
                )
                continue
            source_type = _optional_string_field(item, "source_type")
            source_reference = self._mapped_source_reference(
                context,
                source_type,
                _optional_string_field(item, "source_id"),
            )
            entry = WorkspaceMemoryEntry(
                id=uuid4(),
                workspace_id=context.workspace.id,
                created_by_user_id=context.user_id,
                created_by_agent_profile_id=self._mapped_uuid(
                    context.id_map["agents"].get(
                        _optional_string_field(item, "created_by_agent_profile_id") or ""
                    )
                ),
                created_by_agent_run_id=None,
                source_type=_optional_string_field(item, "source_type"),
                source_id=source_reference,
                memory_layer=_string_field(item, "memory_layer", "semantic"),
                scope_type=scope_type,
                scope_id=scope_id,
                memory_key=_optional_string_field(item, "memory_key"),
                entry_type=_string_field(item, "entry_type", "note"),
                title=_string_field(item, "title", "Imported memory"),
                content=_string_field(item, "content"),
                tags=item.get("tags") if isinstance(item.get("tags"), list) else [],
                visibility_scope=_string_field(item, "visibility_scope", "workspace"),
                importance=_int_field(item, "importance", 0),
                status=_string_field(item, "status", "active"),
                revision=max(_int_field(item, "revision", 1), 1),
                content_fingerprint=_string_field(item, "content_fingerprint")
                or memory_content_fingerprint(
                    _string_field(item, "title", "Imported memory"),
                    _string_field(item, "content"),
                ),
                memory_metadata=_dict_field(item, "metadata"),
                last_accessed_at=_datetime(_optional_string_field(item, "last_accessed_at")),
                expires_at=_datetime(_optional_string_field(item, "expires_at")),
                archived_at=_datetime(_optional_string_field(item, "archived_at")),
                access_count=max(_int_field(item, "access_count", 0), 0),
                embedding=None,
                embedding_model=_optional_string_field(item, "embedding_model"),
                embedding_status="not_applicable",
                embedding_generation=max(_int_field(item, "embedding_generation", 0), 0),
                embedding_attempts=0,
            )
            self._session.add(entry)
            context.id_map["memory_entries"][source_id] = str(entry.id)
            context.created_counts["memory_entries"] += 1
            if _string_field(item, "embedding_status") == "ready":
                context.warnings.append(
                    f"Memory entry {source_id} requires embedding rebuild after import"
                )

        for item in context.request.export.memory_versions[
            : context.request.max_items_per_collection
        ]:
            entry_id = context.id_map["memory_entries"].get(_string_field(item, "memory_entry_id"))
            if entry_id is None:
                continue
            self._session.add(
                WorkspaceMemoryVersion(
                    id=uuid4(),
                    workspace_id=context.workspace.id,
                    memory_entry_id=UUID(entry_id),
                    revision=max(_int_field(item, "revision", 1), 1),
                    snapshot=_dict_field(item, "snapshot"),
                    content_fingerprint=_string_field(item, "content_fingerprint"),
                    changed_by_user_id=context.user_id,
                    change_reason=_optional_string_field(item, "change_reason"),
                )
            )
            context.created_counts["memory_versions"] += 1

    @staticmethod
    def _mapped_uuid(value: str | None) -> UUID | None:
        return UUID(value) if value else None

    @staticmethod
    def _mapped_scope(
        context: WorkspaceMetadataImportContext,
        scope_type: str,
        source_scope_id: str,
    ) -> str | None:
        if scope_type == "workspace":
            return str(context.workspace.id)
        collection = {
            "agent": "agents",
            "team": "teams",
            "task": "tasks",
        }.get(scope_type)
        if collection is None:
            return None
        return context.id_map[collection].get(source_scope_id)

    @staticmethod
    def _mapped_source_reference(
        context: WorkspaceMetadataImportContext,
        source_type: str | None,
        source_id: str | None,
    ) -> str | None:
        if not source_id:
            return None
        collection = {
            "agent": "agents",
            "agent_profile": "agents",
            "team": "teams",
            "task": "tasks",
            "workspace_file": "files",
            "artifact": "artifacts",
        }.get(source_type or "")
        if collection is None:
            return None
        return context.id_map.get(collection, {}).get(source_id)


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
