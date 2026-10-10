from backend.app.orchestration.runs.models import AgentRun
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.models import WorkspaceRuntime


class RunProcessCleanupService:
    """Reap one run's process group and files; failures retain its occupied slot."""

    def __init__(self, docker_client: DockerRuntimeClient | None) -> None:
        self._docker = docker_client

    def reset(
        self, host: WorkspaceRuntime, run: AgentRun, *, retain_workspace: bool = False
    ) -> None:
        if self._docker is None or host.docker_container_id is None:
            raise RuntimeError("Runtime host is unavailable")
        commands = [["python", "-m", "backend.app.runtime.agent_host.processes", str(run.id)]]
        if not retain_workspace:
            commands.append(["rm", "-rf", "--", f"/workspace/runs/{run.id}"])
        for command in commands:
            result = self._docker.exec_command(
                host.docker_container_id, command, 30, working_dir="/"
            )
            if result.exit_code:
                raise RuntimeError("Run process or workspace cleanup failed")
