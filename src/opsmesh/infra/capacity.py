import logging
import math
import sys
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

TASK_MEMORY_BYTES = 1024**3
MAX_CONCURRENCY = 64


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None


def _cgroup_limits(cpus: float, memory: int) -> tuple[float, int]:
    root = Path("/sys/fs/cgroup")
    if not (root / "cgroup.controllers").exists():
        membership = _read(Path("/proc/self/cgroup"))
        if membership:
            raise RuntimeError(
                "Automatic concurrency requires cgroup v2; set OPSMESH_WORKER_CONCURRENCY explicitly"
            )
        return cpus, memory

    membership = _read(Path("/proc/self/cgroup"))
    if membership is None:
        raise RuntimeError("Cannot read cgroup membership; set OPSMESH_WORKER_CONCURRENCY")
    group = next((line[3:] for line in membership.splitlines() if line.startswith("0::")), None)
    if group is None:
        raise RuntimeError("Cannot locate cgroup v2 membership; set OPSMESH_WORKER_CONCURRENCY")

    # A container's cgroup namespace can hide the membership path above its mount root.
    current = root.joinpath(*[part for part in group.split("/") if part not in {"", ".", ".."}])
    while not current.is_dir() and current != root:
        current = current.parent
    while True:
        cpu_limit = _read(current / "cpu.max")
        if cpu_limit is not None:
            quota, period = cpu_limit.split()
            if quota != "max":
                cpus = min(cpus, int(quota) / int(period))
        memory_limit = _read(current / "memory.max")
        if memory_limit is not None and memory_limit != "max":
            usage = _read(current / "memory.current")
            if usage is None:
                raise RuntimeError(
                    "Cannot read cgroup memory usage; set OPSMESH_WORKER_CONCURRENCY"
                )
            memory = min(memory, max(0, int(memory_limit) - int(usage)))
        if current == root:
            break
        current = current.parent
    return cpus, memory


def worker_concurrency(configured: int | None) -> int:
    if configured is not None:
        logger.info("task_worker_capacity mode=manual concurrency=%d", configured)
        return configured

    detected_cpus = psutil.cpu_count()
    if detected_cpus is None:
        raise RuntimeError("Cannot detect CPU count; set OPSMESH_WORKER_CONCURRENCY")
    cpus = float(detected_cpus)
    if sys.platform in {"linux", "win32", "freebsd"}:
        cpus = min(cpus, float(len(psutil.Process().cpu_affinity())))
    memory = psutil.virtual_memory().available
    if sys.platform == "linux":
        cpus, memory = _cgroup_limits(cpus, memory)

    # Model calls are mostly I/O; keep 25% memory headroom and budget 1 GiB per slot.
    cpu_slots = max(1, math.floor(cpus * 2))
    memory_slots = max(1, memory * 3 // 4 // TASK_MEMORY_BYTES)
    concurrency = min(MAX_CONCURRENCY, cpu_slots, memory_slots)
    logger.info(
        "task_worker_capacity mode=auto concurrency=%d cpus=%.2f available_memory_bytes=%d",
        concurrency,
        cpus,
        memory,
    )
    return concurrency
