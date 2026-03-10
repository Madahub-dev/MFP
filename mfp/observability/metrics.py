"""Prometheus-compatible metrics for MFP runtime.

Provides counters, gauges, and histograms for monitoring message flow,
performance, and system health.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mfp.runtime.runtime import Runtime


@dataclass
class MetricsConfig:
    """Configuration for metrics collection."""

    enabled: bool = True
    include_labels: bool = True  # Include agent_id, channel_id labels
    histogram_buckets: tuple[float, ...] = (
        0.001,  # 1ms
        0.005,  # 5ms
        0.01,  # 10ms
        0.025,  # 25ms
        0.05,  # 50ms
        0.1,  # 100ms
        0.25,  # 250ms
        0.5,  # 500ms
        1.0,  # 1s
        2.5,  # 2.5s
        5.0,  # 5s
        10.0,  # 10s
    )


class MetricsCollector:
    """Collects and exposes metrics in Prometheus format.

    This is a simple implementation without external dependencies.
    For production, consider using prometheus_client library.
    """

    def __init__(self, config: MetricsConfig | None = None):
        self.config = config or MetricsConfig()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._start_time = time.time()

    # Counters

    def increment_counter(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0):
        """Increment a counter metric."""
        if not self.config.enabled:
            return
        key = self._make_key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value

    def increment_messages_sent(self, agent_id: str = "", channel_id: str = ""):
        """Increment messages_sent counter."""
        labels = {}
        if self.config.include_labels:
            if agent_id:
                labels["agent_id"] = agent_id
            if channel_id:
                labels["channel_id"] = channel_id
        self.increment_counter("mfp_messages_sent_total", labels)

    def increment_messages_received(self, agent_id: str = ""):
        """Increment messages_received counter."""
        labels = {}
        if self.config.include_labels and agent_id:
            labels["agent_id"] = agent_id
        self.increment_counter("mfp_messages_received_total", labels)

    def increment_validation_failures(self, error_type: str = ""):
        """Increment validation_failures counter."""
        labels = {"error_type": error_type} if error_type else {}
        self.increment_counter("mfp_validation_failures_total", labels)

    def increment_quarantine_events(self, reason: str = ""):
        """Increment quarantine_events counter."""
        labels = {"reason": reason} if reason else {}
        self.increment_counter("mfp_quarantine_events_total", labels)

    # Gauges

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Set a gauge metric."""
        if not self.config.enabled:
            return
        key = self._make_key(name, labels)
        self._gauges[key] = value

    def set_active_channels(self, count: int):
        """Set active_channels gauge."""
        self.set_gauge("mfp_active_channels", float(count))

    def set_active_agents(self, count: int):
        """Set active_agents gauge."""
        self.set_gauge("mfp_active_agents", float(count))

    def set_quarantined_agents(self, count: int):
        """Set quarantined_agents gauge."""
        self.set_gauge("mfp_quarantined_agents", float(count))

    def set_pending_messages(self, count: int):
        """Set pending_messages gauge."""
        self.set_gauge("mfp_pending_messages", float(count))

    # Histograms

    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Observe a value for a histogram metric."""
        if not self.config.enabled:
            return
        key = self._make_key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def observe_pipeline_duration(self, stage: str, duration_seconds: float):
        """Observe pipeline stage duration."""
        labels = {"stage": stage}
        self.observe_histogram("mfp_pipeline_duration_seconds", duration_seconds, labels)

    def observe_sg_computation_duration(self, duration_seconds: float):
        """Observe Sg computation duration."""
        self.observe_histogram("mfp_sg_computation_duration_seconds", duration_seconds)

    def observe_storage_operation_duration(self, operation: str, duration_seconds: float):
        """Observe storage operation duration."""
        labels = {"operation": operation}
        self.observe_histogram("mfp_storage_operation_duration_seconds", duration_seconds, labels)

    def observe_message_size(self, size_bytes: int):
        """Observe message size."""
        self.observe_histogram("mfp_message_size_bytes", float(size_bytes))

    # Runtime stats collection

    def collect_runtime_stats(self, runtime: Runtime):
        """Collect current runtime statistics."""
        # Agent counts
        agent_count = len(runtime._agents)
        quarantined_count = sum(
            1 for agent in runtime._agents.values()
            if agent.state.name == "QUARANTINED"
        )
        self.set_active_agents(agent_count)
        self.set_quarantined_agents(quarantined_count)

        # Channel count
        channel_count = len(runtime._channels)
        self.set_active_channels(channel_count)

        # Pending messages (would need queue implementation)
        # For now, set to 0
        self.set_pending_messages(0)

    # Prometheus export

    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        """Create metric key from name and labels."""
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def _format_counter(self, key: str, value: float) -> str:
        """Format counter for Prometheus exposition."""
        name = key.split("{")[0]
        return f"{key} {value}"

    def _format_gauge(self, key: str, value: float) -> str:
        """Format gauge for Prometheus exposition."""
        return f"{key} {value}"

    def _format_histogram(self, key: str, values: list[float]) -> str:
        """Format histogram for Prometheus exposition."""
        name = key.split("{")[0]
        base_labels = key[len(name):] if "{" in key else ""

        # Calculate buckets
        lines = []
        buckets = list(self.config.histogram_buckets) + [float("inf")]
        counts = {b: 0 for b in buckets}
        total = 0
        sum_val = sum(values)

        for value in values:
            for bucket in buckets:
                if value <= bucket:
                    counts[bucket] += 1
            total += 1

        # Emit bucket lines
        for bucket in buckets:
            count = counts[bucket]
            if base_labels:
                # Merge existing labels with le label
                labels_dict = {}
                if base_labels.startswith("{") and base_labels.endswith("}"):
                    inner = base_labels[1:-1]
                    for pair in inner.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            labels_dict[k] = v.strip('"')
                labels_dict["le"] = str(bucket) if bucket != float("inf") else "+Inf"
                label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels_dict.items()))
                line = f'{name}_bucket{{{label_str}}} {count}'
            else:
                le = str(bucket) if bucket != float("inf") else "+Inf"
                line = f'{name}_bucket{{le="{le}"}} {count}'
            lines.append(line)

        # Emit sum and count
        if base_labels:
            lines.append(f"{name}_sum{base_labels} {sum_val}")
            lines.append(f"{name}_count{base_labels} {total}")
        else:
            lines.append(f"{name}_sum {sum_val}")
            lines.append(f"{name}_count {total}")

        return "\n".join(lines)

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines = []

        # Add metadata
        lines.append("# MFP Metrics")
        lines.append(f"# Uptime: {time.time() - self._start_time:.2f}s")
        lines.append("")

        # Counters
        if self._counters:
            lines.append("# HELP mfp_messages_sent_total Total messages sent")
            lines.append("# TYPE mfp_messages_sent_total counter")
            for key, value in sorted(self._counters.items()):
                if "messages_sent" in key:
                    lines.append(self._format_counter(key, value))

            lines.append("")
            lines.append("# HELP mfp_messages_received_total Total messages received")
            lines.append("# TYPE mfp_messages_received_total counter")
            for key, value in sorted(self._counters.items()):
                if "messages_received" in key:
                    lines.append(self._format_counter(key, value))

            lines.append("")
            lines.append("# HELP mfp_validation_failures_total Total validation failures")
            lines.append("# TYPE mfp_validation_failures_total counter")
            for key, value in sorted(self._counters.items()):
                if "validation_failures" in key:
                    lines.append(self._format_counter(key, value))

            lines.append("")
            lines.append("# HELP mfp_quarantine_events_total Total quarantine events")
            lines.append("# TYPE mfp_quarantine_events_total counter")
            for key, value in sorted(self._counters.items()):
                if "quarantine_events" in key:
                    lines.append(self._format_counter(key, value))

        # Gauges
        if self._gauges:
            lines.append("")
            lines.append("# HELP mfp_active_channels Currently active channels")
            lines.append("# TYPE mfp_active_channels gauge")
            for key, value in sorted(self._gauges.items()):
                if "active_channels" in key:
                    lines.append(self._format_gauge(key, value))

            lines.append("")
            lines.append("# HELP mfp_active_agents Currently active agents")
            lines.append("# TYPE mfp_active_agents gauge")
            for key, value in sorted(self._gauges.items()):
                if "active_agents" in key:
                    lines.append(self._format_gauge(key, value))

            lines.append("")
            lines.append("# HELP mfp_quarantined_agents Currently quarantined agents")
            lines.append("# TYPE mfp_quarantined_agents gauge")
            for key, value in sorted(self._gauges.items()):
                if "quarantined_agents" in key:
                    lines.append(self._format_gauge(key, value))

            lines.append("")
            lines.append("# HELP mfp_pending_messages Currently pending messages")
            lines.append("# TYPE mfp_pending_messages gauge")
            for key, value in sorted(self._gauges.items()):
                if "pending_messages" in key:
                    lines.append(self._format_gauge(key, value))

        # Histograms
        if self._histograms:
            lines.append("")
            lines.append("# HELP mfp_pipeline_duration_seconds Pipeline stage duration")
            lines.append("# TYPE mfp_pipeline_duration_seconds histogram")
            for key, values in sorted(self._histograms.items()):
                if "pipeline_duration" in key and values:
                    lines.append(self._format_histogram(key, values))

            lines.append("")
            lines.append("# HELP mfp_sg_computation_duration_seconds Sg computation duration")
            lines.append("# TYPE mfp_sg_computation_duration_seconds histogram")
            for key, values in sorted(self._histograms.items()):
                if "sg_computation" in key and values:
                    lines.append(self._format_histogram(key, values))

            lines.append("")
            lines.append("# HELP mfp_storage_operation_duration_seconds Storage operation duration")
            lines.append("# TYPE mfp_storage_operation_duration_seconds histogram")
            for key, values in sorted(self._histograms.items()):
                if "storage_operation" in key and values:
                    lines.append(self._format_histogram(key, values))

            lines.append("")
            lines.append("# HELP mfp_message_size_bytes Message size in bytes")
            lines.append("# TYPE mfp_message_size_bytes histogram")
            for key, values in sorted(self._histograms.items()):
                if "message_size" in key and values:
                    lines.append(self._format_histogram(key, values))

        return "\n".join(lines) + "\n"


# Global metrics collector instance
_metrics_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def set_metrics_collector(collector: MetricsCollector):
    """Set global metrics collector."""
    global _metrics_collector
    _metrics_collector = collector
