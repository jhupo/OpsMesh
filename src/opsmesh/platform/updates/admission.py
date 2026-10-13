"""Fence new business execution while an installation is being replaced."""

from sqlalchemy.orm import Session

from opsmesh.platform.updates.service import maintenance_enabled
from opsmesh.shared.errors import DomainError


def require_execution_admission(session: Session) -> None:
    # Share the installation row until the admission transaction commits. The
    # updater takes an exclusive lock before entering maintenance and draining.
    if maintenance_enabled(session):
        raise DomainError(
            "New execution is paused while the platform is being updated",
            code="platform_maintenance",
            status_code=503,
        )
