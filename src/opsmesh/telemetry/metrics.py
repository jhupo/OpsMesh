"""Counter, histogram and gauge metrics with a process-local registry."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Literal, cast
from weakref import WeakKeyDictionary
from wsgiref.simple_server import WSGIServer

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector
from prometheus_client.registry import Collector

MetricKind = Literal["counter", "histogram", "gauge"]
PrometheusMetric = Counter | Histogram | Gauge
MetricKey = tuple[MetricKind, str, tuple[str, ...]]


@dataclass(frozen=True)
class GaugeMetric:
    name: str
    value: float
    labels: Mapping[str, object] = field(default_factory=dict)
    help_text: str | None = None


class SnapshotGaugeCollector(Collector):
    """Expose caller-provided gauge snapshots through the official client."""

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


class LiveGaugeCollector(Collector):
    """The producer owns snapshot collection and its failure semantics."""

    def __init__(self, producer: Callable[[], Iterable[GaugeMetric]]) -> None:
        self.producer = producer

    def collect(self) -> Iterable[GaugeMetricFamily]:
        yield from SnapshotGaugeCollector(self.producer()).collect()


@dataclass
class MetricsServer:
    server: WSGIServer
    thread: Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_metrics_server(
    *, host: str, port: int, producer: Callable[[], Iterable[GaugeMetric]]
) -> MetricsServer:
    """Bind a deployment-only listener, separate from the public application API."""
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_registry)
    registry.register(LiveGaugeCollector(producer))
    server, thread = start_http_server(port, addr=host, registry=registry)
    return MetricsServer(server, thread)


def create_metrics_registry() -> CollectorRegistry:
    registry = CollectorRegistry(auto_describe=True)
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)
    return registry


_registry = create_metrics_registry()
_metrics: WeakKeyDictionary[CollectorRegistry, dict[MetricKey, PrometheusMetric]] = (
    WeakKeyDictionary()
)
_lock = Lock()


def increment_counter(
    name: str,
    *,
    labels: Mapping[str, object] | None = None,
    amount: float = 1,
    help_text: str | None = None,
    registry: CollectorRegistry | None = None,
) -> None:
    normalized = _string_labels(labels or {})
    metric = cast(Counter, _metric("counter", name, normalized, help_text, registry=registry))
    target = metric.labels(**normalized) if normalized else metric
    target.inc(amount)


def observe_histogram(
    name: str,
    value: float,
    *,
    buckets: tuple[float, ...],
    labels: Mapping[str, object] | None = None,
    help_text: str | None = None,
    registry: CollectorRegistry | None = None,
) -> None:
    normalized = _string_labels(labels or {})
    metric = cast(
        Histogram,
        _metric("histogram", name, normalized, help_text, buckets=buckets, registry=registry),
    )
    target = metric.labels(**normalized) if normalized else metric
    target.observe(float(value))


def set_gauge(
    name: str,
    value: float,
    *,
    labels: Mapping[str, object] | None = None,
    registry: CollectorRegistry | None = None,
) -> None:
    normalized = _string_labels(labels or {})
    metric = cast(Gauge, _metric("gauge", name, normalized, None, registry=registry))
    target = metric.labels(**normalized) if normalized else metric
    target.set(value)


def render_prometheus(
    *, gauges: Iterable[GaugeMetric] = (), registry: CollectorRegistry | None = None
) -> str:
    rendered = generate_latest(registry if registry is not None else _registry)
    snapshot_gauges = tuple(gauges)
    if snapshot_gauges:
        snapshots = CollectorRegistry(auto_describe=True)
        snapshots.register(SnapshotGaugeCollector(snapshot_gauges))
        rendered += generate_latest(snapshots)
    return rendered.decode("utf-8")


def reset_metrics() -> None:
    """Start a fresh process registry without modifying caller-owned registries."""
    global _registry
    with _lock:
        _metrics.pop(_registry, None)
        _registry = create_metrics_registry()


def _metric(
    kind: MetricKind,
    name: str,
    labels: Mapping[str, str],
    help_text: str | None,
    *,
    buckets: tuple[float, ...] = (),
    registry: CollectorRegistry | None = None,
) -> PrometheusMetric:
    label_names = tuple(sorted(labels))
    key = (kind, name, label_names)
    with _lock:
        target = registry if registry is not None else _registry
        registered = _metrics.setdefault(target, {})
        if key in registered:
            return registered[key]
        description = help_text or f"{name} metric."
        if kind == "counter":
            metric: PrometheusMetric = Counter(
                name, description, labelnames=label_names, registry=target
            )
        elif kind == "gauge":
            metric = Gauge(name, description, labelnames=label_names, registry=target)
        else:
            metric = Histogram(
                name, description, labelnames=label_names, buckets=buckets, registry=target
            )
        registered[key] = metric
        return metric


def _string_labels(labels: Mapping[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in labels.items()}
