"""Extension-owned unfinished effects and deployments used by host drain."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.extensions.deployment import stop_deployment
from opsmesh.extensions.models import ExtensionInstance, Installation, Invocation, ManagedDeployment
from opsmesh.identity.context import CurrentUser


def stop_deployments(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID | None = None,
) -> int:
    """Stage platform or already-authorized configuration cleanup, never Docker I/O."""
    instances = select(ExtensionInstance).order_by(ExtensionInstance.id).with_for_update()
    if configuration_id is not None:
        instances = instances.where(
            ExtensionInstance.configuration_id == configuration_id,
            ExtensionInstance.owner_id == actor.user_id,
        )
    elif not actor.has_platform_role("admin"):
        instances = instances.where(ExtensionInstance.owner_id == actor.user_id)
    pending = 0
    for instance in session.scalars(instances):
        for deployment in session.scalars(
            select(ManagedDeployment)
            .where(
                ManagedDeployment.instance_id == instance.id,
                ManagedDeployment.status.in_(("starting", "ready", "stopping")),
            )
            .order_by(ManagedDeployment.id)
            .with_for_update()
        ):
            stop_deployment(session, deployment=deployment, user=actor)
            pending += 1
    return pending


def unsettled_extension_count(session: Session) -> int:
    invocations = (
        session.scalar(
            select(func.count())
            .select_from(Invocation)
            .where(Invocation.status.in_(("started", "needs_attention")))
        )
        or 0
    )
    deployments = (
        session.scalar(
            select(func.count())
            .select_from(Installation)
            .where(
                Installation.status.in_(("building", "deploying", "draining", "needs_attention"))
            )
        )
        or 0
    )
    residents = (
        session.scalar(
            select(func.count())
            .select_from(ManagedDeployment)
            .where(ManagedDeployment.status != "stopped")
        )
        or 0
    )
    return invocations + deployments + residents
