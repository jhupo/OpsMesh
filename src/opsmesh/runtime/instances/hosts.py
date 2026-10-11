"""Physical node capacity shared by independent workspace Runtime policies."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.contracts import DockerRuntimeClient, RuntimeLimits
from opsmesh.runtime.instances.models import RuntimeHost, RuntimeTemplate, WorkspaceRuntime


class RuntimeHostStore:
    def __init__(self, session: Session, docker: DockerRuntimeClient) -> None:
        self.session = session
        self.docker = docker

    def acquire(
        self, runtime: WorkspaceRuntime, template: RuntimeTemplate, limits: RuntimeLimits
    ) -> tuple[RuntimeHost, bool]:
        # Image and filesystem trust remain physical host boundaries. Network
        # allowlists deliberately do not participate in host identity.
        key = hashlib.sha256(
            json.dumps(
                {
                    "image": template.image,
                    "space": str(runtime.runtime_space_id),
                    "limits": {
                        "cpu": limits.cpu_count,
                        "memory": limits.memory_mb,
                        "disk": limits.disk_mb,
                        "processes": limits.max_processes,
                        "capacity": limits.max_concurrent_executions,
                    },
                    "isolated": str(runtime.id) if runtime.execution_mode == "isolated" else None,
                    "plugin": runtime.capabilities.get("plugin_install_id"),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        node = self.docker.node_identity()
        # WorkspaceQuotaPolicy has already locked the workspace row. This also
        # serializes creation of its physical host across Worker processes.
        host = self.session.scalar(
            select(RuntimeHost).where(
                RuntimeHost.workspace_id == runtime.workspace_id,
                RuntimeHost.node_id == node,
                RuntimeHost.host_key == key,
                RuntimeHost.status.not_in(["deleted", "failed"]),
            )
        )
        if host is not None:
            runtime.host = host
            self.session.flush()
            return host, False
        host = RuntimeHost(
            workspace_id=runtime.workspace_id,
            node_id=node,
            host_key=key,
            image=template.image,
            capacity=limits.max_concurrent_executions,
            status="provisioning",
            provisioning_owner_id=runtime.id,
        )
        self.session.add(host)
        self.session.flush([host])
        runtime.host = host
        self.session.flush([runtime])
        return host, True

    def bindings(self, runtime: WorkspaceRuntime) -> list[WorkspaceRuntime]:
        if runtime.host_id is None:
            return []
        return list(
            self.session.scalars(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == runtime.workspace_id,
                    WorkspaceRuntime.host_id == runtime.host_id,
                    WorkspaceRuntime.status != "deleted",
                )
            )
        )
