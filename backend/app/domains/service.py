from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.domains import (
    DomainItemCreateRequest,
    DomainProjectCreateRequest,
    ReviewCommentCreateRequest,
    RevisionRequestCreateRequest,
)
from backend.app.db.base import Base
from backend.app.domains.models import (
    DomainItem,
    DomainProject,
    ReviewComment,
    RevisionRequest,
)
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue

T = TypeVar("T")


class DomainTaskService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def create_project(
        self,
        workspace_id: UUID,
        data: DomainProjectCreateRequest,
    ) -> DomainProject:
        if data.agent_team_id is not None:
            self._ensure_workspace_row("agent_teams", workspace_id, data.agent_team_id)
        project = DomainProject(workspace_id=workspace_id, **data.model_dump())
        self._session.add(project)
        self._session.commit()
        self._session.refresh(project)
        return project

    def list_projects(
        self,
        workspace_id: UUID,
        page: PageParams,
        domain_type: str | None = None,
    ) -> tuple[list[DomainProject], int]:
        statement = select(DomainProject).where(DomainProject.workspace_id == workspace_id)
        if domain_type is not None:
            statement = statement.where(DomainProject.domain_type == domain_type)
        return self._page(statement.order_by(DomainProject.created_at.desc()), page)

    def create_item(self, workspace_id: UUID, data: DomainItemCreateRequest) -> DomainItem:
        self._validate_item_references(workspace_id, data)
        item = DomainItem(workspace_id=workspace_id, **data.model_dump())
        self._session.add(item)
        self._session.commit()
        self._session.refresh(item)
        return item

    def list_items(
        self,
        workspace_id: UUID,
        page: PageParams,
        task_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> tuple[list[DomainItem], int]:
        statement = select(DomainItem).where(DomainItem.workspace_id == workspace_id)
        if task_id is not None:
            statement = statement.where(DomainItem.task_id == task_id)
        if project_id is not None:
            statement = statement.where(DomainItem.domain_project_id == project_id)
        statement = statement.order_by(DomainItem.order_index.asc(), DomainItem.created_at.asc())
        return self._page(statement, page)

    def task_view(self, workspace_id: UUID, task_id: UUID) -> tuple[
        Task,
        DomainProject | None,
        list[DomainItem],
        list[ReviewComment],
        list[RevisionRequest],
    ] | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        items = self._session.scalars(
            select(DomainItem)
            .where(DomainItem.workspace_id == workspace_id, DomainItem.task_id == task_id)
            .order_by(DomainItem.order_index.asc(), DomainItem.created_at.asc())
        ).all()
        domain_items = list(items)
        project_id = self._domain_project_id_from_items(domain_items)
        project = self._session.get(DomainProject, project_id) if project_id is not None else None
        comments = self._session.scalars(
            select(ReviewComment)
            .where(ReviewComment.workspace_id == workspace_id, ReviewComment.task_id == task_id)
            .order_by(ReviewComment.created_at.asc())
        ).all()
        revision_requests = self._session.scalars(
            select(RevisionRequest)
            .where(RevisionRequest.workspace_id == workspace_id, RevisionRequest.task_id == task_id)
            .order_by(RevisionRequest.created_at.asc())
        ).all()
        return task, project, domain_items, list(comments), list(revision_requests)

    def create_review_comment(
        self,
        workspace_id: UUID,
        task_id: UUID,
        author_user_id: UUID,
        data: ReviewCommentCreateRequest,
    ) -> ReviewComment | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        if data.domain_item_id is not None:
            self._ensure_domain_item(workspace_id, data.domain_item_id, task_id)
        comment = ReviewComment(
            workspace_id=workspace_id,
            task_id=task_id,
            author_user_id=author_user_id,
            domain_item_id=data.domain_item_id,
            body=data.body,
            metadata_=data.metadata,
        )
        self._session.add(comment)
        self._session.commit()
        self._session.refresh(comment)
        return comment

    def create_revision_request(
        self,
        workspace_id: UUID,
        task_id: UUID,
        requested_by_user_id: UUID,
        data: RevisionRequestCreateRequest,
    ) -> RevisionRequest | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        if data.domain_item_id is not None:
            self._ensure_domain_item(workspace_id, data.domain_item_id, task_id)
        if data.assigned_agent_profile_id is not None:
            self._ensure_workspace_row(
                "agent_profiles",
                workspace_id,
                data.assigned_agent_profile_id,
            )
        revision = RevisionRequest(
            workspace_id=workspace_id,
            task_id=task_id,
            requested_by_user_id=requested_by_user_id,
            **data.model_dump(),
        )
        self._session.add(revision)
        self._session.flush()
        if self._queue is not None:
            self._queue.enqueue(
                JobPayload(
                    workspace_id=workspace_id,
                    job_type=JobType.TASK_PLAN,
                    resource_id=task_id,
                    requested_by_user_id=requested_by_user_id,
                    idempotency_key=f"revision:{workspace_id}:{revision.id}",
                )
            )
        self._session.commit()
        self._session.refresh(revision)
        return revision

    def _validate_item_references(self, workspace_id: UUID, data: DomainItemCreateRequest) -> None:
        if data.domain_project_id is not None:
            self._ensure_workspace_row("domain_projects", workspace_id, data.domain_project_id)
        if data.task_id is not None:
            self._ensure_workspace_row("tasks", workspace_id, data.task_id)
        if data.parent_item_id is not None:
            self._ensure_workspace_row("domain_items", workspace_id, data.parent_item_id)

    def _ensure_domain_item(self, workspace_id: UUID, item_id: UUID, task_id: UUID) -> None:
        item = self._session.get(DomainItem, item_id)
        if item is None or item.workspace_id != workspace_id or item.task_id != task_id:
            raise ValueError("Domain item does not belong to the task")

    def _ensure_workspace_row(self, table_name: str, workspace_id: UUID, row_id: UUID) -> None:
        table = Base.metadata.tables[table_name]
        exists = self._session.scalar(
            select(func.count())
            .select_from(table)
            .where(table.c.id == row_id, table.c.workspace_id == workspace_id)
        )
        if not exists:
            raise ValueError(f"{table_name} row is not in workspace")

    def _domain_project_id_from_items(self, items: list[DomainItem]) -> UUID | None:
        for item in items:
            if item.domain_project_id is not None:
                return item.domain_project_id
        return None

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
