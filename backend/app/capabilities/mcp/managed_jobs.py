from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.capabilities.mcp.catalog.discovery import McpToolDiscoveryService
from backend.app.capabilities.mcp.managed_runtime import deployment_runtime, process_request
from backend.app.capabilities.mcp.managed_service import authorize_management
from backend.app.capabilities.mcp.models import McpCredentialReference, McpDeployment, McpServer
from backend.app.capabilities.mcp.policy import require_mcp_server
from backend.app.capabilities.mcp.transport.stdio_credentials import hosted_stdio_environment
from backend.app.capabilities.plugins.policy import require_plugin_resource
from backend.app.governance.audit.service import AuditService
from backend.app.governance.policies.reader import PlatformPolicyService
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resource_queries import (
    ResourceQueryScope,
    bind_resource_queries,
)
from backend.app.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.runtime.instances.manager import DockerRuntimeManagerProvider
from backend.app.runtime.instances.models import WorkspaceRuntime
from backend.app.runtime.instances.policies.safety import RuntimeSafetyPolicy
from backend.app.runtime.instances.policies.templates import RuntimeTemplateGuard
from backend.app.runtime.instances.service import RuntimeControlService
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload
from backend.app.shared.errors import PolicyDeniedError


class ManagedMcpJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self.context = context

    def handle(self, job: JobPayload) -> None:
        session = self.context.session
        deployment = session.scalar(
            select(McpDeployment)
            .where(
                McpDeployment.workspace_id == job.workspace_id,
                McpDeployment.id == job.resource_id,
            )
            .with_for_update()
        )
        if deployment is None or deployment.generation != job.routing.get("generation"):
            return
        if deployment.status not in {"queued", "failed"}:
            return
        deployment.status = "starting"
        deployment.checked_at = datetime.now(UTC)
        session.commit()
        try:
            self._handle(deployment)
        except Exception:
            session.rollback()
            # Revoked authority or invalid configuration must not leave a project running.
            runtime = deployment_runtime(session, deployment)
            try:
                if runtime is not None and runtime.docker_container_id:
                    self._manager().require().stop_runtime(runtime)
            except Exception:
                deployment.last_error = "mcp_process_cleanup_failed"
            else:
                deployment.last_error = "mcp_process_control_failed"
            deployment.status = "failed"
            deployment.checked_at = datetime.now(UTC)
            server = require_mcp_server(session, deployment.workspace_id, deployment.mcp_server_id)
            server.health_status = "unhealthy"
            server.last_error = deployment.last_error
            server.last_health_check_at = deployment.checked_at
            session.commit()
            # Do not put secret-bearing third-party exceptions in queue retry metadata.
            raise RuntimeError("Managed MCP lifecycle failed; inspect deployment status") from None

    def _manager(self) -> DockerRuntimeManagerProvider:
        return DockerRuntimeManagerProvider(
            self.context.session,
            self.context.require_settings(context="managed MCP"),
            self.context.docker_client(),
        )

    def _handle(self, deployment: McpDeployment) -> None:
        session = self.context.session
        settings = self.context.require_settings(context="managed MCP")
        user = ExecutionIdentityService(session).restore(
            deployment.workspace_id, deployment.execution_identity
        )
        authorize_management(session, deployment.workspace_id, user)
        ResourceAuthorizationService(session, user).require(
            deployment.workspace_id,
            ResourceKind.MCP_SERVER,
            deployment.mcp_server_id,
            ResourceAction.CONTROL,
        )
        bind_resource_queries(
            session, ResourceQueryScope(deployment.workspace_id, user, execution=True)
        )
        server = require_mcp_server(session, deployment.workspace_id, deployment.mcp_server_id)
        runtime = deployment_runtime(session, deployment)
        if deployment.action == "stop":
            if runtime is not None and runtime.docker_container_id:
                self._manager().require().stop_runtime(runtime)
            self._finish(deployment, server, "stopped", user.user_id)
            return
        if (
            server.status != "active"
            or server.platform_blocked
            or server.connection.get("runtime") != "managed"
        ):
            raise PolicyDeniedError("MCP project is not approved for execution")
        require_plugin_resource(session, deployment.workspace_id, "mcp_server", server.id)
        guard = RuntimeTemplateGuard(
            session,
            RuntimeSafetyPolicy(
                tuple(settings.runtime_allowed_images),
                PlatformPolicyService(session).risky_execution_policy(),
            ),
        )
        if (
            guard.validated_template(
                workspace_id=deployment.workspace_id,
                template_id=deployment.template_id,
                limits=None,
                network_disabled=deployment.network_disabled,
                runtime_space_id=None,
            )
            is None
        ):
            raise ValueError("Runtime template is unavailable")
        credential = None
        credential_id = server.connection.get("credential_reference_id")
        if server.connection.get("requires_credentials"):
            from uuid import UUID

            credential = session.scalar(
                select(McpCredentialReference).where(
                    McpCredentialReference.workspace_id == deployment.workspace_id,
                    McpCredentialReference.mcp_server_id == server.id,
                    McpCredentialReference.id == UUID(str(credential_id)),
                    McpCredentialReference.status == "active",
                )
            )
            if credential is None:
                raise PolicyDeniedError("MCP environment is not approved or was revoked")
        configuration_version = server.configuration_version
        credential_version = credential.configuration_version if credential else None
        if runtime is None or runtime.status == "deleted":
            runtime = WorkspaceRuntime(
                workspace_id=deployment.workspace_id,
                runtime_template_id=deployment.template_id,
                name=f"mcp-{server.id}",
                execution_mode="persistent",
                status="queued",
                capabilities={"managed_mcp_server_id": str(server.id)},
                limits={},
                network_policy={},
            )
            session.add(runtime)
            session.flush()
            deployment.runtime_id = runtime.id
            session.commit()
        if not runtime.docker_container_id:
            runtime = RuntimeControlService(
                session, settings, self._manager()
            ).complete_queued_runtime_create(
                workspace_id=deployment.workspace_id,
                runtime_id=runtime.id,
                template_id=deployment.template_id,
                name=runtime.name,
                limits=None,
                network_disabled=deployment.network_disabled,
                execution_mode="persistent",
            )
            if runtime is None:
                raise ValueError("MCP runtime provisioning failed")
        changed = (
            deployment.server_version != configuration_version
            or deployment.credential_version != credential_version
        )
        if deployment.action == "restart" or changed:
            # Stop clears the private socket, session and all child processes.
            self._manager().require().stop_runtime(runtime)
        if runtime.status != "running" or not self.context.docker_client().container_running(
            runtime.docker_container_id or ""
        ):
            self._manager().require().start_runtime(runtime)
        server_config = {
            key: value
            for key, value in server.connection.items()
            if key in {"command", "args", "cwd", "restart_policy"}
        }
        environment = hosted_stdio_environment(
            [credential] if credential else [],
            secret_service=self.context.secret_service(context="MCP process"),
        )
        server_config["env"] = {
            "HOME": "/workspace",
            "UV_CACHE_DIR": "/workspace/.cache/uv",
            "npm_config_cache": "/workspace/.cache/npm",
            **environment,
        }
        process_request(
            self.context.docker_client(),
            runtime,
            server.id,
            {"action": "start", "server": server_config},
        )
        discovered = process_request(
            self.context.docker_client(), runtime, server.id, {"action": "discover"}
        )
        tools = discovered.get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise ValueError("Invalid MCP discovery response")
        McpToolDiscoveryService(session).persist_runtime_tools(
            workspace_id=deployment.workspace_id,
            server=server,
            tools=tools,
            credential_id=credential.id if credential else None,
            credential_version=credential_version,
            actor_user_id=user.user_id,
            configuration_version=configuration_version,
        )
        deployment.server_version = server.configuration_version
        deployment.credential_version = credential_version
        self._finish(deployment, server, "running", user.user_id)

    def _finish(
        self, deployment: McpDeployment, server: McpServer, status: str, user_id: object
    ) -> None:
        from uuid import UUID

        deployment.status = status
        deployment.checked_at = datetime.now(UTC)
        deployment.last_error = None
        if status == "stopped":
            server.health_status = "unknown"
            server.last_error = "mcp_process_stopped"
            server.last_health_check_at = deployment.checked_at
        if isinstance(user_id, UUID):
            AuditService(self.context.session).record_user_action(
                workspace_id=deployment.workspace_id,
                user_id=user_id,
                action=f"mcp_process.{status}",
                target_type="mcp_server",
                target_id=server.id,
                metadata={
                    "runtime_id": str(deployment.runtime_id),
                    "generation": deployment.generation,
                },
            )
        self.context.session.commit()
