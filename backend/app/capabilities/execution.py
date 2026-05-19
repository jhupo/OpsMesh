from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.approvals.service import ApprovalService
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
)
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task, TaskMessage
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tools.errors import ToolPermissionError, ToolResourceNotFoundError


class McpExecutionError(Exception):
    def __init__(self, message: str, *, code: str = "mcp_execution_failed") -> None:
        super().__init__(message)
        self.code = code


class McpToolAdapter(Protocol):
    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        """Execute an MCP tool and return a JSON-serializable response."""


@runtime_checkable
class McpToolAdapterResolver(Protocol):
    def resolve(self, server: McpServer) -> McpToolAdapter: ...


class UnconfiguredMcpToolAdapter:
    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        raise McpExecutionError(
            "MCP protocol adapter is not configured",
            code="mcp_adapter_unconfigured",
        )


@dataclass(frozen=True)
class McpExecutionRequest:
    workspace_id: UUID
    agent_run_id: UUID
    tool_name: str
    arguments: dict[str, object]
    mcp_server_id: UUID | None = None
    runtime_allowed_tools: tuple[str, ...] | None = None


@dataclass(frozen=True)
class McpExecutionResult:
    status: str
    response: dict[str, object] | None
    error: dict[str, object] | None
    log_id: UUID
    latency_ms: int


class McpToolExecutionService:
    def __init__(
        self,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
    ) -> None:
        self._session = session
        self._adapter_or_resolver = adapter

    def execute(self, request: McpExecutionRequest) -> McpExecutionResult:
        run = self._require_run(request.workspace_id, request.agent_run_id)
        snapshot = _authorization_snapshot(run)
        self._validate_snapshot_scope(snapshot, request)
        self._require_runtime_context_tool(request)
        allow, server = self._resolve_allowed_tool(request)
        self._require_snapshot_tool(snapshot, request)
        policy_decision = PlatformPolicyService(self._session).risky_execution_policy()
        if _is_high_risk_tool(allow) and policy_decision.high_risk_tool_mode == "block":
            self._block(request, "mcp_high_risk_tool_globally_disabled")
        if allow.requires_approval:
            return self._request_tool_approval(
                request,
                run,
                allow,
                server,
                reason="mcp_tool_requires_approval",
            )
        if (
            _is_high_risk_tool(allow)
            and policy_decision.high_risk_tool_mode == "require_workspace_approval"
        ):
            return self._request_tool_approval(
                request,
                run,
                allow,
                server,
                reason="mcp_high_risk_tool_requires_approval",
            )
        policy = _mcp_policy(snapshot, allow)

        self._append_run_event(
            run=run,
            event_type="tool.called",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "request_sha256": _payload_hash(request.arguments),
                **_snapshot_audit_metadata(snapshot),
            },
        )
        started = monotonic()
        try:
            self._enforce_payload_size(request.arguments, policy.max_input_bytes)
            credential_refs = self._credential_refs(request.workspace_id, server.id)
            response = self._adapter_for(server).call(
                server=server,
                tool_name=request.tool_name,
                arguments=request.arguments,
                credential_refs=credential_refs,
                timeout_seconds=policy.timeout_seconds,
            )
            self._enforce_payload_size(response, policy.max_output_bytes)
        except ToolPermissionError:
            raise
        except Exception as exc:
            latency_ms = _latency_ms(started)
            error = _normalized_error(exc)
            log = self._log_call(
                request=request,
                server_id=server.id,
                status="failed",
                response=None,
                error={**error, "latency_ms": latency_ms},
                snapshot=snapshot,
            )
            self._append_run_event(
                run=run,
                event_type="tool.failed",
                message=request.tool_name,
                metadata={"tool_kind": "mcp", "error": error, "latency_ms": latency_ms},
            )
            self._append_task_message(
                run=run,
                message_type="tool.failed",
                body=f"MCP tool failed: {request.tool_name}",
                payload={
                    "tool_name": request.tool_name,
                    "mcp_server_id": str(server.id),
                    "error": error,
                    "latency_ms": latency_ms,
                },
            )
            self._session.flush()
            return McpExecutionResult(
                status="failed",
                response=None,
                error=error,
                log_id=log.id,
                latency_ms=latency_ms,
            )

        latency_ms = _latency_ms(started)
        log = self._log_call(
            request=request,
            server_id=server.id,
            status="completed",
            response={"result": response, "latency_ms": latency_ms},
            error=None,
            snapshot=snapshot,
        )
        self._append_run_event(
            run=run,
            event_type="tool.completed",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "response_sha256": _payload_hash(response),
                "latency_ms": latency_ms,
                **_snapshot_audit_metadata(snapshot),
            },
        )
        self._append_task_message(
            run=run,
            message_type="tool.completed",
            body=f"MCP tool completed: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "latency_ms": latency_ms,
                "response_sha256": _payload_hash(response),
            },
        )
        self._session.flush()
        return McpExecutionResult(
            status="completed",
            response=response,
            error=None,
            log_id=log.id,
            latency_ms=latency_ms,
        )

    def _require_run(self, workspace_id: UUID, run_id: UUID) -> AgentRun:
        run = self._session.get(AgentRun, run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ToolResourceNotFoundError("Agent run not found")
        return run

    def _validate_snapshot_scope(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> None:
        if snapshot.get("workspace_id") not in (None, str(request.workspace_id)):
            self._block(request, "authorization_snapshot_workspace_mismatch")
        if snapshot.get("agent_run_id") not in (None, str(request.agent_run_id)):
            self._block(request, "authorization_snapshot_run_mismatch")

    def _require_runtime_context_tool(self, request: McpExecutionRequest) -> None:
        if request.runtime_allowed_tools is None:
            return
        if request.tool_name in request.runtime_allowed_tools:
            return
        self._block(request, "mcp_tool_not_in_runtime_context")

    def _resolve_allowed_tool(
        self,
        request: McpExecutionRequest,
    ) -> tuple[McpToolAllowlist, McpServer]:
        statement = (
            select(McpToolAllowlist, McpServer)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == request.workspace_id,
                McpToolAllowlist.tool_name == request.tool_name,
                McpToolAllowlist.status == "active",
                McpServer.workspace_id == request.workspace_id,
                McpServer.status == "active",
            )
        )
        if request.mcp_server_id is not None:
            statement = statement.where(McpServer.id == request.mcp_server_id)
        row = self._session.execute(statement).first()
        if row is None:
            self._block(request, "mcp_tool_not_allowed")
            raise AssertionError("unreachable")
        return row[0], row[1]

    def _require_snapshot_tool(
        self,
        snapshot: dict[str, object],
        request: McpExecutionRequest,
    ) -> None:
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list) and all(isinstance(tool, str) for tool in raw_tools):
            if request.tool_name in raw_tools:
                return
            self._block(request, "mcp_tool_not_in_run_snapshot")
        self._block(request, "mcp_tool_snapshot_missing")

    def _enforce_payload_size(self, payload: dict[str, object], max_bytes: int) -> None:
        size = len(_canonical_payload(payload).encode("utf-8"))
        if size > max_bytes:
            raise McpExecutionError(
                "MCP payload exceeds configured size limit",
                code="mcp_payload_too_large",
            )

    def _credential_refs(
        self,
        workspace_id: UUID,
        server_id: UUID,
    ) -> list[McpCredentialReference]:
        return list(
            self._session.scalars(
                select(McpCredentialReference)
                .where(
                    McpCredentialReference.workspace_id == workspace_id,
                    McpCredentialReference.status == "active",
                    or_(
                        McpCredentialReference.mcp_server_id == server_id,
                        McpCredentialReference.mcp_server_id.is_(None),
                    ),
                )
                .order_by(McpCredentialReference.created_at.asc())
            )
        )

    def _log_call(
        self,
        *,
        request: McpExecutionRequest,
        server_id: UUID,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        snapshot: dict[str, object] | None = None,
    ) -> McpToolCallLog:
        log = McpToolCallLog(
            workspace_id=request.workspace_id,
            mcp_server_id=server_id,
            agent_run_id=request.agent_run_id,
            tool_name=request.tool_name,
            status=status,
            request={
                "arguments_sha256": _payload_hash(request.arguments),
                "argument_bytes": len(_canonical_payload(request.arguments).encode("utf-8")),
                **_snapshot_audit_metadata(snapshot or {}),
            },
            response=response,
            error=error,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        self._session.flush()
        return log

    def _request_tool_approval(
        self,
        request: McpExecutionRequest,
        run: AgentRun,
        allow: McpToolAllowlist,
        server: McpServer,
        *,
        reason: str,
    ) -> McpExecutionResult:
        snapshot = _authorization_snapshot(run)
        log = self._log_call(
            request=request,
            server_id=server.id,
            status="waiting_approval",
            response=None,
            error=None,
            snapshot=snapshot,
        )
        ApprovalService(self._session).create_approval(
            workspace_id=request.workspace_id,
            task_id=run.task_id,
            agent_run_id=run.id,
            requested_by_agent_profile_id=run.agent_profile_id,
            approval_type="mcp.tool",
            risk_level=allow.risk_level,
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "arguments_sha256": _payload_hash(request.arguments),
                "reason": reason,
                "requires_approval": allow.requires_approval,
                **_snapshot_audit_metadata(snapshot),
            },
        )
        run.status = RunStatus.WAITING_APPROVAL.value
        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None and task.status == TaskStatus.RUNNING.value:
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
        self._append_run_event(
            run=run,
            event_type="approval.requested",
            message=request.tool_name,
            metadata={
                "tool_kind": "mcp",
                "mcp_server_id": str(server.id),
                "tool_name": request.tool_name,
                "risk_level": allow.risk_level,
                "reason": reason,
                "requires_approval": allow.requires_approval,
                **_snapshot_audit_metadata(snapshot),
            },
        )
        self._append_task_message(
            run=run,
            message_type="approval.requested",
            body=f"MCP tool requires approval: {request.tool_name}",
            payload={
                "tool_name": request.tool_name,
                "mcp_server_id": str(server.id),
                "risk_level": allow.risk_level,
                "reason": reason,
                "requires_approval": allow.requires_approval,
            },
        )
        self._session.flush()
        return McpExecutionResult(
            status="waiting_approval",
            response=None,
            error=None,
            log_id=log.id,
            latency_ms=0,
        )

    def _append_run_event(
        self,
        *,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        sequence = _next_run_event_sequence(self._session, run.workspace_id, run.id)
        self._session.add(
            RunEvent(
                workspace_id=run.workspace_id,
                agent_run_id=run.id,
                event_type=event_type,
                sequence=sequence,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _append_task_message(
        self,
        *,
        run: AgentRun,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> None:
        if run.task_id is None:
            return
        sequence = _next_task_message_sequence(self._session, run.workspace_id, run.task_id)
        self._session.add(
            TaskMessage(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                task_step_id=run.task_step_id,
                agent_run_id=run.id,
                agent_profile_id=run.agent_profile_id,
                message_type=message_type,
                sequence=sequence,
                body=body,
                payload=payload,
            )
        )

    def _block(self, request: McpExecutionRequest, reason: str) -> None:
        run = self._session.get(AgentRun, request.agent_run_id)
        snapshot = (
            _authorization_snapshot(run)
            if run is not None and run.workspace_id == request.workspace_id
            else {}
        )
        error = {"code": reason, "message": "MCP tool invocation was blocked by policy"}
        if run is not None and run.workspace_id == request.workspace_id:
            self._append_run_event(
                run=run,
                event_type="tool.blocked",
                message=request.tool_name,
                metadata={
                    "tool_kind": "mcp",
                    "mcp_server_id": str(request.mcp_server_id)
                    if request.mcp_server_id is not None
                    else None,
                    "tool_name": request.tool_name,
                    "reason": reason,
                    **_snapshot_audit_metadata(snapshot),
                },
            )
            self._append_task_message(
                run=run,
                message_type="tool.blocked",
                body=f"MCP tool blocked: {request.tool_name}",
                payload={
                    "tool_name": request.tool_name,
                    "mcp_server_id": str(request.mcp_server_id)
                    if request.mcp_server_id is not None
                    else None,
                    "reason": reason,
                },
            )
        self._session.add(
            McpToolCallLog(
                workspace_id=request.workspace_id,
                mcp_server_id=request.mcp_server_id,
                agent_run_id=request.agent_run_id,
                tool_name=request.tool_name,
                status="blocked",
                request={
                    "arguments_sha256": _payload_hash(request.arguments),
                    "argument_bytes": len(_canonical_payload(request.arguments).encode("utf-8")),
                    **_snapshot_audit_metadata(snapshot),
                },
                response=None,
                error=error,
                created_at=datetime.now(UTC),
            )
        )
        self._session.add(
            SecurityEvent(
                workspace_id=request.workspace_id,
                user_id=None,
                action="mcp_tool.blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="internal:mcp_tool_execution",
                method="WORKER",
                reason=reason,
                event_metadata={
                    "agent_run_id": str(request.agent_run_id),
                    "mcp_server_id": str(request.mcp_server_id)
                    if request.mcp_server_id is not None
                    else None,
                    "tool_name": request.tool_name,
                    **_snapshot_audit_metadata(snapshot),
                },
                created_at=datetime.now(UTC),
            )
        )
        self._session.flush()
        raise ToolPermissionError(f"MCP tool blocked: {reason}")

    def _adapter_for(self, server: McpServer) -> McpToolAdapter:
        if isinstance(self._adapter_or_resolver, McpToolAdapterResolver):
            adapter = self._adapter_or_resolver.resolve(server)
            if not hasattr(adapter, "call"):
                raise McpExecutionError(
                    "MCP adapter resolver returned an invalid adapter",
                    code="mcp_adapter_invalid",
                )
            return adapter
        return self._adapter_or_resolver


@dataclass(frozen=True)
class _McpPolicy:
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int


def _authorization_snapshot(run: AgentRun) -> dict[str, object]:
    run_input = run.input if isinstance(run.input, dict) else {}
    snapshot = run_input.get("authorization_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _snapshot_audit_metadata(snapshot: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    version = snapshot.get("version")
    if isinstance(version, int):
        metadata["authorization_snapshot_version"] = version
    for key in ("workspace_id", "task_id", "task_step_id", "agent_profile_id", "runtime_space_id"):
        value = snapshot.get(key)
        if value is None or isinstance(value, str):
            metadata[f"snapshot_{key}"] = value
    installed_skills = snapshot.get("installed_skills")
    if isinstance(installed_skills, list):
        skill_refs: list[dict[str, object]] = []
        for item in installed_skills:
            if not isinstance(item, dict):
                continue
            skill_refs.append(
                {
                    "install_id": item.get("install_id"),
                    "installed_key": item.get("installed_key"),
                    "installed_version": item.get("installed_version"),
                    "source_checksum": item.get("source_checksum"),
                    "source_visibility": item.get("source_visibility"),
                }
            )
        metadata["snapshot_installed_skills"] = skill_refs
    raw_tools = snapshot.get("allowed_tools")
    if isinstance(raw_tools, list):
        metadata["snapshot_allowed_tools"] = [tool for tool in raw_tools if isinstance(tool, str)]
    return metadata


def _mcp_policy(snapshot: dict[str, object], allow: McpToolAllowlist) -> _McpPolicy:
    runtime_policy = snapshot.get("runtime_policy")
    snapshot_mcp_policy = runtime_policy.get("mcp") if isinstance(runtime_policy, dict) else None
    allow_policy = allow.policy if isinstance(allow.policy, dict) else {}
    return _McpPolicy(
        timeout_seconds=_int_policy(allow_policy, snapshot_mcp_policy, "timeout_seconds", 30),
        max_input_bytes=_int_policy(allow_policy, snapshot_mcp_policy, "max_input_bytes", 64_000),
        max_output_bytes=_int_policy(
            allow_policy,
            snapshot_mcp_policy,
            "max_output_bytes",
            256_000,
        ),
    )


def _int_policy(
    allow_policy: dict[str, object],
    snapshot_policy: object,
    key: str,
    default: int,
) -> int:
    value = allow_policy.get(key)
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(snapshot_policy, dict):
        value = snapshot_policy.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return default


def _is_high_risk_tool(allow: McpToolAllowlist) -> bool:
    return allow.risk_level in {"high", "critical"}


def _next_run_event_sequence(session: Session, workspace_id: UUID, run_id: UUID) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
            RunEvent.workspace_id == workspace_id,
            RunEvent.agent_run_id == run_id,
        )
    )
    return int(current or 0) + 1


def _next_task_message_sequence(session: Session, workspace_id: UUID, task_id: UUID) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
            TaskMessage.workspace_id == workspace_id,
            TaskMessage.task_id == task_id,
        )
    )
    return int(current or 0) + 1


def _normalized_error(exc: Exception) -> dict[str, object]:
    if isinstance(exc, McpExecutionError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "mcp_adapter_failed", "message": exc.__class__.__name__}


def _payload_hash(payload: dict[str, object]) -> str:
    return sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()


def _canonical_payload(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _latency_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
