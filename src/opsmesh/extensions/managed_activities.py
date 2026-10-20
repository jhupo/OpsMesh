"""Short authorized transactions around resident process effects; no recovery scanner."""

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar
from uuid import UUID

from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    authorize_invocation,
    load_installation,
)
from opsmesh.extensions.contracts import HostRequest
from opsmesh.extensions.deployment import load_deployment
from opsmesh.extensions.invocation import load_invocation
from opsmesh.extensions.managed_process import DockerManagedWorker
from opsmesh.extensions.models import ExtensionInstance, Invocation
from opsmesh.extensions.schemas import ExtensionWork
from opsmesh.extensions.service import audit_change, audit_invocation
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.shared.errors import DomainError, ForbiddenError

logger = logging.getLogger(__name__)
EffectResult = TypeVar("EffectResult")


async def complete_process_effect(operation: Callable[[], EffectResult]) -> EffectResult:
    pending = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Docker can finish a submitted effect after the caller is cancelled.
        # Do not let a later stop overtake that still-running start operation.
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not pending.cancelled():
            pending.exception()
        raise


class ManagedDeployments:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        users: UserDirectory,
        scopes: ScopeAuthorization,
        processes: DockerManagedWorker,
        *,
        reserve: Callable[[Session, UUID, UUID], UUID],
        release: Callable[[Session, UUID, UUID], None],
    ) -> None:
        self.sessions, self.users, self.scopes, self.processes = sessions, users, scopes, processes
        self.reserve, self.release = reserve, release

    async def invoke(
        self,
        *,
        invocation_id: UUID,
        envelope: dict[str, Any],
        host: HostRequest,
    ) -> dict[str, Any]:
        with self.sessions() as session:
            self.scopes.require_admission(session)
            invocation = load_invocation(session, invocation_id)
            authorize_invocation(session, invocation, users=self.users, scopes=self.scopes)
            deployment = load_deployment(session, UUID(str(invocation.snapshot["deployment_id"])))
            if deployment.status != "ready" or not deployment.container_id:
                raise ForbiddenError("The managed deployment is no longer ready")
            container_id = deployment.container_id
        return await self.processes.invoke(container_id=container_id, envelope=envelope, host=host)

    def _claim(self, work: ExtensionWork) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            admitted = True
            if work.action != "stop_worker":
                try:
                    self.scopes.require_admission(session)
                except DomainError:
                    admitted = False
            initial = load_deployment(session, work.target_id)
            instance = session.get(ExtensionInstance, initial.instance_id, with_for_update=True)
            assert instance is not None
            deployment = load_deployment(session, work.target_id, lock=True)
            if deployment.status == "stopped":
                return {"status": "stopped"}
            if not admitted or work.action == "stop_worker" or deployment.status == "stopping":
                deployment.status = "stopping"
                return {"status": "stopping"}
            try:
                user = self.users.load_identity(session, instance.owner_id)
                if user is None:
                    raise ForbiddenError("The deployment owner no longer exists")
                authorize_instance(
                    session, instance.id, user=user, scopes=self.scopes, execution=True
                )
                installation = load_installation(session, instance.installation_id)
                authorize_installation(session, installation, user=user)
                if (
                    installation.status != "enabled"
                    or instance.status != "enabled"
                    or instance.revision != deployment.instance_revision
                    or installation.artifact != deployment.artifact
                ):
                    raise ForbiddenError("The resident deployment authorization was revoked")
            except DomainError:
                deployment.status = "stopping"
                deployment.error_code = "deployment_authorization_revoked"
                deployment.revision += 1
                return {"status": "stopping"}
            if deployment.status == "needs_attention":
                return {"status": "needs_attention"}
            if deployment.capacity_reservation_id is None:
                deployment.capacity_reservation_id = self.reserve(
                    session,
                    instance.owner_id,
                    deployment.id,
                )
            return {
                "status": deployment.status,
                "container_id": deployment.container_id,
                "instance_id": str(instance.id),
                "artifact": deployment.artifact,
                "namespace": installation.namespace,
                "policy": PackageManifest.model_validate(installation.manifest).execution,
            }

    async def operate(self, work: ExtensionWork) -> dict[str, Any]:
        try:
            claimed = await asyncio.to_thread(self._claim, work)
        except DomainError:
            return await asyncio.to_thread(
                self._uncertain,
                work.target_id,
                "deployment_admission_denied",
            )
        status = claimed["status"]
        if status in {"stopped", "needs_attention"}:
            return {"status": status}
        try:
            if status == "stopping":
                await complete_process_effect(partial(self.processes.stop, str(work.target_id)))
                return await asyncio.to_thread(self._stopped, work.target_id)
            if status == "ready":
                running = await asyncio.to_thread(
                    self.processes.running,
                    claimed["container_id"],
                    str(work.target_id),
                )
                if not running:
                    return await asyncio.to_thread(
                        self._uncertain, work.target_id, "resident_process_lost"
                    )
                return {"status": "ready"}
            container_id = await complete_process_effect(
                partial(
                    self.processes.start,
                    deployment_id=str(work.target_id),
                    **{k: claimed[k] for k in ("instance_id", "artifact", "namespace", "policy")},
                )
            )
            with self.sessions() as session, session.begin():
                deployment = load_deployment(session, work.target_id, lock=True)
                deployment.container_id = container_id
                if deployment.status == "starting":
                    deployment.status = "ready"
                    deployment.revision += 1
                    user = self.users.load_identity(
                        session, instance_owner(session, deployment.instance_id)
                    )
                    if user is not None:
                        audit_change(session, user, "deployment.ready", deployment.id)
                return {"status": deployment.status}
        except BaseException as error:
            logger.warning(
                "managed_deployment_effect_failed",
                extra={
                    "deployment_id": str(work.target_id),
                    "error_type": type(error).__name__,
                    "reason": error.message
                    if isinstance(error, DomainError)
                    else "external_effect_failed",
                },
            )
            # The stable process identity is retained for explicit stop; retries
            # cannot create a replacement and repeat unknown external effects.
            return await asyncio.shield(
                asyncio.to_thread(
                    self._uncertain,
                    work.target_id,
                    "resident_effect_unconfirmed",
                )
            )

    def _uncertain(self, identifier: UUID, code: str) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            deployment = load_deployment(session, identifier, lock=True)
            if deployment.status == "stopped":
                return {"status": "stopped"}
            deployment.status = "needs_attention"
            deployment.error_code = code
            deployment.revision += 1
            user = self.users.load_identity(
                session, instance_owner(session, deployment.instance_id)
            )
            if user is not None:
                audit_change(session, user, "deployment.needs_attention", deployment.id)
            self._interrupt(session, deployment.instance_id)
            return {"status": "needs_attention"}

    def _interrupt(self, session: Session, instance_id: UUID) -> None:
        for invocation in session.scalars(
            select(Invocation)
            .where(
                Invocation.instance_id == instance_id,
                Invocation.status == "started",
            )
            .order_by(Invocation.id)
            .with_for_update()
        ):
            invocation.status = "needs_attention"
            invocation.error_code = "resident_process_interrupted"
            invocation.revision += 1
            audit_invocation(session, invocation)

    def _stopped(self, identifier: UUID) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            initial = load_deployment(session, identifier)
            instance = session.get(ExtensionInstance, initial.instance_id, with_for_update=True)
            assert instance is not None
            deployment = load_deployment(session, identifier, lock=True)
            if deployment.status == "stopped":
                return {"status": "stopped"}
            self._interrupt(session, deployment.instance_id)
            if deployment.capacity_reservation_id:
                self.release(session, instance.owner_id, deployment.capacity_reservation_id)
                deployment.capacity_reservation_id = None
            deployment.status = "stopped"
            deployment.revision += 1
            user = self.users.load_identity(session, instance.owner_id)
            if user is not None:
                audit_change(session, user, "deployment.stopped", identifier)
            return {"status": "stopped"}


def instance_owner(session: Session, instance_id: UUID) -> UUID:
    instance = session.get(ExtensionInstance, instance_id)
    assert instance is not None
    return instance.owner_id
