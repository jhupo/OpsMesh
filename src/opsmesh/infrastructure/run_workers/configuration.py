"""Worker-specific deployment values without a dependency on application bootstrap."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .kubernetes.configuration import KubernetesWorkerConfiguration
from .status import WorkerMode


@dataclass(frozen=True)
class WorkerLaunchConfiguration:
    data_directory: Path
    database_url: str
    kubernetes_worker: KubernetesWorkerConfiguration | None
    settings_payload: Callable[[WorkerMode], str]
