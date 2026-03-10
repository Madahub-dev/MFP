"""Tests for health check system."""

import pytest

from mfp import Runtime, RuntimeConfig, bind
from mfp.observability.health import HealthChecker, HealthStatus


class TestHealthChecker:
    """Tests for HealthChecker."""

    def test_liveness_always_healthy_when_runtime_exists(self):
        """Liveness should return HEALTHY if runtime exists."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        result = checker.liveness()

        assert result.status == HealthStatus.HEALTHY
        assert result.checks["runtime_exists"] is True
        assert result.checks["responsive"] is True
        assert "uptime_seconds" in result.metadata

        runtime.shutdown()

    def test_readiness_unhealthy_with_no_agents(self):
        """Readiness should be UNHEALTHY with no agents."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        result = checker.readiness()

        assert result.status == HealthStatus.UNHEALTHY
        assert result.checks["has_agents"] is False
        assert result.metadata["agent_count"] == 0

        runtime.shutdown()

    def test_readiness_healthy_with_agents_and_channels(self):
        """Readiness should be HEALTHY with active agents and channels."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        # Bind agents
        def agent_a(msg):
            return {}

        def agent_b(msg):
            return {}

        handle_a = bind(runtime, agent_a)
        handle_b = bind(runtime, agent_b)

        # Establish channel using runtime
        runtime.establish_channel(handle_a.agent_id, handle_b.agent_id)

        result = checker.readiness()

        assert result.status == HealthStatus.HEALTHY
        assert result.checks["has_agents"] is True
        assert result.checks["global_state_exists"] is True
        assert result.metadata["agent_count"] == 2
        assert result.metadata["channel_count"] == 1
        assert result.metadata["quarantined_agents"] == 0

        runtime.shutdown()

    def test_startup_unhealthy_before_marked_complete(self):
        """Startup should be UNHEALTHY before marked complete."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        result = checker.startup()

        assert result.status == HealthStatus.UNHEALTHY
        assert result.checks["startup_complete"] is False
        assert result.checks["runtime_initialized"] is True

        runtime.shutdown()

    def test_startup_healthy_after_marked_complete(self):
        """Startup should be HEALTHY after marked complete."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        checker.mark_startup_complete()

        result = checker.startup()

        assert result.status == HealthStatus.HEALTHY
        assert result.checks["startup_complete"] is True
        assert "startup_duration_seconds" in result.metadata

        runtime.shutdown()

    def test_detailed_status_includes_agent_states(self):
        """Detailed status should include agent state breakdown."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        def agent(msg):
            return {}

        bind(runtime, agent)

        result = checker.detailed_status()

        assert "agent_states" in result.metadata
        assert result.metadata["agent_count"] == 1
        assert "uptime_seconds" in result.metadata

        runtime.shutdown()

    def test_to_dict_serializable(self):
        """Health check results should be JSON-serializable."""
        runtime = Runtime(RuntimeConfig())
        checker = HealthChecker(runtime)

        result = checker.liveness()
        data = result.to_dict()

        assert "status" in data
        assert "message" in data
        assert "checks" in data
        assert "metadata" in data
        assert data["status"] == "healthy"

        runtime.shutdown()
