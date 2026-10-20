"""Cancel configuration Invocations and stop only their resident deployments."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.extensions.invocation import cancel_invocation
from opsmesh.extensions.maintenance import stop_deployments
from opsmesh.extensions.models import ExtensionInstance, Invocation, ManagedDeployment
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.errors import ForbiddenError


def drain_configuration(
    session: Session,
    *,
    configuration_id: UUID,
    actor: CurrentUser,
) -> tuple[int, int]:
    def authorize_cleanup(
        current_session: Session, invocation: Invocation, current_actor: CurrentUser
    ) -> None:
        instance = current_session.get(ExtensionInstance, invocation.instance_id)
        if (
            instance is None
            or instance.owner_id != current_actor.user_id
            or instance.configuration_id != configuration_id
        ):
            raise ForbiddenError("The configuration cleanup does not own this export")

    instances = select(ExtensionInstance.id).where(
        ExtensionInstance.configuration_id == configuration_id,
        ExtensionInstance.owner_id == actor.user_id,
    )
    pending = uncertain = 0
    for invocation in session.scalars(
        select(Invocation)
        .where(
            Invocation.instance_id.in_(instances),
            Invocation.status.in_(("queued", "started", "needs_attention")),
        )
        .order_by(Invocation.id)
        .with_for_update()
    ):
        if invocation.status == "needs_attention":
            uncertain += 1
        else:
            if not invocation.cancel_requested:
                cancel_invocation(
                    session,
                    invocation,
                    user=actor,
                    expected_revision=invocation.revision,
                    authorize_owner=authorize_cleanup,
                )
            pending += int(invocation.status != "cancelled")
    deployments = list(
        session.scalars(
            select(ManagedDeployment).where(
                ManagedDeployment.instance_id.in_(instances),
                ManagedDeployment.status != "stopped",
            )
        )
    )
    uncertain += sum(item.status == "needs_attention" for item in deployments)
    pending += stop_deployments(session, actor=actor, configuration_id=configuration_id)
    return pending, uncertain
