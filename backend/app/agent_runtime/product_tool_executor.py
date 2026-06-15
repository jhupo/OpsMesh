from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.agent_runtime.tool_arguments import (
    bool_argument,
    bytes_argument,
    dict_argument,
    int_argument,
    optional_str_argument,
    optional_str_set_argument,
    optional_uuid_argument,
    str_argument,
    str_list_argument,
    uuid_argument,
)
from backend.app.agent_runtime.tool_metadata import (
    agent_profile_id_for_context,
    product_review_context,
    tool_metadata,
)
from backend.app.agent_runtime.tool_payloads import (
    artifact_payload,
    memory_entry_payload,
    workspace_file_payload,
)
from backend.app.approvals.service import ApprovalService
from backend.app.core.config import Settings
from backend.app.reviews.tool_execution import ToolExecutionReview, ToolExecutionReviewService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.security.redaction import redact_sensitive_text
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools import ProductToolService

PRODUCT_TOOL_NAMES = frozenset(
    {
        "archive_workspace_memory",
        "get_agent_inbox",
        "list_workspace_files",
        "mark_agent_message_read",
        "read_workspace_file",
        "remember_workspace_memory",
        "search_workspace_memory",
        "send_agent_message",
        "list_agent_thread_messages",
        "write_artifact",
    }
)


class ProductToolExecutor:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings

    def execute(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        execution_review = ToolExecutionReviewService(
            self._session,
            self._settings,
        ).review_product_tool_call(
            workspace_id=context.workspace_id,
            tool_name=tool_name,
            arguments=arguments,
            context=product_review_context(context),
        )
        if not execution_review.approved:
            self._request_approval(
                context=context,
                tool_name=tool_name,
                execution_review=execution_review,
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                error=None,
                metadata=tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="product",
                    extra={
                        "review_risk_level": execution_review.risk_level,
                        "review_reasons": execution_review.reasons,
                    },
                ),
            )
        product_context = ToolContext(
            workspace_id=context.workspace_id,
            agent_run_id=context.run_id,
            task_id=context.task_id,
            allowed_tools=frozenset(context.allowed_tools),
            metadata=dict(context.metadata),
        )
        try:
            output = _execute_product_tool(
                service=ProductToolService(self._session),
                context=product_context,
                tool_name=tool_name,
                arguments=arguments,
            )
        except (ToolResourceNotFoundError, ValueError) as exc:
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "product_tool_failed",
                    "message": redact_sensitive_text(str(exc)),
                },
                metadata=tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="product",
                ),
            )
        self._session.commit()
        return AgentRuntimeToolResult(
            status="completed",
            output=output,
            metadata=tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="product",
            ),
        )

    def _request_approval(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        execution_review: ToolExecutionReview,
    ) -> None:
        ApprovalService(self._session).create_approval(
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.run_id,
            requested_by_agent_profile_id=agent_profile_id_for_context(
                self._session,
                context,
            ),
            approval_type="product.tool",
            risk_level=execution_review.risk_level,
            payload={
                "tool_name": tool_name,
                "reason": "product_tool_execution_review_requires_approval",
                "execution_review": execution_review.approval_payload(),
            },
        )
        run = self._session.get(AgentRun, context.run_id)
        if run is not None and run.workspace_id == context.workspace_id:
            run.status = RunStatus.WAITING_APPROVAL.value
        if context.task_id is not None:
            task = self._session.get(Task, context.task_id)
            if (
                task is not None
                and task.workspace_id == context.workspace_id
                and task.status == TaskStatus.RUNNING.value
            ):
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
        self._session.commit()


def _execute_product_tool(
    *,
    service: ProductToolService,
    context: ToolContext,
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    if tool_name == "send_agent_message":
        return service.send_agent_message(
            context,
            recipient_agent_profile_id=uuid_argument(arguments, "recipient_agent_profile_id"),
            body=str_argument(arguments, "body", default=""),
            thread_id=optional_uuid_argument(arguments, "thread_id"),
            subject=optional_str_argument(arguments, "subject"),
            message_type=str_argument(arguments, "message_type", default="message"),
            payload=dict_argument(arguments, "payload"),
            reply_to_message_id=optional_uuid_argument(arguments, "reply_to_message_id"),
        )
    if tool_name == "list_agent_thread_messages":
        return service.list_agent_thread_messages(
            context,
            thread_id=uuid_argument(arguments, "thread_id"),
            limit=int_argument(arguments, "limit", default=50),
            offset=int_argument(arguments, "offset", default=0),
            status=optional_str_argument(arguments, "status"),
        )
    if tool_name == "get_agent_inbox":
        return service.get_agent_inbox(
            context,
            latest_limit=int_argument(arguments, "latest_limit", default=20),
            unread_only=bool_argument(arguments, "unread_only", default=False),
        )
    if tool_name == "mark_agent_message_read":
        return service.mark_agent_message_read(
            context,
            message_id=uuid_argument(arguments, "message_id"),
        )
    if tool_name == "search_workspace_memory":
        return {
            "items": service.search_workspace_memory(
                context,
                query=str_argument(arguments, "query", default=""),
                limit=int_argument(arguments, "limit", default=10),
                source_types=optional_str_set_argument(arguments, "source_types"),
            )
        }
    if tool_name == "remember_workspace_memory":
        return memory_entry_payload(
            service.remember_workspace_memory(
                context,
                title=str_argument(arguments, "title", default="Untitled memory"),
                content=str_argument(arguments, "content", default=""),
                entry_type=str_argument(arguments, "entry_type", default="note"),
                tags=str_list_argument(arguments, "tags"),
                source_type=optional_str_argument(arguments, "source_type"),
                source_id=optional_str_argument(arguments, "source_id"),
                visibility_scope=str_argument(
                    arguments,
                    "visibility_scope",
                    default="workspace",
                ),
                importance=int_argument(arguments, "importance", default=0),
                metadata=dict_argument(arguments, "metadata"),
            )
        )
    if tool_name == "archive_workspace_memory":
        return memory_entry_payload(
            service.archive_workspace_memory(
                context,
                memory_entry_id=uuid_argument(arguments, "memory_entry_id"),
            )
        )
    if tool_name == "list_workspace_files":
        return {
            "items": [
                workspace_file_payload(file) for file in service.list_workspace_files(context)
            ]
        }
    if tool_name == "read_workspace_file":
        return workspace_file_payload(
            service.read_workspace_file(
                context,
                file_id=uuid_argument(arguments, "file_id"),
            )
        )
    if tool_name == "write_artifact":
        return artifact_payload(
            service.write_artifact(
                context,
                filename=str_argument(arguments, "filename", default="artifact.txt"),
                content=bytes_argument(arguments, "content"),
                content_type=str_argument(arguments, "content_type", default="text/plain"),
                artifact_type=str_argument(arguments, "artifact_type", default="file"),
            )
        )
    raise ValueError(f"Unsupported product tool: {tool_name}")
