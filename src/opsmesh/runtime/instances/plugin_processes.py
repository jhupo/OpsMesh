"""Reconcile durable plugin intent through the existing runtime lifecycle."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.deployments import (
    DeploymentConfiguration,
    deployment_lock_key,
)
from opsmesh.capabilities.plugins.models import PluginCredential, PluginDeployment
from opsmesh.capabilities.plugins.services import PluginPrincipal, PluginServices
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import ResourceAccessDenied
from opsmesh.identity.authorization.service import AuthorizationService
from opsmesh.runtime.instances.contracts import DockerRuntimeClient, RuntimeProcess
from opsmesh.runtime.instances.manager import RuntimeManager
from opsmesh.runtime.instances.models import RuntimeTemplate, WorkspaceRuntime
from opsmesh.runtime.instances.policies.quotas import RuntimeQuotaPolicy
from opsmesh.runtime.instances.policies.runtime import RuntimePolicyResolver
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import ensure_aware_utc


class PluginProcessWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        docker: DockerRuntimeClient,
    ) -> None:
        self.sessions, self.settings, self.docker = session_factory, settings, docker
        self.secrets = SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )

    def run_once(self, limit: int = 4) -> None:
        with self.sessions() as session:
            due = list(
                session.execute(
                    select(PluginDeployment.id, PluginDeployment.install_id)
                    .where(PluginDeployment.next_check_at <= datetime.now(UTC))
                    .order_by(PluginDeployment.next_check_at)
                    .limit(limit)
                )
            )
            engine = session.get_bind()
            if not isinstance(engine, Engine):
                raise RuntimeError("Plugin process maintenance requires an engine-bound session")
        for deployment_id, install_id in due:
            # Keep the connection pinned: session-level lock survives lifecycle commits and is
            # released on connection loss. Admission takes the matching transaction-level lock.
            with engine.connect() as connection:
                key = deployment_lock_key(install_id)
                locked = connection.scalar(select(func.pg_try_advisory_lock(key)))
                connection.commit()
                if not locked:
                    continue
                try:
                    with Session(bind=connection) as session:
                        self._run(session, deployment_id)
                finally:
                    connection.rollback()
                    connection.execute(select(func.pg_advisory_unlock(key)))
                    connection.commit()

    def _run(self, session: Session, deployment_id: UUID) -> None:
        item = session.get(PluginDeployment, deployment_id)
        if item is None:
            return
        previous = (item.status, item.error_code)
        manager = RuntimeManager(session, self.docker)
        try:
            self._reconcile(session, manager, item)
        except Exception:
            # Provider/Docker errors may contain configuration. Durable failure evidence carries
            # a bounded code only; never serialize exceptions or the decrypted environment.
            session.rollback()
            item = session.get(PluginDeployment, deployment_id)
            if item is None:
                return
            item.attempts += 1
            item.status, item.error_code = "failed", "plugin_process_reconcile_failed"
        if previous != (item.status, item.error_code):
            AuditService(session).record_system_action(
                workspace_id=item.workspace_id,
                action="plugin.deployment.reconciled",
                target_type="plugin_install",
                target_id=item.install_id,
                metadata={
                    "status": item.status,
                    "error_code": item.error_code,
                    "revision": item.revision,
                    "attempts": item.attempts,
                },
            )
        item.next_check_at = datetime.now(UTC) + timedelta(
            seconds=min(300, 30 * (item.attempts + 1))
        )
        session.commit()

    def _authorized(self, session: Session, item: PluginDeployment) -> AuthenticatedUser:
        services = PluginServices(session)
        install = services.active_install(item.workspace_id, item.install_id)
        release = services.active_release(install)
        config = DeploymentConfiguration.model_validate(item.configuration)
        if install.generation != item.generation or not set(config.permissions) <= set(
            release.approved_permissions
        ):
            raise ResourceAccessDenied()
        actor = ExecutionIdentityService(session).restore(
            item.workspace_id, item.execution_identity
        )
        AuthorizationService(session).require_workspace(
            user_id=actor.user_id,
            workspace_id=item.workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        template = session.get(RuntimeTemplate, item.template_id)
        if template is None or template.status != "active" or template.image != item.image:
            raise ResourceAccessDenied()
        policy = RuntimePolicyResolver(session).resolve_runtime_policy(
            workspace_id=item.workspace_id,
            runtime_space_id=None,
            template=template,
            requested_limits=None,
            requested_network_disabled=False,
        )
        if (
            config.limits != policy.limits
            or config.network_policy != policy.egress_policy.as_dict()
        ):
            raise ResourceAccessDenied()
        return actor

    def _runtime(self, session: Session, item: PluginDeployment) -> WorkspaceRuntime | None:
        if item.runtime_id is None:
            return None
        runtime = session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == item.workspace_id,
                WorkspaceRuntime.id == item.runtime_id,
            )
        )
        if runtime is None or runtime.capabilities.get("plugin_install_id") != str(item.install_id):
            raise ResourceAccessDenied()
        return runtime

    def _remove(self, session: Session, manager: RuntimeManager, item: PluginDeployment) -> None:
        runtime = self._runtime(session, item)
        if runtime is not None and runtime.status != "deleted":
            # The stable name also finds a container created just before a Worker interruption.
            if not runtime.docker_container_id:
                if runtime.host is None:
                    raise RuntimeError("Plugin physical host identity is unavailable")
                runtime.host.docker_container_id = f"opsmesh-{item.workspace_id}-{runtime.host.id}"
            manager.delete_runtime(runtime)
            if runtime.status != "deleted":
                raise RuntimeError("Plugin runtime cleanup is incomplete")
        item.runtime_id = None
        if item.credential_id is not None:
            credential = session.scalar(
                select(PluginCredential).where(
                    PluginCredential.workspace_id == item.workspace_id,
                    PluginCredential.install_id == item.install_id,
                    PluginCredential.id == item.credential_id,
                )
            )
            if credential is not None:
                credential.status = "revoked"
        item.credential_id, item.credential_expires_at = None, None
        session.commit()

    def _reconcile(self, session: Session, manager: RuntimeManager, item: PluginDeployment) -> None:
        if item.desired_state == "stopped":
            self._remove(session, manager, item)
            item.status, item.error_code, item.applied_revision = "stopped", None, item.revision
            return
        try:
            actor = self._authorized(session, item)
        except Exception:
            self._remove(session, manager, item)
            item.status, item.error_code = "blocked", "plugin_deployment_authority_revoked"
            return
        expired = item.credential_expires_at is not None and ensure_aware_utc(
            item.credential_expires_at
        ) <= datetime.now(UTC) + timedelta(hours=1)
        if item.applied_revision != item.revision or expired:
            self._remove(session, manager, item)
        if item.credential_id is not None:
            try:
                PluginServices(session).require(
                    PluginPrincipal(item.workspace_id, item.install_id, item.credential_id)
                )
            except ResourceAccessDenied:
                self._remove(session, manager, item)
                item.status, item.error_code = "blocked", "plugin_credential_revoked"
                # Revocation is not permission to issue another token automatically.
                item.attempts = 8
                return
        if item.attempts >= 8:
            return
        if item.runtime_id is None:
            self._prepare(session, item, actor)
        runtime = self._runtime(session, item)
        if runtime is None:
            raise RuntimeError("Missing plugin runtime intent")
        if runtime.docker_container_id is None:
            environment = self.secrets.decrypt_payload(
                item.encrypted_environment, key_id=item.encryption_key_id
            )
            template = session.get(RuntimeTemplate, item.template_id)
            if template is None:
                raise ResourceAccessDenied()
            manager.provision_runtime(
                runtime,
                template=template,
                limits=DeploymentConfiguration.model_validate(item.configuration).limits,
                network_disabled=runtime.network_policy.get("mode") == "none",
                process=RuntimeProcess({str(k): str(v) for k, v in environment.items()}),
            )
        if not self.docker.container_running(runtime.docker_container_id or ""):
            if runtime.status == "running":
                item.attempts += 1
                session.commit()
                if item.attempts >= 8:
                    self._remove(session, manager, item)
                    item.status, item.error_code = "failed", "plugin_restart_limit"
                    return
            manager.start_runtime(runtime)
            if not self.docker.container_running(runtime.docker_container_id or ""):
                raise RuntimeError("Plugin process exited")
        runtime.last_heartbeat_at, runtime.connection_status = datetime.now(UTC), "online"
        runtime.status = "running"
        item.status, item.error_code = "running", None

    def _prepare(self, session: Session, item: PluginDeployment, actor: AuthenticatedUser) -> None:
        config = DeploymentConfiguration.model_validate(item.configuration)
        record, token = PluginServices(session).issue(
            item.workspace_id,
            item.install_id,
            actor,
            config.permissions,
            24,
        )
        RuntimeQuotaPolicy(session).assert_can_create_runtime(item.workspace_id, config.limits)
        environment = self.secrets.decrypt_payload(
            item.encrypted_environment, key_id=item.encryption_key_id
        )
        environment.update(
            {
                "OPSMESH_URL": config.platform_url,
                "OPSMESH_WORKSPACE_ID": str(item.workspace_id),
                "OPSMESH_INSTALL_ID": str(item.install_id),
                "OPSMESH_PLUGIN_TOKEN": token,
            }
        )
        encrypted = self.secrets.encrypt_payload(environment)
        item.encrypted_environment, item.encryption_key_id = encrypted.ciphertext, encrypted.key_id
        item.credential_id, item.credential_expires_at = record.id, record.expires_at
        runtime = WorkspaceRuntime(
            id=uuid4(),
            workspace_id=item.workspace_id,
            runtime_template_id=item.template_id,
            name=f"plugin-{item.install_id}",
            execution_mode="shared",
            status="provisioning",
            limits=asdict(config.limits),
            network_policy=config.network_policy,
            capabilities={"plugin_install_id": str(item.install_id)},
        )
        session.add(runtime)
        session.flush()
        item.runtime_id, item.applied_revision = runtime.id, item.revision
        # Persist the stable identity and encrypted credential before any container side effect.
        session.commit()
