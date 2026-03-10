"""Tests for metrics collection system."""

import pytest

from mfp.observability.metrics import MetricsCollector, MetricsConfig


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_counter_increments(self):
        """Counters should increment correctly."""
        collector = MetricsCollector()

        collector.increment_counter("test_counter", {"label": "value"}, 1.0)
        collector.increment_counter("test_counter", {"label": "value"}, 2.0)

        assert collector._counters['test_counter{label="value"}'] == 3.0

    def test_counter_without_labels(self):
        """Counters should work without labels."""
        collector = MetricsCollector()

        collector.increment_counter("test_counter", None, 5.0)

        assert collector._counters["test_counter"] == 5.0

    def test_messages_sent_counter(self):
        """Messages sent counter should increment with labels."""
        collector = MetricsCollector()

        collector.increment_messages_sent(agent_id="12345678", channel_id="abcdefgh")
        collector.increment_messages_sent(agent_id="12345678", channel_id="abcdefgh")

        key = 'mfp_messages_sent_total{agent_id="12345678",channel_id="abcdefgh"}'
        assert collector._counters[key] == 2.0

    def test_messages_received_counter(self):
        """Messages received counter should increment."""
        collector = MetricsCollector()

        collector.increment_messages_received(agent_id="87654321")

        key = 'mfp_messages_received_total{agent_id="87654321"}'
        assert collector._counters[key] == 1.0

    def test_validation_failures_counter(self):
        """Validation failures counter should track error types."""
        collector = MetricsCollector()

        collector.increment_validation_failures(error_type="invalid_signature")
        collector.increment_validation_failures(error_type="invalid_signature")
        collector.increment_validation_failures(error_type="decode_error")

        assert collector._counters['mfp_validation_failures_total{error_type="invalid_signature"}'] == 2.0
        assert collector._counters['mfp_validation_failures_total{error_type="decode_error"}'] == 1.0

    def test_quarantine_events_counter(self):
        """Quarantine events counter should track reasons."""
        collector = MetricsCollector()

        collector.increment_quarantine_events(reason="validation_threshold_exceeded")

        key = 'mfp_quarantine_events_total{reason="validation_threshold_exceeded"}'
        assert collector._counters[key] == 1.0

    def test_gauge_sets_value(self):
        """Gauges should set values correctly."""
        collector = MetricsCollector()

        collector.set_gauge("test_gauge", 42.0, {"label": "value"})

        assert collector._gauges['test_gauge{label="value"}'] == 42.0

    def test_active_channels_gauge(self):
        """Active channels gauge should set count."""
        collector = MetricsCollector()

        collector.set_active_channels(5)

        assert collector._gauges["mfp_active_channels"] == 5.0

    def test_active_agents_gauge(self):
        """Active agents gauge should set count."""
        collector = MetricsCollector()

        collector.set_active_agents(10)

        assert collector._gauges["mfp_active_agents"] == 10.0

    def test_quarantined_agents_gauge(self):
        """Quarantined agents gauge should set count."""
        collector = MetricsCollector()

        collector.set_quarantined_agents(2)

        assert collector._gauges["mfp_quarantined_agents"] == 2.0

    def test_pending_messages_gauge(self):
        """Pending messages gauge should set count."""
        collector = MetricsCollector()

        collector.set_pending_messages(15)

        assert collector._gauges["mfp_pending_messages"] == 15.0

    def test_histogram_observes_values(self):
        """Histograms should record observations."""
        collector = MetricsCollector()

        collector.observe_histogram("test_histogram", 0.1, {"label": "value"})
        collector.observe_histogram("test_histogram", 0.2, {"label": "value"})
        collector.observe_histogram("test_histogram", 0.3, {"label": "value"})

        key = 'test_histogram{label="value"}'
        assert len(collector._histograms[key]) == 3
        assert sum(collector._histograms[key]) == pytest.approx(0.6)

    def test_pipeline_duration_histogram(self):
        """Pipeline duration histogram should track stages."""
        collector = MetricsCollector()

        collector.observe_pipeline_duration("FRAME", 0.025)
        collector.observe_pipeline_duration("ENCODE", 0.015)
        collector.observe_pipeline_duration("FRAME", 0.030)

        assert len(collector._histograms['mfp_pipeline_duration_seconds{stage="FRAME"}']) == 2
        assert len(collector._histograms['mfp_pipeline_duration_seconds{stage="ENCODE"}']) == 1

    def test_sg_computation_duration_histogram(self):
        """Sg computation duration histogram should record values."""
        collector = MetricsCollector()

        collector.observe_sg_computation_duration(0.050)
        collector.observe_sg_computation_duration(0.075)

        assert len(collector._histograms["mfp_sg_computation_duration_seconds"]) == 2

    def test_storage_operation_duration_histogram(self):
        """Storage operation duration histogram should track operations."""
        collector = MetricsCollector()

        collector.observe_storage_operation_duration("save_state", 0.010)

        key = 'mfp_storage_operation_duration_seconds{operation="save_state"}'
        assert len(collector._histograms[key]) == 1

    def test_message_size_histogram(self):
        """Message size histogram should record byte counts."""
        collector = MetricsCollector()

        collector.observe_message_size(1024)
        collector.observe_message_size(2048)

        assert len(collector._histograms["mfp_message_size_bytes"]) == 2
        assert collector._histograms["mfp_message_size_bytes"] == [1024.0, 2048.0]

    def test_prometheus_export_format(self):
        """Prometheus export should produce valid text format."""
        collector = MetricsCollector()

        # Add some metrics
        collector.increment_messages_sent(agent_id="12345678")
        collector.set_active_channels(3)
        collector.observe_pipeline_duration("FRAME", 0.025)

        output = collector.export_prometheus()

        # Check format
        assert "# MFP Metrics" in output
        assert "# HELP mfp_messages_sent_total" in output
        assert "# TYPE mfp_messages_sent_total counter" in output
        assert "# HELP mfp_active_channels" in output
        assert "# TYPE mfp_active_channels gauge" in output
        assert "# HELP mfp_pipeline_duration_seconds" in output
        assert "# TYPE mfp_pipeline_duration_seconds histogram" in output

    def test_prometheus_export_counter_values(self):
        """Prometheus export should include counter values."""
        collector = MetricsCollector()

        collector.increment_messages_sent(agent_id="12345678")
        collector.increment_messages_sent(agent_id="12345678")

        output = collector.export_prometheus()

        assert 'mfp_messages_sent_total{agent_id="12345678"} 2.0' in output

    def test_prometheus_export_gauge_values(self):
        """Prometheus export should include gauge values."""
        collector = MetricsCollector()

        collector.set_active_agents(5)

        output = collector.export_prometheus()

        assert "mfp_active_agents 5.0" in output

    def test_prometheus_export_histogram_buckets(self):
        """Prometheus export should include histogram buckets."""
        collector = MetricsCollector()

        collector.observe_pipeline_duration("FRAME", 0.005)
        collector.observe_pipeline_duration("FRAME", 0.015)
        collector.observe_pipeline_duration("FRAME", 0.050)

        output = collector.export_prometheus()

        # Check bucket lines exist
        assert 'mfp_pipeline_duration_seconds_bucket{le="0.001",stage="FRAME"}' in output
        assert 'mfp_pipeline_duration_seconds_bucket{le="0.01",stage="FRAME"}' in output
        assert 'mfp_pipeline_duration_seconds_bucket{le="0.1",stage="FRAME"}' in output
        assert 'mfp_pipeline_duration_seconds_bucket{le="+Inf",stage="FRAME"}' in output

        # Check sum and count
        assert 'mfp_pipeline_duration_seconds_sum{stage="FRAME"} 0.07' in output
        assert 'mfp_pipeline_duration_seconds_count{stage="FRAME"} 3' in output

    def test_metrics_disabled_config(self):
        """Metrics collection should be skipped when disabled."""
        config = MetricsConfig(enabled=False)
        collector = MetricsCollector(config)

        collector.increment_messages_sent()
        collector.set_active_channels(5)
        collector.observe_pipeline_duration("FRAME", 0.025)

        assert len(collector._counters) == 0
        assert len(collector._gauges) == 0
        assert len(collector._histograms) == 0

    def test_metrics_without_labels_config(self):
        """Metrics should not include labels when disabled."""
        config = MetricsConfig(include_labels=False)
        collector = MetricsCollector(config)

        collector.increment_messages_sent(agent_id="12345678", channel_id="abcdefgh")

        # Should use metric name without labels
        assert "mfp_messages_sent_total" in collector._counters
        assert 'agent_id="12345678"' not in str(collector._counters)

    def test_global_metrics_collector(self):
        """Global metrics collector should be retrievable."""
        from mfp.observability.metrics import get_metrics_collector, set_metrics_collector

        # Get default collector
        collector1 = get_metrics_collector()
        assert isinstance(collector1, MetricsCollector)

        # Should return same instance
        collector2 = get_metrics_collector()
        assert collector1 is collector2

        # Set custom collector
        custom = MetricsCollector(MetricsConfig(enabled=False))
        set_metrics_collector(custom)

        collector3 = get_metrics_collector()
        assert collector3 is custom
