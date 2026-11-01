"""Process metrics with bounded, low-cardinality labels."""

from collections import Counter
from math import isfinite
from threading import Lock
from time import time
from typing import Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract

COUNTERS = frozenset({"http_requests", "runs", "outbox_events", "tool_calls", "memory_jobs"})
GAUGES = frozenset({"active_runs", "queued_runs", "pending_memory_jobs"})
HISTOGRAMS = frozenset(
    {
        "http_duration_seconds",
        "tool_duration_seconds",
        "model_duration_seconds",
        "run_duration_seconds",
        "queue_wait_seconds",
        "first_token_seconds",
        "memory_duration_seconds",
        "approval_wait_seconds",
    }
)
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)


class MetricsSnapshot(OpsMeshContract):
    version: Literal[1] = 1
    counters: dict[str, dict[str, int]] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    gauge_timestamps: dict[str, float] = Field(default_factory=dict)
    histograms: dict[str, list[float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_names_and_values(self) -> Self:
        if (
            set(self.counters) - COUNTERS
            or set(self.gauges) - GAUGES
            or set(self.histograms) - HISTOGRAMS
            or set(self.gauge_timestamps) - set(self.gauges)
        ):
            raise ValueError("Metrics snapshot contains undeclared names")
        for outcomes in self.counters.values():
            if set(outcomes) - {"success", "failure", "rejected"} or any(
                v < 0 for v in outcomes.values()
            ):
                raise ValueError("Invalid counter labels or values")
        for cells in self.histograms.values():
            if len(cells) != len(BUCKETS) + 2 or any(not isfinite(v) or v < 0 for v in cells):
                raise ValueError("Invalid histogram cells")
            if cells[:-1] != sorted(cells[:-1]):
                raise ValueError("Histogram bucket counts must be cumulative")
        if any(not isfinite(v) or v < 0 for v in self.gauges.values()):
            raise ValueError("Invalid gauge values")
        if any(not isfinite(v) or v < 0 for v in self.gauge_timestamps.values()):
            raise ValueError("Invalid gauge observation times")
        return self


class PlatformMetrics:
    def __init__(self) -> None:
        self._counters: Counter[tuple[str, str]] = Counter()
        self._lock = Lock()
        self._gauges: dict[str, float] = {}
        self._gauge_timestamps: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._buckets = BUCKETS

    def gauge(self, metric: str, value: float) -> None:
        if metric not in GAUGES:
            raise ValueError("Gauge is not declared")
        if not isfinite(value) or value < 0:
            raise ValueError("Gauge value must be finite and nonnegative")
        with self._lock:
            self._gauges[metric] = value
            self._gauge_timestamps[metric] = time()

    def observe(self, metric: str, value: float) -> None:
        if metric not in HISTOGRAMS:
            raise ValueError("Histogram is not declared")
        if not isfinite(value) or value < 0:
            raise ValueError("Observation must be finite and nonnegative")
        with self._lock:
            cells = self._histograms.setdefault(metric, [0.0] * (len(self._buckets) + 2))
            for index, upper in enumerate(self._buckets):
                cells[index] += int(value <= upper)
            cells[-2] += 1
            cells[-1] += value

    def increment(self, metric: str, outcome: str = "success") -> None:
        if metric not in COUNTERS:
            raise ValueError("Metric is not declared")
        outcome = outcome if outcome in {"success", "failure", "rejected"} else "failure"
        with self._lock:
            self._counters[(metric, outcome)] += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters: dict[str, dict[str, int]] = {}
            for (name, outcome), value in self._counters.items():
                counters.setdefault(name, {})[outcome] = value
            return MetricsSnapshot(
                counters=counters,
                gauges=dict(self._gauges),
                gauge_timestamps=dict(self._gauge_timestamps),
                histograms={name: list(values) for name, values in self._histograms.items()},
            )

    def include_snapshot(self, snapshot: MetricsSnapshot) -> None:
        """Sum cumulative observations once per process; gauges are selected separately."""
        with self._lock:
            for name, outcomes in snapshot.counters.items():
                for outcome, value in outcomes.items():
                    self._counters[(name, outcome)] += value
            for name, values in snapshot.histograms.items():
                cells = self._histograms.setdefault(name, [0.0] * (len(BUCKETS) + 2))
                for index, cell_value in enumerate(values):
                    cells[index] += cell_value

    def render(self) -> str:
        with self._lock:
            lines = [
                f'opsmesh_{name}_total{{outcome="{outcome}"}} {count}'
                for (name, outcome), count in sorted(self._counters.items())
            ]
            lines.extend(
                f"opsmesh_{name} {value:g}" for name, value in sorted(self._gauges.items())
            )
            for name, values in sorted(self._histograms.items()):
                lines.append(f"# TYPE opsmesh_{name} histogram")
                for upper, count in zip(self._buckets, values, strict=False):
                    lines.append(f'opsmesh_{name}_bucket{{le="{upper:g}"}} {count:g}')
                lines.extend(
                    [
                        f'opsmesh_{name}_bucket{{le="+Inf"}} {values[-2]:g}',
                        f"opsmesh_{name}_count {values[-2]:g}",
                        f"opsmesh_{name}_sum {values[-1]:g}",
                    ]
                )
            return "\n".join(lines) + "\n"
