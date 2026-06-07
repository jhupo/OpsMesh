from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Lock

DEFAULT_HTTP_DURATION_BUCKETS = (10, 50, 100, 250, 500, 1000, 2500, 5000)


@dataclass(frozen=True)
class GaugeMetric:
    name: str
    value: float
    labels: dict[str, object] = field(default_factory=dict)
    help_text: str | None = None


@dataclass
class HistogramState:
    buckets: dict[float, int] = field(default_factory=dict)
    count: int = 0
    total: float = 0.0


@dataclass
class MetricsRegistry:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=dict)
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], HistogramState] = field(
        default_factory=dict
    )
    help_texts: dict[str, str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def increment(
        self,
        name: str,
        *,
        labels: dict[str, object] | None = None,
        amount: int = 1,
        help_text: str | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self._remember_help(name, help_text)
            self.counters[key] = self.counters.get(key, 0) + amount

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, object] | None = None,
        help_text: str | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self._remember_help(name, help_text)
            self.gauges[key] = float(value)

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...],
        labels: dict[str, object] | None = None,
        help_text: str | None = None,
    ) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self._remember_help(name, help_text)
            histogram = self.histograms.setdefault(key, HistogramState())
            for bucket in buckets:
                if value <= bucket:
                    histogram.buckets[bucket] = histogram.buckets.get(bucket, 0) + 1
            histogram.buckets[float("inf")] = histogram.buckets.get(float("inf"), 0) + 1
            histogram.count += 1
            histogram.total += value

    def render_prometheus(self, *, gauges: Iterable[GaugeMetric] = ()) -> str:
        metric_lines: dict[str, list[str]] = {}
        metric_types: dict[str, str] = {}
        help_texts: dict[str, str] = {}
        with self._lock:
            help_texts.update(self.help_texts)
            for (name, labels), value in sorted(self.counters.items()):
                metric_types[name] = "counter"
                metric_lines.setdefault(name, []).append(
                    f"{name}{_render_labels(dict(labels))} {value}"
                )
            for (name, labels), value in sorted(self.gauges.items()):
                metric_types[name] = "gauge"
                metric_lines.setdefault(name, []).append(
                    f"{name}{_render_labels(dict(labels))} {_format_number(value)}"
                )
            for (name, labels), histogram in sorted(self.histograms.items()):
                metric_types[name] = "histogram"
                label_dict = dict(labels)
                lines = metric_lines.setdefault(name, [])
                for bucket, value in sorted(histogram.buckets.items()):
                    rendered_bucket = "+Inf" if bucket == float("inf") else _format_number(bucket)
                    labels_with_bucket = label_dict | {"le": rendered_bucket}
                    lines.append(
                        f"{name}_bucket{_render_labels(labels_with_bucket)} {value}"
                    )
                rendered_labels = _render_labels(label_dict)
                lines.append(f"{name}_count{rendered_labels} {histogram.count}")
                lines.append(f"{name}_sum{rendered_labels} {_format_number(histogram.total)}")

        for gauge in gauges:
            metric_types[gauge.name] = "gauge"
            if gauge.help_text is not None:
                help_texts[gauge.name] = gauge.help_text
            metric_lines.setdefault(gauge.name, []).append(
                f"{gauge.name}{_render_labels(_string_labels(gauge.labels))} "
                f"{_format_number(gauge.value)}"
            )

        lines: list[str] = []
        for name in sorted(metric_lines):
            help_text = _escape_help(help_texts.get(name) or _default_help(name))
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_types[name]}")
            lines.extend(sorted(metric_lines[name]))
        return "\n".join(lines) + ("\n" if lines else "")

    def clear(self) -> None:
        with self._lock:
            self.counters.clear()
            self.gauges.clear()
            self.histograms.clear()
            self.help_texts.clear()

    def _remember_help(self, name: str, help_text: str | None) -> None:
        if help_text is not None:
            self.help_texts[name] = help_text


metrics_registry = MetricsRegistry()


def record_http_request(method: str, path: str, status_code: int, duration_ms: int) -> None:
    labels = {
        "method": method,
        "path": _normalize_path(path),
        "status": str(status_code),
    }
    metrics_registry.increment(
        "chaincloud_http_requests_total",
        labels=labels,
        help_text="Total HTTP requests served by the API.",
    )
    metrics_registry.observe(
        "chaincloud_http_request_duration_ms",
        float(duration_ms),
        buckets=DEFAULT_HTTP_DURATION_BUCKETS,
        labels=labels,
        help_text="HTTP request duration in milliseconds.",
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


def _string_labels(labels: dict[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in labels.items()}


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _default_help(name: str) -> str:
    return f"{name} metric."


def _format_number(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _normalize_path(path: str) -> str:
    return path or "/"
