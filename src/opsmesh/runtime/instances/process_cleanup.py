from sqlalchemy.orm import object_session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore, allocation_identity
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.instances.models import WorkspaceRuntime


class RunProcessCleanupService:
    """Reap one run's process group and files; failures retain its occupied slot."""

    def __init__(self, docker_client: DockerRuntimeClient | None) -> None:
        self._docker = docker_client

    def reset(
        self, host: WorkspaceRuntime, run: AgentRun, *, retain_workspace: bool = False
    ) -> None:
        if self._docker is None or host.docker_container_id is None:
            raise RuntimeError("Runtime host is unavailable")
        session = object_session(host)
        if session is None:
            raise RuntimeError("Runtime allocation session is unavailable")
        allocation = RuntimeAllocationStore(session).get(host, "run", run.id)
        if allocation is not None:
            self._docker.revoke_execution(host.docker_container_id, allocation_identity(allocation))
        commands: list[list[str]] = []
        if retain_workspace:
            commands.extend(
                [
                    ["chown", "-R", "0:0", f"/workspace/runs/{run.id}"],
                    ["chmod", "700", f"/workspace/runs/{run.id}"],
                ]
            )
        if not retain_workspace:
            commands.append(["rm", "-rf", "--", f"/workspace/runs/{run.id}"])
        for command in commands:
            result = self._docker.exec_command(
                host.docker_container_id, command, 30, working_dir="/"
            )
            if result.exit_code:
                raise RuntimeError("Run process or workspace cleanup failed")
