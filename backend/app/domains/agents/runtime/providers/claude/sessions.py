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
        self._seen_entry_ids: set[
            tuple[str | None, str | None, str]
        ] | None = None

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if key["session_id"] != self._session_id:
            raise ValueError("Claude transcript key does not match the product session")
        if not entries:
            return
        seen = await self._seen_ids()
        project_key = key.get("project_key")
        subpath = key.get("subpath")
        pending_ids: set[tuple[str | None, str | None, str]] = set()
        pending: list[dict[str, object]] = []
        for entry in entries:
            entry_id = entry.get("uuid")
            dedup_key = (
                (project_key, subpath, entry_id)
                if isinstance(entry_id, str) and entry_id
                else None
            )
            if dedup_key is not None and (dedup_key in seen or dedup_key in pending_ids):
                continue
            pending.append(
                {
                    "_opsmesh_runtime": "claude_agent_sdk",
                    "session_id": self._session_id,
                    "project_key": project_key,
                    "subpath": subpath,
                    "entry": json.loads(json.dumps(entry)),
                }
            )
            if dedup_key is not None:
                pending_ids.add(dedup_key)
        if not pending:
            return
        await self._session.add_items(pending)
        seen.update(pending_ids)

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
            if item.get("project_key") != key.get("project_key"):
                continue
            if item.get("subpath") != key.get("subpath"):
                continue
            entry = item.get("entry")
            if isinstance(entry, dict):
                entries.append(json.loads(json.dumps(entry)))
        return entries or None

    async def has_transcript(self) -> bool:
        return bool(await self._stored_items())

    async def _seen_ids(self) -> set[tuple[str | None, str | None, str]]:
        if self._seen_entry_ids is None:
            seen: set[tuple[str | None, str | None, str]] = set()
            for item in await self._stored_items():
                entry = item.get("entry")
                entry_id = entry.get("uuid") if isinstance(entry, dict) else None
                if isinstance(entry_id, str) and entry_id:
                    subpath = item.get("subpath")
                    project_key = item.get("project_key")
                    seen.add(
                        (
                            project_key if isinstance(project_key, str) else None,
                            subpath if isinstance(subpath, str) else None,
                            entry_id,
                        )
                    )
            self._seen_entry_ids = seen
        return self._seen_entry_ids

    async def _stored_items(self) -> list[dict[str, object]]:
        return [
            item
            for item in await self._session.get_items()
            if isinstance(item, dict)
            and item.get("_opsmesh_runtime") == "claude_agent_sdk"
            and item.get("session_id") == self._session_id
        ]


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
    if len(request.approval_decisions) != 1:
        raise ValueError("Claude Agent SDK supports one deferred approval per resume state")
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
