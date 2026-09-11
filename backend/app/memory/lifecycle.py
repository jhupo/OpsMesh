from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
)
from backend.app.memory.policy import MemoryLifecyclePolicy, memory_lifecycle_policy
from backend.app.memory.semantic import AgentSemanticMemoryService, SemanticMemoryUpsert
from backend.app.observability.audit_service import AuditService
from backend.app.teams.models import AgentTeam


@dataclass(frozen=True, slots=True)
class MemoryLifecycleSummary:
    archived_episodes: int = 0
    archived_semantic: int = 0
    promoted_episodes: int = 0


class WorkspaceMemoryLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def run_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> MemoryLifecycleSummary:
        if limit <= 0:
            return MemoryLifecycleSummary()
        now = _as_utc(now or datetime.now(UTC))
        configurations = self._session.scalars(
            select(WorkspaceMemoryConfiguration).order_by(
                WorkspaceMemoryConfiguration.updated_at.asc()
            )
        ).all()
        archived_episodes = 0
        archived_semantic = 0
        promoted_episodes = 0
        remaining = limit
        for configuration in configurations:
            if remaining <= 0:
                break
            policy = memory_lifecycle_policy(configuration.lifecycle_policy)
            entries = self._due_entries(
                configuration.workspace_id,
                policy=policy,
                now=now,
                limit=remaining,
            )
            for entry in entries:
                if _expired_episode(entry, policy, now):
                    self._archive_episode(
                        entry,
                        configuration_version=configuration.version,
                        now=now,
                    )
                    archived_episodes += 1
                elif _stale_semantic(entry, policy, now):
                    self._archive_semantic(
                        entry,
                        configuration_version=configuration.version,
                    )
                    archived_semantic += 1
                elif _promotion_eligible(entry, policy):
                    self._promote_episode(
                        entry,
                        configuration_version=configuration.version,
                        now=now,
                    )
                    promoted_episodes += 1
                remaining -= 1
        return MemoryLifecycleSummary(
            archived_episodes=archived_episodes,
            archived_semantic=archived_semantic,
            promoted_episodes=promoted_episodes,
        )

    def _due_entries(
        self,
        workspace_id: UUID,
        *,
        policy: MemoryLifecyclePolicy,
        now: datetime,
        limit: int,
    ) -> list[WorkspaceMemoryEntry]:
        conditions = []
        if policy.archive_expired_episodes:
            conditions.append(
                (
                    WorkspaceMemoryEntry.memory_layer == "episodic"
                )
                & WorkspaceMemoryEntry.expires_at.is_not(None)
                & (WorkspaceMemoryEntry.expires_at <= now)
            )
        if policy.semantic_archive_after_days is not None:
            semantic_cutoff = now - timedelta(days=policy.semantic_archive_after_days)
            conditions.append(
                (WorkspaceMemoryEntry.memory_layer == "semantic")
                & (WorkspaceMemoryEntry.source_type == "semantic_memory")
                & (WorkspaceMemoryEntry.updated_at <= semantic_cutoff)
                & or_(
                    WorkspaceMemoryEntry.last_accessed_at.is_(None),
                    WorkspaceMemoryEntry.last_accessed_at <= semantic_cutoff,
                )
            )
        if policy.auto_promote_episodes:
            conditions.append(
                (WorkspaceMemoryEntry.memory_layer == "episodic")
                & (WorkspaceMemoryEntry.importance >= policy.promotion_min_importance)
                & (WorkspaceMemoryEntry.access_count >= policy.promotion_min_access_count)
            )
        if not conditions:
            return []
        return list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.status == "active",
                    or_(*conditions),
                )
                .order_by(
                    WorkspaceMemoryEntry.importance.desc(),
                    WorkspaceMemoryEntry.updated_at.asc(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )

    def _archive_episode(
        self,
        entry: WorkspaceMemoryEntry,
        *,
        configuration_version: int,
        now: datetime,
    ) -> None:
        before = _entry_state(entry)
        entry.status = "archived"
        entry.archived_at = now
        entry.invalidate_embedding(status="not_applicable")
        self._record_event(
            entry,
            action="archived",
            reason_code="episodic_retention_expired",
            policy_version=configuration_version,
            before=before,
            after=_entry_state(entry),
        )

    def _archive_semantic(
        self,
        entry: WorkspaceMemoryEntry,
        *,
        configuration_version: int,
    ) -> None:
        before = _entry_state(entry)
        AgentSemanticMemoryService(self._session).archive(
            entry,
            expected_revision=entry.revision,
            change_reason="Archived by workspace semantic memory retention policy",
        )
        self._record_event(
            entry,
            action="archived",
            reason_code="semantic_retention_expired",
            policy_version=configuration_version,
            before=before,
            after=_entry_state(entry),
        )

    def _promote_episode(
        self,
        entry: WorkspaceMemoryEntry,
        *,
        configuration_version: int,
        now: datetime,
    ) -> None:
        before = _entry_state(entry)
        scope_type, scope_id = self._promotion_scope(entry)
        promoted = AgentSemanticMemoryService(self._session).upsert(
            SemanticMemoryUpsert(
                workspace_id=entry.workspace_id,
                scope_type=scope_type,
                scope_id=scope_id,
                memory_key=f"promoted-episode:{entry.content_fingerprint}",
                knowledge_type="fact",
                title=f"Promoted episode: {entry.title}"[:240],
                content=entry.content,
                tags=sorted({*entry.tags, "promoted", "source:episodic"}),
                importance=entry.importance,
                metadata={
                    "promotion": {
                        "source_memory_entry_id": str(entry.id),
                        "source_type": entry.source_type,
                        "source_id": entry.source_id,
                        "policy_version": configuration_version,
                    },
                    "source_provenance": entry.memory_metadata,
                },
                changed_by_agent_profile_id=entry.created_by_agent_profile_id,
                changed_by_agent_run_id=entry.created_by_agent_run_id,
                change_reason="Promoted by deterministic episodic memory policy",
            )
        )
        entry.status = "promoted"
        entry.archived_at = now
        entry.invalidate_embedding(status="not_applicable")
        entry.memory_metadata = {
            **entry.memory_metadata,
            "promoted_to_memory_id": str(promoted.id),
            "promoted_at": now.isoformat(),
        }
        self._record_event(
            entry,
            action="promoted",
            reason_code="episodic_promotion_threshold_met",
            policy_version=configuration_version,
            before=before,
            after={**_entry_state(entry), "promoted_to_memory_id": str(promoted.id)},
        )

    def _record_event(
        self,
        entry: WorkspaceMemoryEntry,
        *,
        action: str,
        reason_code: str,
        policy_version: int,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        self._session.add(
            WorkspaceMemoryLifecycleEvent(
                workspace_id=entry.workspace_id,
                memory_entry_id=entry.id,
                action=action,
                reason_code=reason_code,
                policy_version=policy_version,
                before=before,
                after=after,
            )
        )
        AuditService(self._session).record_system_action(
            workspace_id=entry.workspace_id,
            action=f"memory.{action}",
            target_type="workspace_memory_entry",
            target_id=entry.id,
            metadata={
                "reason_code": reason_code,
                "policy_version": policy_version,
                "memory_layer": entry.memory_layer,
            },
            actor_id="opsmesh.memory_lifecycle",
        )
        self._session.flush()

    def _promotion_scope(self, entry: WorkspaceMemoryEntry) -> tuple[str, UUID]:
        task = entry.memory_metadata.get("task")
        if isinstance(task, dict):
            team_id = _uuid_or_none(task.get("agent_team_id"))
            team = self._session.get(AgentTeam, team_id) if team_id is not None else None
            if team is not None and team.workspace_id == entry.workspace_id:
                return "team", team.id
        profile = (
            self._session.get(AgentProfile, entry.created_by_agent_profile_id)
            if entry.created_by_agent_profile_id is not None
            else None
        )
        if profile is not None and profile.workspace_id == entry.workspace_id:
            return "agent", profile.id
        return "workspace", entry.workspace_id


def decayed_importance(
    entry: WorkspaceMemoryEntry,
    policy: MemoryLifecyclePolicy,
    *,
    now: datetime,
) -> float:
    half_life_days = (
        policy.episodic_decay_half_life_days
        if entry.memory_layer == "episodic"
        else policy.semantic_decay_half_life_days
    )
    reference = entry.last_accessed_at or entry.updated_at or entry.created_at
    age_days = max((_as_utc(now) - _as_utc(reference)).total_seconds() / 86_400, 0)
    decay_factor: float = 0.5 ** (age_days / half_life_days)
    return round(float(entry.importance) * decay_factor, 6)


def _expired_episode(
    entry: WorkspaceMemoryEntry,
    policy: MemoryLifecyclePolicy,
    now: datetime,
) -> bool:
    return (
        policy.archive_expired_episodes
        and entry.memory_layer == "episodic"
        and entry.expires_at is not None
        and _as_utc(entry.expires_at) <= now
    )


def _stale_semantic(
    entry: WorkspaceMemoryEntry,
    policy: MemoryLifecyclePolicy,
    now: datetime,
) -> bool:
    if (
        entry.memory_layer != "semantic"
        or entry.source_type != "semantic_memory"
        or policy.semantic_archive_after_days is None
    ):
        return False
    cutoff = now - timedelta(days=policy.semantic_archive_after_days)
    return _as_utc(entry.updated_at) <= cutoff and (
        entry.last_accessed_at is None or _as_utc(entry.last_accessed_at) <= cutoff
    )


def _promotion_eligible(
    entry: WorkspaceMemoryEntry,
    policy: MemoryLifecyclePolicy,
) -> bool:
    return (
        policy.auto_promote_episodes
        and entry.memory_layer == "episodic"
        and entry.importance >= policy.promotion_min_importance
        and entry.access_count >= policy.promotion_min_access_count
    )


def _entry_state(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "status": entry.status,
        "importance": entry.importance,
        "access_count": entry.access_count,
        "revision": entry.revision,
        "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
    }


def _uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
