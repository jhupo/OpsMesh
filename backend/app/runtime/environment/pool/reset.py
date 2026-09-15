from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.environment.leases import RuntimeLeaseStore
from backend.app.runtime.environment.manager import RuntimeManager
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.environment.pool.policy import runtime_timeout
from backend.app.runtime.environment.project_files import RUNTIME_WORKSPACE_ROOT


class RuntimePoolResetService:
    """Sanitize a leased member before reuse and quarantine it on any reset failure."""

    def __init__(self, session: Session, docker_client: DockerRuntimeClient | None) -> None:
        self._session = session
        self._docker = docker_client

    def reset(self, member: WorkspaceRuntime, run: AgentRun) -> None:
        if self._docker is None or member.docker_container_id is None:
            raise RuntimeError("Pooled runtime container is unavailable")
        timeout_seconds = runtime_timeout(member.limits)
        process_cleanup = self._docker.exec_command(
            member.docker_container_id,
            [
                "sh",
                "-c",
                (
                    "for proc in /proc/[0-9]*; do "
                    'pid="${proc##*/}"; '
                    '[ "$pid" -gt 1 ] && [ "$pid" -ne "$$" ] && '
                    'kill -KILL "$pid" 2>/dev/null || true; '
                    "done"
                ),
            ],
            timeout_seconds,
            working_dir="/",
        )
        if process_cleanup.exit_code != 0:
            raise RuntimeError("Pooled runtime process cleanup failed")
        workspace_cleanup = self._docker.exec_command(
            member.docker_container_id,
            ["rm", "-rf", "--", f"{RUNTIME_WORKSPACE_ROOT}/runs/{run.id}"],
            timeout_seconds,
            working_dir="/",
        )
        if workspace_cleanup.exit_code != 0:
            raise RuntimeError("Pooled runtime workspace cleanup failed")

    def destroy_failed_member(
        self,
        member: WorkspaceRuntime,
        child: WorkspaceRuntime,
    ) -> bool:
        if self._docker is None:
            member.status = child.status = "cleanup_failed"
            member.connection_status = child.connection_status = "offline"
            return False
        try:
            RuntimeManager(self._session, self._docker).delete_runtime(
                member,
                allow_active_pool_lease=True,
            )
        except Exception:
            member.status = "cleanup_failed"
            member.connection_status = "offline"
        deleted = member.status == "deleted"
        child.status = "deleted" if deleted else "cleanup_failed"
        child.connection_status = "offline"
        RuntimeLeaseStore(self._session).set_status(
            child,
            "released" if deleted else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        return deleted
