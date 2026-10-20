"""Explicit operation policy calls; resource authorization remains with its owner."""

from typing import Literal, Protocol, cast

from sqlalchemy.orm import Session

from opsmesh.shared.errors import ForbiddenError

PolicyDecision = Literal["allow", "deny", "require_approval"]


class PolicyChecker(Protocol):
    def __call__(self, session: Session, *, action: str, name: str) -> PolicyDecision: ...


def enforce_operation_policy(
    session: Session,
    *,
    action: str,
    name: str,
    approval_configured: bool = False,
) -> None:
    """Check the current policy before an effect; missing assembly fails closed."""
    checker = session.info.get("policy_checker")
    if checker is None:
        raise RuntimeError("The operation policy checker is not assembled")
    decision = cast(PolicyChecker, checker)(session, action=action, name=name)
    if decision == "deny":
        raise ForbiddenError("The platform policy denies this operation")
    if decision == "require_approval" and not approval_configured:
        raise ForbiddenError("This operation requires an explicitly configured approval path")
    if decision not in {"allow", "deny", "require_approval"}:
        raise RuntimeError("The operation policy returned an invalid decision")
