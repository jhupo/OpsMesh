from backend.app.runtime.environment.models import WorkspaceRuntime


def require_container(runtime: WorkspaceRuntime) -> None:
    if not runtime.docker_container_id:
        raise ValueError("Runtime has no Docker container")
