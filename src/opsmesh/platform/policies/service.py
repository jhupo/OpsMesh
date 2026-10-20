"""CAS replacement and current policy enforcement in the caller's transaction."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.audit.service import AuditReader
from opsmesh.platform.policies.evaluation import evaluate_policy
from opsmesh.platform.policies.models import PlatformPolicy, PlatformPolicyRevision
from opsmesh.platform.policies.schemas import Decision, PolicyWrite, RiskPolicy
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.serialization import canonical_json


def replace_policy(
    session: Session, *, key: str, payload: PolicyWrite, subject: AuditReader
) -> PlatformPolicy:
    if not subject.has_platform_role("admin"):
        raise ForbiddenError("Platform policy administration is required")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"platform.policy:{key}"},
    )
    record = session.scalar(
        select(PlatformPolicy)
        .where(PlatformPolicy.key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    revision = record.revision if record else 0
    if revision != payload.expected_revision:
        raise ConflictError("The policy revision changed")
    if record is None:
        record = PlatformPolicy(key=key)
        session.add(record)
    record.revision, record.enabled = revision + 1, payload.enabled
    record.policy = payload.policy.model_dump(mode="json")
    session.flush()
    session.add(
        PlatformPolicyRevision(
            policy_id=record.id,
            revision=record.revision,
            enabled=record.enabled,
            policy=record.policy,
            actor_id=subject.user_id,
        )
    )
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="platform.policy_replaced",
            actor=AuditActor.from_user(subject),
            source="platform",
            target_type="platform_policy",
            target_id=str(record.id),
            summary="Risk policy replaced",
            payload_json=canonical_json({"revision": record.revision}),
        ),
    )
    return record


def current_decision(session: Session, *, action: str, name: str) -> Decision:
    decisions: list[Decision] = ["allow"]
    for record in session.scalars(
        select(PlatformPolicy)
        .where(PlatformPolicy.enabled.is_(True))
        .execution_options(populate_existing=True)
    ):
        decisions.append(
            evaluate_policy(RiskPolicy.model_validate(record.policy), action=action, name=name)
        )
    return max(decisions, key={"allow": 0, "require_approval": 1, "deny": 2}.__getitem__)
