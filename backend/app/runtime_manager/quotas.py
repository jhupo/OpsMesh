from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class RuntimeQuota:
    max_runtime_cpu: float = 4
    max_runtime_memory_mb: int = 8192
    max_runtime_disk_mb: int = 20_480
    max_runtime_timeout_seconds: int = 3600
    max_runtime_output_bytes: int = 2_000_000
    max_runtime_processes: int = 512
    max_active_runtimes: int = 8
    max_total_cpu: float = 16
    max_total_memory_mb: int = 32_768
    max_total_disk_mb: int = 102_400


@dataclass(frozen=True)
class RuntimeUsage:
    active_runtimes: int
    total_cpu: float
    total_memory_mb: int
    total_disk_mb: int


class RuntimeQuotaExceededError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeQuotaPolicy:
    _COUNTED_STATUSES = {"created", "running", "stopped"}

    def __init__(self, session: Session) -> None:
        self._session = session

    def assert_can_create_runtime(self, workspace_id: UUID, limits: RuntimeLimits) -> RuntimeQuota:
        quota = self.quota_for_workspace(workspace_id)
        self._validate_single_runtime(limits, quota)
        usage = self.usage_for_workspace(workspace_id)
        self._validate_total_usage(limits, usage, quota)
        return quota

    def quota_for_workspace(self, workspace_id: UUID) -> RuntimeQuota:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return RuntimeQuota()
        raw_quota = workspace.settings.get("runtime_quota", {})
        if not isinstance(raw_quota, dict):
            return RuntimeQuota()
        defaults = RuntimeQuota()
        return RuntimeQuota(
            max_runtime_cpu=_float_setting(raw_quota, "max_runtime_cpu", defaults.max_runtime_cpu),
            max_runtime_memory_mb=_int_setting(
                raw_quota,
                "max_runtime_memory_mb",
                defaults.max_runtime_memory_mb,
            ),
            max_runtime_disk_mb=_int_setting(
                raw_quota,
                "max_runtime_disk_mb",
                defaults.max_runtime_disk_mb,
            ),
            max_runtime_timeout_seconds=_int_setting(
                raw_quota,
                "max_runtime_timeout_seconds",
                defaults.max_runtime_timeout_seconds,
            ),
            max_runtime_output_bytes=_int_setting(
                raw_quota,
                "max_runtime_output_bytes",
                defaults.max_runtime_output_bytes,
            ),
            max_runtime_processes=_int_setting(
                raw_quota,
                "max_runtime_processes",
                defaults.max_runtime_processes,
            ),
            max_active_runtimes=_int_setting(
                raw_quota,
                "max_active_runtimes",
                defaults.max_active_runtimes,
            ),
            max_total_cpu=_float_setting(raw_quota, "max_total_cpu", defaults.max_total_cpu),
            max_total_memory_mb=_int_setting(
                raw_quota,
                "max_total_memory_mb",
                defaults.max_total_memory_mb,
            ),
            max_total_disk_mb=_int_setting(
                raw_quota,
                "max_total_disk_mb",
                defaults.max_total_disk_mb,
            ),
        )

    def usage_for_workspace(self, workspace_id: UUID) -> RuntimeUsage:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status.in_(self._COUNTED_STATUSES),
            )
        ).all()
        return RuntimeUsage(
            active_runtimes=len(runtimes),
            total_cpu=sum(_float_limit(runtime.limits, "cpu_count") for runtime in runtimes),
            total_memory_mb=sum(_int_limit(runtime.limits, "memory_mb") for runtime in runtimes),
            total_disk_mb=sum(_int_limit(runtime.limits, "disk_mb") for runtime in runtimes),
        )

    def _validate_single_runtime(self, limits: RuntimeLimits, quota: RuntimeQuota) -> None:
        if limits.cpu_count <= 0:
            raise RuntimeQuotaExceededError("runtime_cpu_invalid", "Runtime CPU must be positive")
        if limits.memory_mb <= 0:
            raise RuntimeQuotaExceededError(
                "runtime_memory_invalid",
                "Runtime memory must be positive",
            )
        if limits.disk_mb <= 0:
            raise RuntimeQuotaExceededError("runtime_disk_invalid", "Runtime disk must be positive")
        if limits.timeout_seconds <= 0:
            raise RuntimeQuotaExceededError(
                "runtime_timeout_invalid",
                "Runtime timeout must be positive",
            )
        if limits.cpu_count > quota.max_runtime_cpu:
            raise RuntimeQuotaExceededError(
                "runtime_cpu_quota_exceeded",
                "Runtime CPU exceeds quota",
            )
        if limits.memory_mb > quota.max_runtime_memory_mb:
            raise RuntimeQuotaExceededError(
                "runtime_memory_quota_exceeded",
                "Runtime memory exceeds quota",
            )
        if limits.disk_mb > quota.max_runtime_disk_mb:
            raise RuntimeQuotaExceededError(
                "runtime_disk_quota_exceeded",
                "Runtime disk exceeds quota",
            )
        if limits.timeout_seconds > quota.max_runtime_timeout_seconds:
            raise RuntimeQuotaExceededError(
                "runtime_timeout_quota_exceeded",
                "Runtime timeout exceeds quota",
            )
        if limits.max_output_bytes <= 0:
            raise RuntimeQuotaExceededError(
                "runtime_output_invalid",
                "Runtime output limit must be positive",
            )
        if limits.max_output_bytes > quota.max_runtime_output_bytes:
            raise RuntimeQuotaExceededError(
                "runtime_output_quota_exceeded",
                "Runtime output limit exceeds quota",
            )
        if limits.max_processes <= 0:
            raise RuntimeQuotaExceededError(
                "runtime_process_limit_invalid",
                "Runtime process limit must be positive",
            )
        if limits.max_processes > quota.max_runtime_processes:
            raise RuntimeQuotaExceededError(
                "runtime_process_quota_exceeded",
                "Runtime process limit exceeds quota",
            )

    def _validate_total_usage(
        self,
        limits: RuntimeLimits,
        usage: RuntimeUsage,
        quota: RuntimeQuota,
    ) -> None:
        if usage.active_runtimes + 1 > quota.max_active_runtimes:
            raise RuntimeQuotaExceededError(
                "runtime_count_quota_exceeded",
                "Workspace active runtime count exceeds quota",
            )
        if usage.total_cpu + limits.cpu_count > quota.max_total_cpu:
            raise RuntimeQuotaExceededError(
                "runtime_total_cpu_quota_exceeded",
                "Workspace runtime CPU total exceeds quota",
            )
        if usage.total_memory_mb + limits.memory_mb > quota.max_total_memory_mb:
            raise RuntimeQuotaExceededError(
                "runtime_total_memory_quota_exceeded",
                "Workspace runtime memory total exceeds quota",
            )
        if usage.total_disk_mb + limits.disk_mb > quota.max_total_disk_mb:
            raise RuntimeQuotaExceededError(
                "runtime_total_disk_quota_exceeded",
                "Workspace runtime disk total exceeds quota",
            )


def _float_setting(settings: dict[str, object], key: str, default: float) -> float:
    value = settings.get(key, default)
    return value if isinstance(value, int | float) else default


def _int_setting(settings: dict[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    return value if isinstance(value, int) else default


def _float_limit(limits: dict[str, object], key: str) -> float:
    value = limits.get(key, 0)
    return value if isinstance(value, int | float) else 0


def _int_limit(limits: dict[str, object], key: str) -> int:
    value = limits.get(key, 0)
    return value if isinstance(value, int) else 0
