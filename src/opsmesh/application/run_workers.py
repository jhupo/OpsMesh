"""Admission-aware scheduling of durable Run workers."""

from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics
from opsmesh.modules.configuration.api import PlatformConfiguration
from opsmesh.modules.runtime.api import AgentRuntime

from .ports import RunWorkerController


class RunWorkerCoordinator:
    def __init__(
        self,
        runtime: AgentRuntime,
        launcher: RunWorkerController,
        configuration: PlatformConfiguration,
        metrics: PlatformMetrics,
    ) -> None:
        self.runtime, self.launcher = runtime, launcher
        self.configuration, self.metrics = configuration, metrics

    async def launch_runs(self) -> None:
        await self.launcher.reap()
        policy = await self.configuration.admission_policy()
        await self.runtime.recover_stale()
        active = await self.runtime.count_runs(("preparing", "running", "finalizing"))
        self.metrics.gauge("active_runs", active)
        self.metrics.gauge("queued_runs", await self.runtime.count_runs(("queued", "resuming")))
        runs = await self.runtime.list_runs(
            statuses=("queued", "resuming"),
            limit=min(1000, max(1, policy.max_active_runs - active)),
        )
        for run in runs:
            if run.status not in {"queued", "resuming"} or active >= policy.max_active_runs:
                continue
            process = await self.launcher.poll(run.id)
            if process is not None and process.status == "exited" and process.exit_code:
                current = await self.runtime.worker_exited(
                    run.id, process.exit_code, launch_token=process.launch_token
                )
                if current.status not in {"queued", "resuming"}:
                    continue
            await self.launcher.launch(run.id)
            active += 1
