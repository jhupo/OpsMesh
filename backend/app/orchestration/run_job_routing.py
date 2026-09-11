from sqlalchemy.orm import Session

from backend.app.orchestration.resource_usage import positive_numeric_usage
from backend.app.orchestration.run_request.utils import string_list
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.tasks.models import Task


class RunJobRoutingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def routing(self, run: AgentRun) -> dict[str, object]:
        routing: dict[str, object] = {}
        priority = self.priority(run)
        if priority:
            routing["priority"] = priority
        if run.runtime_id is not None:
            routing["workspace_runtime_id"] = str(run.runtime_id)
        if run.runtime_space_id is None:
            return routing
        routing["runtime_space_id"] = str(run.runtime_space_id)
        runtime_space = self._session.get(RuntimeSpace, run.runtime_space_id)
        if runtime_space is None or runtime_space.workspace_id != run.workspace_id:
            return routing

        runtime_modes = string_list(runtime_space.policy.get("runtime_modes"))
        runtime_mode = runtime_space.policy.get("runtime_mode")
        if not runtime_modes and isinstance(runtime_mode, str):
            runtime_modes = [runtime_mode]
        capabilities = string_list(runtime_space.policy.get("worker_capabilities"))
        worker_types = string_list(runtime_space.policy.get("worker_types"))
        resource_requirements = positive_numeric_usage(
            runtime_space.policy.get("resource_requirements"),
        )
        if runtime_modes:
            routing["runtime_modes"] = runtime_modes
        if capabilities:
            routing["capabilities"] = capabilities
        if worker_types:
            routing["worker_types"] = worker_types
        if resource_requirements:
            routing["resource_requirements"] = resource_requirements
        return routing

    def priority(self, run: AgentRun) -> int:
        if run.task_id is None:
            return 0
        task = self._session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id:
            return 0
        return int(task.priority or 0)
