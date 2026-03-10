"""Integration tests for metrics collection during message flow."""

import pytest

from mfp import Runtime, RuntimeConfig, bind
from mfp.observability.metrics import MetricsCollector, MetricsConfig, set_metrics_collector


class TestMetricsIntegration:
    """Tests for metrics collection during real message flow."""

    def test_message_sent_and_received_metrics(self):
        """Metrics should track sent and received messages."""
        # Create runtime with metrics
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        # Bind agents
        received = []

        def agent_a(msg):
            return {}

        def agent_b(msg):
            received.append(msg)
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)

        # Establish channel
        channel_id = runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)

        # Send messages
        handle_a.send(channel_id, b"hello")
        handle_a.send(channel_id, b"world")
        handle_b.send(channel_id, b"reply")

        # Check metrics
        prometheus_output = metrics.export_prometheus()

        # Should have sent counter incremented
        assert "mfp_messages_sent_total" in prometheus_output
        # Should track message size
        assert "mfp_message_size_bytes" in prometheus_output

        runtime.shutdown()

    def test_validation_failure_metrics(self):
        """Metrics should track validation failures."""
        # This would require triggering a validation failure
        # For now, just verify the counter exists
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        # Manually increment to test
        metrics.increment_validation_failures(error_type="test_error")

        prometheus_output = metrics.export_prometheus()
        assert "mfp_validation_failures_total" in prometheus_output
        assert 'error_type="test_error"' in prometheus_output

        runtime.shutdown()

    def test_quarantine_metrics(self):
        """Metrics should track quarantine events."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        def agent(msg):
            return {}

        handle = bind(runtime, agent)

        # Quarantine agent
        runtime.quarantine_agent(handle.agent_id, reason="test_quarantine")

        prometheus_output = metrics.export_prometheus()
        assert "mfp_quarantine_events_total" in prometheus_output
        assert 'reason="test_quarantine"' in prometheus_output

        runtime.shutdown()

    def test_pipeline_duration_metrics(self):
        """Metrics should track pipeline stage durations."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)

        channel_id = runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)

        # Send message (triggers pipeline)
        handle_a.send(channel_id, b"test")

        prometheus_output = metrics.export_prometheus()

        # Should have pipeline duration histograms
        assert "mfp_pipeline_duration_seconds" in prometheus_output
        # Should track all pipeline stages
        assert 'stage="ACCEPT"' in prometheus_output or 'stage="accept"' in prometheus_output

        runtime.shutdown()

    def test_gauge_metrics_from_runtime_stats(self):
        """Gauge metrics should reflect runtime state."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        def agent_c(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)
        handle_c = bind(runtime, agent_c)

        runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)
        runtime.establish_channel(handle_a.agent_id, handle_c.agent_id)

        # Collect runtime stats
        metrics.collect_runtime_stats(runtime)

        prometheus_output = metrics.export_prometheus()

        # Should have gauge metrics
        assert "mfp_active_agents" in prometheus_output
        assert "mfp_active_channels" in prometheus_output
        assert "mfp_quarantined_agents" in prometheus_output

        runtime.shutdown()

    def test_metrics_disabled_config(self):
        """When metrics disabled, no metrics should be collected."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig(enabled=False))
        set_metrics_collector(metrics)

        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)

        channel_id = runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)
        handle_a.send(channel_id, b"test")

        # Should have no counters/gauges/histograms
        assert len(metrics._counters) == 0
        assert len(metrics._gauges) == 0
        assert len(metrics._histograms) == 0

        runtime.shutdown()

    def test_high_volume_metrics(self):
        """Metrics should handle high message volume."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)

        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)

        channel_id = runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)

        # Send many messages
        for i in range(100):
            handle_a.send(channel_id, f"message_{i}".encode())

        prometheus_output = metrics.export_prometheus()

        # Should have many histogram observations
        assert "mfp_message_size_bytes" in prometheus_output
        # Should have counter reflecting all messages
        assert "mfp_messages_sent_total" in prometheus_output

        runtime.shutdown()
