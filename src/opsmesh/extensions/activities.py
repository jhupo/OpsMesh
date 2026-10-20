"""Short ownership transactions around isolated build and invocation I/O."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opsmesh_sdk.manifest import ExecutionPolicy, PackageManifest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.capabilities.lifecycle import read_version_package
from opsmesh.extensions.access import ScopeAuthorization, load_installation, require_admin
from opsmesh.extensions.contracts import InvocationCancelled, InvocationSandbox
from opsmesh.extensions.deployment import DockerPackageBuilder
from opsmesh.extensions.host import InvocationHost
from opsmesh.extensions.invocation import claim_invocation, complete_invocation
from opsmesh.extensions.managed_activities import ManagedDeployments
from opsmesh.extensions.models import ExtensionInstance, ManagedDeployment
from opsmesh.extensions.schemas import ExtensionWork
from opsmesh.extensions.service import active_invocations, audit_change
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.shared.errors import ConflictError, DomainError
from opsmesh.shared.security.encryption import SecretCipher

logger = logging.getLogger(__name__)


class ExtensionActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        users: UserDirectory,
        scopes: ScopeAuthorization,
        cipher: SecretCipher,
        storage_root: Path,
        builder: DockerPackageBuilder,
        sandbox: InvocationSandbox,
        host: InvocationHost,
        *,
        api_prefix: str,
        managed: ManagedDeployments | None = None,
    ) -> None:
        self.sessions, self.users, self.scopes = sessions, users, scopes
        self.cipher, self.storage_root = cipher, storage_root
        self.builder, self.sandbox, self.host = builder, sandbox, host
        self.api_prefix = api_prefix
        self.managed = managed

    @activity.defn(name="extensions.operate")
    async def operate(self, work: ExtensionWork) -> dict[str, Any]:
        async def heartbeat() -> None:
            while True:
                activity.heartbeat(str(work.target_id))
                await asyncio.sleep(2)

        pulse = asyncio.create_task(heartbeat())
        try:
            if work.action == "install":
                return await self.install(work)
            if work.action == "uninstall":
                return await asyncio.to_thread(self.uninstall, work)
            if work.action in {"start_worker", "stop_worker"}:
                if self.managed is None:
                    raise ConflictError("Managed deployments are not assembled")
                return await self.managed.operate(work)
            return await self.invoke(work)
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    async def install(self, work: ExtensionWork) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            installation = load_installation(session, work.target_id, lock=True)
            if installation.status == "enabled":
                return {"status": "enabled"}
            if installation.status == "deploying":
                user = self.users.load_identity(session, installation.requested_by)
                if user is None:
                    raise ConflictError("The installer no longer exists")
                require_admin(user)
                if not installation.artifact:
                    raise ConflictError("Deployment has no frozen artifact")
                installation.status = "enabled"
                installation.revision += 1
                audit_change(session, user, "installation.enabled", installation.id)
                return {"status": "enabled"}
            if installation.status not in {"requested", "building"}:
                return {"status": installation.status}
            user = self.users.load_identity(session, installation.requested_by)
            if user is None:
                raise ConflictError("The installer no longer exists")
            require_admin(user)
            _, payload = read_version_package(
                session, version_id=installation.version_id, storage_root=self.storage_root
            )
            manifest = PackageManifest.model_validate(installation.manifest)
            installation.status = "building"
            installation.revision += 1
        try:
            artifact = await self.builder.build(payload, manifest)
        except BaseException:
            with self.sessions() as session, session.begin():
                installation = load_installation(session, work.target_id, lock=True)
                if installation.status == "building":
                    installation.status = "failed"
                    installation.error_code = "isolated_build_failed"
                    installation.revision += 1
            raise
        with self.sessions() as session, session.begin():
            installation = load_installation(session, work.target_id, lock=True)
            if installation.status != "building":
                raise ConflictError("Installation changed during the isolated build")
            installation.artifact = artifact
            installation.status = "deploying"
            installation.revision += 1
        with self.sessions() as session, session.begin():
            installation = load_installation(session, work.target_id, lock=True)
            if installation.status == "deploying":
                user = self.users.load_identity(session, installation.requested_by)
                if user is None:
                    raise ConflictError("The installer no longer exists")
                require_admin(user)
                installation.status = "enabled"
                installation.revision += 1
                audit_change(session, user, "installation.enabled", installation.id)
        return {"status": installation.status}

    def uninstall(self, work: ExtensionWork) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            installation = load_installation(session, work.target_id, lock=True)
            if installation.status == "removed":
                return {"status": "removed"}
            if installation.status != "draining":
                raise ConflictError("The installation is not draining")
            if active_invocations(session, installation_id=installation.id):
                return {"status": "draining"}
            if (
                session.scalar(
                    select(ManagedDeployment.id)
                    .join(
                        ExtensionInstance,
                        ExtensionInstance.id == ManagedDeployment.instance_id,
                    )
                    .where(
                        ExtensionInstance.installation_id == installation.id,
                        ManagedDeployment.status != "stopped",
                    )
                    .limit(1)
                )
                is not None
            ):
                return {"status": "draining"}
            # Native environments have their own explicit close operation.
            # Removing a registration never deletes shared environment/state.
            installation.status = "removed"
            installation.revision += 1
            actor = self.users.load_identity(session, installation.requested_by)
            if actor is not None:
                audit_change(session, actor, "installation.removed", installation.id)
            return {"status": "removed"}

    async def invoke(self, work: ExtensionWork) -> dict[str, Any]:
        started = False
        result = None
        error_code = None
        try:
            with self.sessions() as session, session.begin():
                invocation = claim_invocation(
                    session, work.target_id, users=self.users, scopes=self.scopes
                )
                if invocation.status != "started":
                    return {"status": invocation.status}
                started = True
                managed_mode = (
                    ExecutionPolicy.model_validate(invocation.snapshot["execution_policy"]).mode
                    == "managed_worker"
                )
                envelope = {
                    "action": "invoke",
                    "kind": invocation.kind,
                    "namespace": invocation.snapshot["namespace"],
                    "export_id": invocation.export_id,
                    "configuration": invocation.snapshot["configuration"],
                    "arguments": self.cipher.decrypt_payload(invocation.input_ciphertext),
                    "subject": {
                        "installation_id": str(invocation.installation_id),
                        "instance_id": str(invocation.instance_id),
                        "invocation_id": str(invocation.id),
                        "namespace": invocation.snapshot["namespace"],
                        "parent_invocation_id": str(invocation.parent_id)
                        if invocation.parent_id
                        else None,
                    },
                    "scope": {
                        "configuration_id": invocation.snapshot.get("configuration_id"),
                    },
                    "deadline_seconds": max(
                        0.001, (invocation.deadline - datetime.now(UTC)).total_seconds()
                    ),
                    "api_prefix": self.api_prefix,
                }
            transport = self.sandbox
            if managed_mode:
                if self.managed is None:
                    raise ConflictError("Managed deployments are not assembled")
                transport = self.managed
            result = await transport.invoke(
                invocation_id=work.target_id,
                envelope=envelope,
                host=lambda message: self.host.exchange(work.target_id, message),
            )
        except InvocationCancelled as error:
            result = error.result
            error_code = "cancelled"
        except DomainError as error:
            logger.warning(
                "extension_invocation_rejected",
                extra={
                    "invocation_id": str(work.target_id),
                    "reason": error.message,
                    "error_code": error.code,
                },
            )
            error_code = error.code
        except BaseException:
            error_code = "isolated_invocation_interrupted"
        with self.sessions() as session, session.begin():
            invocation = complete_invocation(
                session,
                work.target_id,
                result=result,
                cipher=self.cipher,
                error_code=error_code,
                confirmed_not_started=not started,
            )
            return {"status": invocation.status}
