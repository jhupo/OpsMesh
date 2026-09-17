from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.agents.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEntry,
    WorkspaceMemoryVersion,
)
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.skills.models import WorkspaceSkillInstall
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.workspace.data_transfer.contracts import (
    SUPPORTED_WORKSPACE_EXPORT_FORMAT,
    WorkspaceExportManifest,
    WorkspaceExportRequest,
    WorkspaceExportResponse,
)
from backend.app.domains.workspace.data_transfer.serialization import (
    _agent_payload,
    _artifact_payload,
    _audit_payload,
    _file_payload,
    _memory_configuration_payload,
    _memory_entry_payload,
    _memory_version_payload,
    _project_configuration_version_payload,
    _project_file_payload,
    _project_output_payload,
    _project_payload,
    _run_event_payload,
    _run_payload,
    _runtime_space_payload,
    _runtime_space_quota_payload,
    _skill_install_payload,
    _task_message_payload,
    _task_payload,
    _task_step_payload,
    _team_member_payload,
    _team_payload,
    _workspace_payload,
)
from backend.app.domains.workspace.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.models import AuditEvent
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceQuota


class WorkspaceExportBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def build_export(
        self,
        *,
        workspace: Workspace,
        user_id: UUID,
        request: WorkspaceExportRequest,
    ) -> WorkspaceExportResponse:
        included: list[str] = []
        payload = self._build_payload(workspace, request, included)

        counts = {key: len(value) for key, value in payload.items()}
        workspace_payload = _workspace_payload(workspace)
        payload_checksum = sha256(
            json.dumps(
                {"workspace": workspace_payload, "collections": payload},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        export = WorkspaceExportResponse(
            manifest=WorkspaceExportManifest(
                workspace_id=workspace.id,
                exported_at=datetime.now(UTC),
                format_version=SUPPORTED_WORKSPACE_EXPORT_FORMAT,
                included_collections=included,
                counts=counts,
                payload_checksum_sha256=payload_checksum,
                dependency_graph={
                    "teams": ["agents"],
                    "team_members": ["teams", "agents"],
                    "tasks": ["agents", "teams"],
                    "task_steps": ["tasks", "agents"],
                    "task_messages": ["tasks", "runs"],
                    "runs": ["tasks", "agents"],
                    "project_configuration_versions": ["projects"],
                    "project_files": ["projects", "files"],
                    "project_outputs": ["projects"],
                    "memory_versions": ["memory_entries"],
                },
            ),
            workspace=workspace_payload,
            **payload,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=user_id,
            action="workspace.export.created",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "format_version": export.manifest.format_version,
                "included_collections": included,
                "counts": counts,
            },
        )
        self._session.commit()
        return export

    def _build_payload(
        self,
        workspace: Workspace,
        request: WorkspaceExportRequest,
        included: list[str],
    ) -> dict[str, list[dict[str, object]]]:
        payload: dict[str, list[dict[str, object]]] = {
            "agents": [],
            "teams": [],
            "team_members": [],
            "tasks": [],
            "task_steps": [],
            "task_messages": [],
            "runs": [],
            "run_events": [],
            "files": [],
            "artifacts": [],
            "runtime_spaces": [],
            "runtime_space_quotas": [],
            "skill_installs": [],
            "audit_events": [],
            "projects": [],
            "project_configuration_versions": [],
            "project_files": [],
            "project_outputs": [],
            "memory_entries": [],
            "memory_versions": [],
            "memory_configurations": [],
        }
        collection_specs = (
            ("agents", "include_agents", (("agents", AgentProfile, _agent_payload),)),
            (
                "teams",
                "include_teams",
                (
                    ("teams", AgentTeam, _team_payload),
                    ("team_members", AgentTeamMember, _team_member_payload),
                ),
            ),
            (
                "tasks",
                "include_tasks",
                (
                    ("tasks", Task, _task_payload),
                    ("task_steps", TaskStep, _task_step_payload),
                    ("task_messages", TaskMessage, _task_message_payload),
                ),
            ),
            (
                "runs",
                "include_runs",
                (("runs", AgentRun, _run_payload), ("run_events", RunEvent, _run_event_payload)),
            ),
            (
                "files",
                "include_files",
                (
                    ("files", WorkspaceFile, _file_payload),
                    ("artifacts", Artifact, _artifact_payload),
                ),
            ),
            (
                "runtime_spaces",
                "include_runtime_spaces",
                (
                    ("runtime_spaces", RuntimeSpace, _runtime_space_payload),
                    ("runtime_space_quotas", RuntimeSpaceQuota, _runtime_space_quota_payload),
                ),
            ),
            (
                "skill_installs",
                "include_skill_installs",
                (("skill_installs", WorkspaceSkillInstall, _skill_install_payload),),
            ),
            (
                "audit_events",
                "include_audit_events",
                (("audit_events", AuditEvent, _audit_payload),),
            ),
            (
                "projects",
                "include_projects",
                (
                    ("projects", WorkspaceProject, _project_payload),
                    (
                        "project_configuration_versions",
                        WorkspaceProjectConfigurationVersion,
                        _project_configuration_version_payload,
                    ),
                    ("project_files", WorkspaceProjectFile, _project_file_payload),
                    ("project_outputs", WorkspaceProjectOutput, _project_output_payload),
                ),
            ),
            (
                "memory",
                "include_memory",
                (
                    ("memory_entries", WorkspaceMemoryEntry, _memory_entry_payload),
                    ("memory_versions", WorkspaceMemoryVersion, _memory_version_payload),
                    (
                        "memory_configurations",
                        WorkspaceMemoryConfiguration,
                        _memory_configuration_payload,
                    ),
                ),
            ),
        )
        for collection, flag, rows in collection_specs:
            if not getattr(request, flag):
                continue
            included.append(collection)
            for key, model, serializer in rows:
                payload[key] = self._rows(
                    model,
                    workspace.id,
                    request.max_items_per_collection,
                    serializer,
                )
        return payload

    def _rows(
        self,
        model: type[Any],
        workspace_id: UUID,
        limit: int,
        serializer: Any,
    ) -> list[dict[str, object]]:
        rows = self._session.scalars(
            select(model).where(model.workspace_id == workspace_id).limit(limit)
        ).all()
        return [serializer(row) for row in rows]
