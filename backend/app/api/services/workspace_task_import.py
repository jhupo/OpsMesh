from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.services.workspace_import_conflicts import (
    _missing_dependency_conflict,
    _skip_conflict,
)
from backend.app.api.services.workspace_import_fields import (
    _dict_field,
    _int_field,
    _optional_dict_field,
    _optional_string_field,
    _string_field,
    _string_list_field,
    _uuid_or_none,
)
from backend.app.api.services.workspace_import_resolution import _resolved_import_name
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)
from backend.app.api.services.workspace_metadata_import_support import resolved_dependency_id
from backend.app.runtime_manager.spaces.models import RuntimeSpace
from backend.app.tasks.models import Task, TaskMessage, TaskStep


class TaskMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_tasks(self, ctx: WorkspaceMetadataImportContext) -> None:
        if ctx.request.import_tasks:
            for item in ctx.request.export.tasks[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_title = _resolved_import_name(
                    ctx.request,
                    collection="tasks",
                    source_id=source_id,
                    fallback=f"{ctx.request.name_prefix}{_string_field(item, 'title')}",
                )
                if self._task_exists(ctx.workspace.id, imported_title):
                    ctx.skipped_counts["tasks"] += 1
                    ctx.conflict_plan.append(
                        _skip_conflict(
                            collection="tasks",
                            source_id=source_id,
                            field="title",
                            source_value=_string_field(item, "title"),
                            target_value=imported_title,
                            message=f"Task {imported_title!r} already exists in target workspace.",
                        )
                    )
                    continue
                ctx.created_counts["tasks"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["tasks"][source_id] = source_id
                    continue
                team_id = ctx.id_map["teams"].get(_string_field(item, "agent_team_id"))
                runtime_space_id = ctx.id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                task = Task(
                    workspace_id=ctx.workspace.id,
                    created_by_user_id=ctx.user_id,
                    agent_team_id=_uuid_or_none(team_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    domain_type=_string_field(item, "domain_type", "general"),
                    title=imported_title,
                    description=_string_field(item, "description"),
                    status="draft",
                    priority=_int_field(item, "priority", 0),
                    input=_dict_field(item, "input"),
                    generic_state=_dict_field(item, "generic_state"),
                    domain_state=_dict_field(item, "domain_state"),
                    team_snapshot=_optional_dict_field(item, "team_snapshot"),
                    project_plan=_optional_dict_field(item, "project_plan"),
                    final_output=_optional_dict_field(item, "final_output"),
                )
                self._session.add(task)
                self._session.flush()
                ctx.id_map["tasks"][source_id] = str(task.id)

            for item in ctx.request.export.task_steps[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                task_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "task_id"),
                    dependency_field="task_id",
                    id_map=ctx.id_map["tasks"],
                    model=Task,
                )
                if task_id is None:
                    ctx.skipped_counts["task_steps"] += 1
                    ctx.warnings.append("Skipped task step with missing imported task")
                    ctx.conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="task_steps",
                            source_id=source_id,
                            dependency="task",
                            dependency_id=_string_field(item, "task_id"),
                        )
                    )
                    continue
                ctx.created_counts["task_steps"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["task_steps"][source_id] = source_id
                    continue
                agent_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "assigned_agent_profile_id"),
                    dependency_field="assigned_agent_profile_id",
                    id_map=ctx.id_map["agents"],
                    model=AgentProfile,
                )
                runtime_space_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_steps",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "runtime_space_id"),
                    dependency_field="runtime_space_id",
                    id_map=ctx.id_map["runtime_spaces"],
                    model=RuntimeSpace,
                )
                step = TaskStep(
                    workspace_id=ctx.workspace.id,
                    task_id=UUID(task_id),
                    assigned_agent_profile_id=_uuid_or_none(agent_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    work_package_id=_optional_string_field(item, "work_package_id"),
                    required_role=_optional_string_field(item, "required_role"),
                    required_skills=_string_list_field(item, "required_skills"),
                    expected_artifacts=_string_list_field(item, "expected_artifacts"),
                    acceptance_criteria=_string_list_field(item, "acceptance_criteria"),
                    review_policy=_dict_field(item, "review_policy"),
                    title=_string_field(item, "title"),
                    description=_string_field(item, "description"),
                    status="queued",
                    order_index=_int_field(item, "order_index", 0),
                    dependencies=_dict_field(item, "dependencies"),
                    result_summary=_optional_string_field(item, "result_summary"),
                )
                self._session.add(step)
                self._session.flush()
                ctx.id_map["task_steps"][source_id] = str(step.id)

            for item in ctx.request.export.task_messages[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                task_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "task_id"),
                    dependency_field="task_id",
                    id_map=ctx.id_map["tasks"],
                    model=Task,
                )
                if task_id is None:
                    ctx.skipped_counts["task_messages"] += 1
                    ctx.warnings.append("Skipped task message with missing imported task")
                    ctx.conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="task_messages",
                            source_id=source_id,
                            dependency="task",
                            dependency_id=_string_field(item, "task_id"),
                        )
                    )
                    continue
                ctx.created_counts["task_messages"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["task_messages"][source_id] = source_id
                    continue
                source_step_id = _string_field(item, "task_step_id")
                source_agent_id = _string_field(item, "agent_profile_id")
                step_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=source_step_id,
                    dependency_field="task_step_id",
                    id_map=ctx.id_map["task_steps"],
                    model=TaskStep,
                )
                agent_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="task_messages",
                    source_id=source_id,
                    source_dependency_id=source_agent_id,
                    dependency_field="agent_profile_id",
                    id_map=ctx.id_map["agents"],
                    model=AgentProfile,
                )
                message = TaskMessage(
                    workspace_id=ctx.workspace.id,
                    task_id=UUID(task_id),
                    task_step_id=_uuid_or_none(step_id),
                    agent_run_id=None,
                    agent_profile_id=_uuid_or_none(agent_id),
                    message_type=_string_field(item, "message_type", "note"),
                    sequence=_int_field(item, "sequence", 1),
                    body=_string_field(item, "body"),
                    payload=_dict_field(item, "payload"),
                )
                self._session.add(message)
                self._session.flush()
                ctx.id_map["task_messages"][source_id] = str(message.id)

    def _task_exists(self, workspace_id: UUID, title: str) -> bool:
        return self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.title == title)
        ) is not None
