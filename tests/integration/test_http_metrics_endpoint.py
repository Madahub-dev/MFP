"""Integration tests for /metrics HTTP endpoint."""

import asyncio
import pytest

from mfp import Runtime, RuntimeConfig, bind
from mfp.observability.health import HealthChecker
from mfp.observability.http_server import HealthHTTPServer, HealthServerConfig
from mfp.observability.metrics import MetricsCollector, MetricsConfig, set_metrics_collector


@pytest.mark.asyncio
class TestHTTPMetricsEndpoint:
    """Tests for /metrics HTTP endpoint."""

    async def test_metrics_endpoint_returns_prometheus_format(self):
        """GET /metrics should return Prometheus text format."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)
        checker = HealthChecker(runtime)

        # Start server on a unique port
        config = HealthServerConfig(port=19877, enable_metrics=True)
        server = HealthHTTPServer(config, checker, metrics)

        try:
            await server.start()

            # Give server time to start
            await asyncio.sleep(0.1)

            # Make HTTP request
            reader, writer = await asyncio.open_connection("127.0.0.1", 19877)

            request = b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            # Read response
            response = await reader.read(4096)
            response_text = response.decode()

            # Close connection
            writer.close()
            await writer.wait_closed()

            # Verify response
            assert "HTTP/1.1 200 OK" in response_text
            assert "Content-Type: text/plain; version=0.0.4" in response_text
            assert "# MFP Metrics" in response_text

        finally:
            await server.stop()
            runtime.shutdown()

    async def test_metrics_endpoint_includes_counters(self):
        """Metrics endpoint should include counter values."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)
        checker = HealthChecker(runtime)

        # Add some metric data
        metrics.increment_messages_sent(agent_id="12345678", channel_id="abcdefgh")
        metrics.increment_messages_sent(agent_id="12345678", channel_id="abcdefgh")

        config = HealthServerConfig(port=19878, enable_metrics=True)
        server = HealthHTTPServer(config, checker, metrics)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_connection("127.0.0.1", 19878)
            request = b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            response = await reader.read(4096)
            response_text = response.decode()

            writer.close()
            await writer.wait_closed()

            # Should have counter
            assert "mfp_messages_sent_total" in response_text
            assert "2.0" in response_text or "2" in response_text

        finally:
            await server.stop()
            runtime.shutdown()

    async def test_metrics_endpoint_disabled(self):
        """When metrics disabled, /metrics should return 404."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        config = HealthServerConfig(port=19879, enable_metrics=False)
        server = HealthHTTPServer(config, checker, metrics_collector=None)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_connection("127.0.0.1", 19879)
            request = b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            response = await reader.read(4096)
            response_text = response.decode()

            writer.close()
            await writer.wait_closed()

            # Should return 404 (endpoint not available)
            assert "HTTP/1.1 404" in response_text

        finally:
            await server.stop()
            runtime.shutdown()

    async def test_metrics_with_real_message_flow(self):
        """Metrics endpoint should reflect actual message traffic."""
        runtime = Runtime(RuntimeConfig())
        metrics = MetricsCollector(MetricsConfig())
        set_metrics_collector(metrics)
        checker = HealthChecker(runtime)

        # Bind agents and send messages
        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)
        channel_id = runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)

        # Send several messages
        handle_a.send(channel_id, b"message 1")
        handle_a.send(channel_id, b"message 2")
        handle_b.send(channel_id, b"reply")

        # Start server
        config = HealthServerConfig(port=19880, enable_metrics=True)
        server = HealthHTTPServer(config, checker, metrics)

        try:
            await server.start()
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_connection("127.0.0.1", 19880)
            request = b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            response = await reader.read(8192)
            response_text = response.decode()

            writer.close()
            await writer.wait_closed()

            # Verify metrics reflect actual traffic
            assert "mfp_messages_sent_total" in response_text
            assert "mfp_message_size_bytes" in response_text
            assert "mfp_pipeline_duration_seconds" in response_text

        finally:
            await server.stop()
            runtime.shutdown()
