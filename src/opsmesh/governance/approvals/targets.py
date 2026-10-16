"""Approval target and notification contracts.

These are static value types plus the one callback a target owner registers:
governance decides once and durably, and the owner learns the decision through
this contract. No business module is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ApprovalTarget:
    """The static reference of what one approval governs.

    ``target_version`` is the revision the requester expected; a target that
    moved on refuses a decision taken against a stale revision.
    """

    target_type: str
    target_id: str
    target_version: str | None = None


@dataclass(frozen=True)
class EffectiveDecision:
    """The decision one target owner receives, with its audit anchor."""

    approval_id: UUID
    decision: str
    target: ApprovalTarget
    decided_by_user_id: UUID
    reason: str | None


class ApprovalDecisionNotifier(Protocol):
    """Deliver one effective decision to its target owner.

    The implementation runs inside the caller's transaction and stages the
    durable delivery (for example a Workflow signal through the Outbox); it
    never performs the target's work itself.
    """

    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None: ...


class NoApprovalTargetNotifier:
    """The S4 notifier: no target owner is registered before the producers.

    Decisions stay correct and auditable; the producers that register a target
    contract arrive with their own steps (S7 execution and later).
    """

    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None:
        _ = (session, approval_id, decision, target, decided_by_user_id, reason)
