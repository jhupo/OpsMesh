from dataclasses import dataclass, field
from threading import Lock

DEFAULT_HTTP_DURATION_BUCKETS = (10, 50, 100, 250, 500, 1000, 2500, 5000)


@dataclass
class MetricsRegistry:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=dict)
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict[float, int]] = field(
        default_factory=dict
    )
    _lock: Lock = field(default_factory=Lock)

    def increment(
        self,
        name: str,
        *,
        labels: dict[str, object] | None = None,
        amount: int = 1,
    ) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + amount

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...],
        labels: dict[str, object] | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            histogram = self.histograms.setdefault(key, {})
            for bucket in buckets:
                if value <= bucket:
                    histogram[bucket] = histogram.get(bucket, 0) + 1
            histogram[float("inf")] = histogram.get(float("inf"), 0) + 1

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}{_render_labels(dict(labels))} {value}")
            for (name, labels), buckets in sorted(self.histograms.items()):
                label_dict = dict(labels)
                for bucket, value in sorted(buckets.items()):
                    rendered_bucket = "+Inf" if bucket == float("inf") else _format_number(bucket)
                    labels_with_bucket = label_dict | {"le": rendered_bucket}
                    lines.append(
                        f"{name}_bucket{_render_labels(labels_with_bucket)} {value}"
                    )
        return "\n".join(lines) + ("\n" if lines else "")

    def clear(self) -> None:
        with self._lock:
            self.counters.clear()
            self.histograms.clear()


metrics_registry = MetricsRegistry()


def record_http_request(method: str, path: str, status_code: int, duration_ms: int) -> None:
    labels = {
        "method": method,
        "path": _normalize_path(path),
        "status": str(status_code),
    }
    metrics_registry.increment("chaincloud_http_requests_total", labels=labels)
    metrics_registry.observe(
        "chaincloud_http_request_duration_ms",
        float(duration_ms),
        buckets=DEFAULT_HTTP_DURATION_BUCKETS,
        labels=labels,
    )


def _metric_key(
    name: str,
    labels: dict[str, object] | None,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    normalized_labels = tuple(
        sorted((key, str(value)) for key, value in (labels or {}).items())
    )
    return name, normalized_labels


def _render_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{key}="{_escape_label_value(value)}"' for key, value in sorted(labels.items())
    )
    return "{" + rendered + "}"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _normalize_path(path: str) -> str:
    return path or "/"
