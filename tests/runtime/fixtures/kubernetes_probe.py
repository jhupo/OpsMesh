"""Trusted, offline backend copied into the explicit integration test's shared PVC."""

import asyncio
import os
import socket

from opsmesh.dependencies import get_runtime_extensions
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentRunCompleted,
    AgentRunInterrupted,
    PendingToolApproval,
)


class KubernetesProbeBackend:
    id = "kubernetes_probe"
    version = "1"
    features = AgentBackendCapabilities(approvals=True, resume=True)

    async def run(self, spec, input, context, *, checkpoint=None, decisions=None, emit=None):
        print(f"opsmesh-probe run={context.run_id} resumed={bool(checkpoint)}", flush=True)
        if input == "wait":
            await asyncio.Event().wait()
        if input == "approval" and checkpoint is None:
            return AgentRunInterrupted(
                pending=(
                    PendingToolApproval(
                        id="probe-approval",
                        tool_name="probe",
                        agent_name=spec.name,
                    ),
                ),
                checkpoint={"phase": 1},
            )
        return AgentRunCompleted(
            output={
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "input": input,
                "resumed": bool(checkpoint),
                "approved": (decisions or {}).get("probe-approval"),
            }
        )


get_runtime_extensions().register_backend("kubernetes_probe", KubernetesProbeBackend)
