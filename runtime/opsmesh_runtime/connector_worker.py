"""Self-hosted MCP connector execution and recovery loop."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal, Protocol
from uuid import UUID

from .connector_models import (
    ConnectorContractError,
    ConnectorCredentialError,
    McpJob,
    McpJobCompletion,
    PendingMcpJob,
    request_from_job_payload,
)
from .connector_state import ConnectorStateStore
from .mcp_stdio_client import execute_request

LoopOutcome = Literal["idle", "completed", "failed", "recovered"]
McpRequestExecutor = Callable[
    [dict[str, object]],
    Coroutine[Any, Any, dict[str, object]],
]


class McpJobApi(Protocol):
    def poll_mcp_job(self) -> McpJob | None: ...

    def claim_mcp_job(self, job_id: UUID) -> None: ...

    def complete_mcp_job(self, job_id: UUID, completion: McpJobCompletion) -> None: ...


class SelfHostedMcpConnector:
    def __init__(
        self,
        *,
        api: McpJobApi,
        state: ConnectorStateStore,
        executor: McpRequestExecutor = execute_request,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._api = api
        self._state = state
        self._executor = executor
        self._environment = environment if environment is not None else os.environ

    def run_once(self) -> LoopOutcome:
        pending = self._state.next_pending()
        if pending is not None:
            return self._recover(pending)
        job = self._api.poll_mcp_job()
        if job is None:
            return "idle"
        self._api.claim_mcp_job(job.id)
        self._state.remember_claimed(job)
        return self._execute(job)

    def _recover(self, pending: PendingMcpJob) -> LoopOutcome:
        if pending.phase == "result_ready":
            if pending.completion is None:
                raise RuntimeError("Recovery result is missing")
            self._deliver(pending.job.id, pending.completion)
            return "recovered"
        if pending.phase == "executing":
            completion = McpJobCompletion.failed(
                code="self_hosted_mcp_execution_interrupted",
                message="Self-hosted MCP execution was interrupted before a result was recorded.",
            )
            self._state.mark_completion(pending.job.id, completion)
            self._deliver(pending.job.id, completion)
            return "recovered"
        return self._execute(pending.job)

    def _execute(self, job: McpJob) -> LoopOutcome:
        try:
            request = request_from_job_payload(
                job.request_payload,
                environment=self._environment,
            )
        except ConnectorCredentialError:
            completion = McpJobCompletion.failed(
                code="self_hosted_mcp_credential_unavailable",
                message="A required self-hosted MCP credential is unavailable.",
            )
        except ConnectorContractError:
            completion = McpJobCompletion.failed(
                code="self_hosted_mcp_contract_invalid",
                message="Self-hosted MCP job contract is invalid.",
            )
        else:
            self._state.mark_executing(job.id)
            try:
                response: dict[str, object] = asyncio.run(self._executor(request))
            except Exception:
                completion = McpJobCompletion.failed(
                    code="self_hosted_mcp_execution_failed",
                    message="Self-hosted MCP execution failed.",
                )
            else:
                completion = (
                    McpJobCompletion.failed(
                        code="self_hosted_mcp_remote_error",
                        message="The MCP server returned a tool error.",
                    )
                    if response.get("isError") is True
                    else McpJobCompletion.completed(response)
                )
        self._state.mark_completion(job.id, completion)
        self._deliver(job.id, completion)
        return "completed" if completion.status == "completed" else "failed"

    def _deliver(self, job_id: UUID, completion: McpJobCompletion) -> None:
        self._api.complete_mcp_job(job_id, completion)
        self._state.remove(job_id)
