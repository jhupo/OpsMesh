from __future__ import annotations

import json
from uuid import UUID, uuid5

from claude_agent_sdk.types import SessionKey, SessionStoreEntry

from backend.app.domains.agents.runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
    AgentRuntimeEvent,
    AgentRuntimeSession,
)

_SESSION_NAMESPACE = UUID("6bd4b8b9-8a4b-49db-9b6c-d0b7d7da4be6")


class ClaudeAgentSessionStore:
    """Mirror Claude's opaque JSONL transcript into the product session."""

    def __init__(self, session: AgentRuntimeSession, *, session_id: str) -> None:
        self._session = session
        self._session_id = session_id

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if key["session_id"] != self._session_id:
            raise ValueError("Claude transcript key does not match the product session")
        if not entries:
            return
        await self._session.add_items(
            [
                {
                    "_opsmesh_runtime": "claude_agent_sdk",
                    "session_id": self._session_id,
                    "project_key": key.get("project_key"),
                    "subpath": key.get("subpath"),
                    "entry": json.loads(json.dumps(entry)),
                }
                for entry in entries
            ]
        )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        if key["session_id"] != self._session_id:
            raise ValueError("Claude transcript key does not match the product session")
        items = await self._session.get_items()
        entries: list[SessionStoreEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("_opsmesh_runtime") != "claude_agent_sdk":
                continue
            if item.get("session_id") != self._session_id:
                continue
            if item.get("subpath") != key.get("subpath"):
                continue
            entry = item.get("entry")
            if isinstance(entry, dict):
                entries.append(json.loads(json.dumps(entry)))
        return entries or None


def _session_id(request: AgentRunRequest) -> str:
    if request.session is None:
        return str(uuid5(_SESSION_NAMESPACE, str(request.context.run_id)))
    return str(
        uuid5(
            _SESSION_NAMESPACE,
            f"{request.context.workspace_id}:{request.session.session_id}",
        )
    )


def _resume_session_id(request: AgentRunRequest) -> str | None:
    if request.resume_state is None:
        return None
    try:
        payload = json.loads(request.resume_state.serialized_state)
    except json.JSONDecodeError as exc:
        raise ValueError("Stored Claude Agent SDK state is not valid JSON") from exc
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str):
        raise ValueError("Stored Claude Agent SDK state is missing session_id")
    expected = _session_id(request)
    if session_id != expected:
        raise ValueError("Stored Claude Agent SDK state does not match the product session")
    return session_id


def _is_rejected_resume(request: AgentRunRequest) -> bool:
    if not request.approval_decisions:
        return False
    if request.resume_state is None:
        raise ValueError("Approval decisions require a Claude Agent SDK resume state")
    payload = json.loads(request.resume_state.serialized_state)
    call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
    tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("session_id") != _session_id(request):
        raise ValueError("Stored Claude Agent SDK state does not match the product session")
    for decision in request.approval_decisions:
        if decision.tool_call_id != call_id or decision.tool_name != tool_name:
            raise ValueError("Stored approval decision does not match the Claude SDK interruption")
        if decision.status not in {"approved", "rejected"}:
            raise ValueError("Stored tool approval decision is invalid")
    return any(decision.status == "rejected" for decision in request.approval_decisions)


def _rejected_result(
    request: AgentRunRequest,
    capabilities: AgentRuntimeCapabilities,
) -> AgentRunResult:
    decision = request.approval_decisions[0]
    return AgentRunResult(
        final_output=decision.reason or f"Tool {decision.tool_name} was rejected by policy.",
        events=(
            AgentRuntimeEvent(
                event_type="tool.rejected",
                message="Tool approval was rejected; Claude session was not resumed.",
                payload={"tool_name": decision.tool_name, "tool_call_id": decision.tool_call_id},
            ),
        ),
        capabilities=capabilities,
    )
