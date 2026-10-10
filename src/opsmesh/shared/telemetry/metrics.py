from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal, cast

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector
from prometheus_client.registry import Collector

DEFAULT_HTTP_DURATION_BUCKETS = (10, 50, 100, 250, 500, 1000, 2500, 5000)
HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
MetricKind = Literal["counter", "gauge", "histogram"]
PrometheusMetric = Counter | Gauge | Histogram


@dataclass(frozen=True)
class GaugeMetric:
    name: str
    value: float
    labels: Mapping[str, object] = field(default_factory=dict)
    help_text: str | None = None


class DomainGaugeCollector(Collector):
    """Expose request-time Postgres and Redis snapshots through the official client."""

    def __init__(self, gauges: Iterable[GaugeMetric]) -> None:
        self._gauges = tuple(gauges)

    def collect(self) -> Iterable[GaugeMetricFamily]:
        grouped: dict[tuple[str, str, tuple[str, ...]], list[GaugeMetric]] = defaultdict(list)
        for gauge in self._gauges:
            label_names = tuple(sorted(gauge.labels))
            grouped[
                (
                    gauge.name,
                    gauge.help_text or f"{gauge.name} metric.",
                    label_names,
                )
            ].append(gauge)

        for (name, help_text, label_names), gauges in sorted(grouped.items()):
            family = GaugeMetricFamily(name, help_text, labels=list(label_names))
            for gauge in gauges:
                labels = _string_labels(gauge.labels)
                family.add_metric([labels[name] for name in label_names], float(gauge.value))
            yield family


@dataclass
class MetricsRegistry:
    _registry: CollectorRegistry = field(default_factory=lambda: _new_registry())
    _metrics: dict[tuple[MetricKind, str, tuple[str, ...]], PrometheusMetric] = field(
        default_factory=dict
    )
    _lock: Lock = field(default_factory=Lock)

    def increment(
        self,
        name: str,
        *,
        labels: Mapping[str, object] | None = None,
        amount: int = 1,
        help_text: str | None = None,
    ) -> None:
        normalized = _string_labels(labels or {})
        metric = cast(
            Counter,
            self._metric("counter", name, tuple(sorted(normalized)), help_text),
        )
        metric.labels(**normalized).inc(amount)

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, object] | None = None,
        help_text: str | None = None,
    ) -> None:
        normalized = _string_labels(labels or {})
        metric = cast(
            Gauge,
            self._metric("gauge", name, tuple(sorted(normalized)), help_text),
        )
        metric.labels(**normalized).set(float(value))

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...],
        labels: Mapping[str, object] | None = None,
        help_text: str | None = None,
    ) -> None:
        normalized = _string_labels(labels or {})
        metric = cast(
            Histogram,
            self._metric(
                "histogram",
                name,
                tuple(sorted(normalized)),
                help_text,
                buckets=buckets,
            ),
        )
        metric.labels(**normalized).observe(float(value))

    def render_prometheus(self, *, gauges: Iterable[GaugeMetric] = ()) -> str:
        rendered = generate_latest(self._registry)
        domain_gauges = tuple(gauges)
        if domain_gauges:
            registry = CollectorRegistry(auto_describe=True)
            registry.register(DomainGaugeCollector(domain_gauges))
            rendered += generate_latest(registry)
        return rendered.decode("utf-8")

    def clear(self) -> None:
        with self._lock:
            self._registry = _new_registry()
            self._metrics.clear()

    def _metric(
        self,
        kind: MetricKind,
        name: str,
        label_names: tuple[str, ...],
        help_text: str | None,
        *,
        buckets: tuple[float, ...] = (),
    ) -> PrometheusMetric:
        key = (kind, name, label_names)
        with self._lock:
            existing = self._metrics.get(key)
            if existing is not None:
                return existing
            description = help_text or f"{name} metric."
            if kind == "counter":
                metric: PrometheusMetric = Counter(
                    name,
                    description,
                    labelnames=label_names,
                    registry=self._registry,
                )
            elif kind == "gauge":
                metric = Gauge(
                    name,
                    description,
                    labelnames=label_names,
                    registry=self._registry,
                )
            else:
                metric = Histogram(
                    name,
                    description,
                    labelnames=label_names,
                    buckets=buckets,
                    registry=self._registry,
                )
            self._metrics[key] = metric
            return metric


def _new_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)
    return registry


metrics_registry = MetricsRegistry()


def record_http_request(method: str, path: str, status_code: int, duration_ms: int) -> None:
    labels = {
        "method": method if method in HTTP_METHODS else "OTHER",
        "path": _normalize_path(path),
        "status": str(status_code),
    }
    metrics_registry.increment(
        "opsmesh_http_requests_total",
        labels=labels,
        help_text="Total HTTP requests served by the API.",
    )
    metrics_registry.observe(
        "opsmesh_http_request_duration_ms",
        float(duration_ms),
        buckets=DEFAULT_HTTP_DURATION_BUCKETS,
        labels=labels,
        help_text="HTTP request duration in milliseconds.",
    )


def _string_labels(labels: Mapping[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in labels.items()}


def _normalize_path(path: str) -> str:
    return path or "/"
