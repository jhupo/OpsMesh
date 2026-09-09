from uuid import UUID

from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeResourceGrant,
    AgentRuntimeToolResult,
)
from backend.app.agent_runtime.tool_arguments import (
    bool_argument,
    bytes_argument,
    dict_argument,
    int_argument,
    optional_int_argument,
    optional_str_argument,
    optional_str_set_argument,
    optional_uuid_argument,
    str_argument,
    str_list_argument,
    uuid_argument,
)
from backend.app.agent_runtime.tool_gateway import AgentToolGateway, ToolGatewayDenied
from backend.app.agent_runtime.tool_metadata import (
    agent_profile_id_for_context,
    product_review_context,
    tool_metadata,
)
from backend.app.agent_runtime.tool_payloads import (
    artifact_payload,
    memory_entry_payload,
    workspace_file_content_payload,
    workspace_file_payload,
)
from backend.app.approvals.policy import ApprovalPolicyDecision, ApprovalPolicyEngine
from backend.app.approvals.service import ApprovalService
from backend.app.approvals.waiting import ApprovalWaitingService
from backend.app.capabilities.product_tool_catalog import PRODUCT_TOOL_NAMES as PRODUCT_TOOL_NAMES
from backend.app.core.config import Settings
from backend.app.core.trace_context import current_trace_context, telemetry_span
from backend.app.files.storage import ObjectStorage, create_storage
from backend.app.memory.authorization import memory_read_scopes, memory_write_scopes
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.redaction import redact_sensitive_text
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools.service import ProductToolService

__all__ = ["PRODUCT_TOOL_NAMES", "ProductToolExecutor"]


class ProductToolExecutor:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        storage: ObjectStorage | None = None,
        secret_service: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._storage = storage or (create_storage(settings) if settings is not None else None)
        self._secret_service = secret_service

    def execute(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        resource_grants: tuple[AgentRuntimeResourceGrant, ...] = (),
        approval_granted: bool = False,
    ) -> AgentRuntimeToolResult:
        with telemetry_span(
            "opsmesh.product.tool.execute",
            parent=current_trace_context(),
            kind=SpanKind.INTERNAL,
            attributes={
                "opsmesh.workspace.id": str(context.workspace_id),
                "opsmesh.run.id": str(context.run_id),
                "opsmesh.tool.name": tool_name,
            },
        ):
            return self._execute(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
                resource_grants=resource_grants,
                approval_granted=approval_granted,
            )

    def _execute(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        resource_grants: tuple[AgentRuntimeResourceGrant, ...],
        approval_granted: bool,
    ) -> AgentRuntimeToolResult:
        policy_result = self._review_for_approval(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
            approval_granted=approval_granted,
        )
        if policy_result is not None:
            return policy_result
        product_context = ToolContext(
            workspace_id=context.workspace_id,
            agent_run_id=context.run_id,
            task_id=context.task_id,
            allowed_tools=frozenset(context.allowed_tools),
            metadata=dict(context.metadata),
        )
        try:
            output = _execute_product_tool(
                service=self._product_tool_service(),
                context=product_context,
                tool_name=tool_name,
                arguments=arguments,
                resource_grants=resource_grants,
                file_scope_ids=context.file_scope_ids,
            )
        except (ToolResourceNotFoundError, ValueError) as exc:
            error_code = getattr(exc, "code", "product_tool_failed")
            if tool_name == "read_workspace_file":
                if error_code == "product_tool_failed":
                    error_code = "workspace_file_not_found"
                AgentToolGateway(self._session).record_denial(
                    context=context,
                    tool_name=tool_name,
                    denial=ToolGatewayDenied(error_code, str(exc)),
                )
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": error_code,
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

    def _product_tool_service(self) -> ProductToolService:
        if self._settings is None:
            return ProductToolService(
                self._session,
                storage=self._storage,
                memory_embedding_secret_service=self._secret_service,
            )
        return ProductToolService(
            self._session,
            storage=self._storage,
            memory_embedding_secret_service=self._secret_service,
            max_file_read_bytes=self._settings.agent_file_read_max_bytes,
            readable_content_types=frozenset(
                self._settings.agent_file_read_content_types
            ),
        )

    def _review_for_approval(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        approval_granted: bool,
    ) -> AgentRuntimeToolResult | None:
        policy_decision = ApprovalPolicyEngine(
            self._session,
            self._settings,
        ).evaluate_product_tool(
            workspace_id=context.workspace_id,
            tool_name=tool_name,
            arguments=arguments,
            context=product_review_context(context),
        )
        if policy_decision.blocked:
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "product_tool_policy_denied",
                    "message": "Product tool invocation was denied by policy",
                },
                metadata=tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="product",
                    extra={
                        "policy_decision": policy_decision.decision.value,
                        "review_risk_level": policy_decision.risk_level,
                        "review_reasons": policy_decision.reasons,
                    },
                ),
            )
        if policy_decision.approved or approval_granted:
            return None
        self._request_approval(
            context=context,
            tool_name=tool_name,
            execution_review=policy_decision,
        )
        return AgentRuntimeToolResult(
            status="waiting_approval",
            error=None,
            metadata=tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="product",
                extra={
                    "review_risk_level": policy_decision.risk_level,
                    "review_reasons": policy_decision.reasons,
                },
            ),
        )

    def _request_approval(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        execution_review: ApprovalPolicyDecision,
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
        ApprovalWaitingService(self._session).mark_waiting(
            workspace_id=context.workspace_id, run_id=context.run_id, task_id=context.task_id,
        )
        self._session.commit()


def _execute_product_tool(
    *,
    service: ProductToolService,
    context: ToolContext,
    tool_name: str,
    arguments: dict[str, object],
    resource_grants: tuple[AgentRuntimeResourceGrant, ...],
    file_scope_ids: tuple[UUID, ...],
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
        source_types = optional_str_set_argument(arguments, "source_types")
        return {
            "items": service.search_workspace_memory(
                context,
                query=str_argument(arguments, "query", default=""),
                limit=int_argument(arguments, "limit", default=10),
                source_types=source_types,
                access_scopes=memory_read_scopes(
                    resource_grants,
                    requested_source_types=source_types,
                ),
            )
        }
    if tool_name == "upsert_semantic_memory":
        return memory_entry_payload(
            service.upsert_semantic_memory(
                context,
                scope_type=str_argument(arguments, "scope_type", default="workspace"),
                scope_id=uuid_argument(arguments, "scope_id"),
                memory_key=str_argument(arguments, "memory_key", default=""),
                knowledge_type=str_argument(arguments, "knowledge_type", default="fact"),
                title=str_argument(arguments, "title", default=""),
                content=str_argument(arguments, "content", default=""),
                tags=str_list_argument(arguments, "tags"),
                importance=int_argument(arguments, "importance", default=50),
                metadata=dict_argument(arguments, "metadata"),
                expected_revision=optional_int_argument(arguments, "expected_revision"),
                change_reason=optional_str_argument(arguments, "change_reason"),
                access_scopes=memory_write_scopes(resource_grants),
            )
        )
    if tool_name == "archive_semantic_memory":
        return memory_entry_payload(
            service.archive_semantic_memory(
                context,
                memory_entry_id=uuid_argument(arguments, "memory_entry_id"),
                expected_revision=int_argument(arguments, "expected_revision", default=0),
                change_reason=optional_str_argument(arguments, "change_reason"),
                access_scopes=memory_write_scopes(resource_grants),
            )
        )
    if tool_name == "promote_working_memory":
        return memory_entry_payload(
            service.promote_working_memory(
                context,
                working_memory_entry_id=uuid_argument(
                    arguments,
                    "working_memory_entry_id",
                ),
            )
        )
    if tool_name == "list_workspace_files":
        return {
            "items": [
                workspace_file_payload(file)
                for file in service.list_workspace_files(
                    context,
                    allowed_file_ids=_file_ids(resource_grants, file_scope_ids),
                )
            ]
        }
    if tool_name == "read_workspace_file":
        return workspace_file_content_payload(
            service.read_workspace_file(
                context,
                file_id=uuid_argument(arguments, "file_id"),
                allowed_file_ids=_file_ids(resource_grants, file_scope_ids),
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


def _file_ids(
    grants: tuple[AgentRuntimeResourceGrant, ...],
    file_scope_ids: tuple[UUID, ...],
) -> set[UUID]:
    ids: set[UUID] = set()
    for grant in grants:
        for raw_id in _resource_locator_strings(grant, "file_ids"):
            try:
                ids.add(UUID(str(raw_id)))
            except (TypeError, ValueError):
                continue
    return ids & set(file_scope_ids) if file_scope_ids else ids


def _resource_locator_strings(
    grant: AgentRuntimeResourceGrant,
    key: str,
) -> tuple[str, ...]:
    value = grant.locator.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
