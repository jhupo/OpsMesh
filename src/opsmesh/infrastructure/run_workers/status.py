"""A process or cluster Job observed by a Run worker launcher."""

from typing import Literal

from opsmesh.framework.typing import OpsMeshContract

WorkerMode = Literal["inline", "subprocess", "docker", "kubernetes"]


class WorkerProcessStatus(OpsMeshContract):
    run_id: str
    mode: WorkerMode
    status: Literal["running", "exited"]
    pid: int | None = None
    exit_code: int | None = None
    launch_token: str | None = None
    job_name: str | None = None
    reason: str | None = None
