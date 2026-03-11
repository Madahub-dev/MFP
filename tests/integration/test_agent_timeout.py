"""Integration tests for agent timeout and quarantine."""

import time

import pytest

from mfp.core.types import AgentError, AgentErrorCode, AgentState
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime


def slow_agent(message):
    """Agent that sleeps longer than the timeout."""
    time.sleep(2.0)  # Exceeds default 30s timeout when configured low


def fast_agent(message):
    """Agent that completes quickly."""
    pass


class TestAgentTimeout:
    """Tests for agent timeout behavior."""

    def test_slow_agent_triggers_timeout(self):
        """Slow agent should trigger timeout error."""
        config = RuntimeConfig(agent_timeout_seconds=0.5)  # Very short timeout
        rt = Runtime(config)

        sender = rt.bind_agent(fast_agent)
        dest = rt.bind_agent(slow_agent)
        channel = rt.establish_channel(sender, dest)

        # Send message should timeout and raise AgentError
        with pytest.raises(AgentError) as exc_info:
            rt.send(sender, channel, b"test")

        assert exc_info.value.code == AgentErrorCode.TIMEOUT
        assert "timeout" in str(exc_info.value).lower()

    def test_slow_agent_gets_quarantined(self):
        """Slow agent should be quarantined after timeout."""
        config = RuntimeConfig(agent_timeout_seconds=0.5)
        rt = Runtime(config)

        sender = rt.bind_agent(fast_agent)
        dest = rt.bind_agent(slow_agent)
        channel = rt.establish_channel(sender, dest)

        # Trigger timeout
        with pytest.raises(AgentError):
            rt.send(sender, channel, b"test")

        # Destination agent should be quarantined
        dest_status = rt.get_status(dest)
        assert dest_status.state == AgentState.QUARANTINED

    def test_sender_not_quarantined_on_dest_timeout(self):
        """Sender should not be quarantined when destination times out."""
        config = RuntimeConfig(agent_timeout_seconds=0.5)
        rt = Runtime(config)

        sender = rt.bind_agent(fast_agent)
        dest = rt.bind_agent(slow_agent)
        channel = rt.establish_channel(sender, dest)

        # Trigger timeout
        with pytest.raises(AgentError):
            rt.send(sender, channel, b"test")

        # Sender should still be active
        sender_status = rt.get_status(sender)
        assert sender_status.state == AgentState.ACTIVE

    def test_fast_agent_completes_within_timeout(self):
        """Fast agent should complete successfully within timeout."""
        config = RuntimeConfig(agent_timeout_seconds=5.0)
        rt = Runtime(config)

        sender = rt.bind_agent(fast_agent)
        dest = rt.bind_agent(fast_agent)
        channel = rt.establish_channel(sender, dest)

        # Should succeed
        receipt = rt.send(sender, channel, b"test")
        assert receipt.step == 0

        # Both agents should still be active
        assert rt.get_status(sender).state == AgentState.ACTIVE
        assert rt.get_status(dest).state == AgentState.ACTIVE

    def test_quarantined_agent_blocks_future_sends(self):
        """Quarantined agent should block future message delivery."""
        config = RuntimeConfig(agent_timeout_seconds=0.5)
        rt = Runtime(config)

        sender = rt.bind_agent(fast_agent)
        dest = rt.bind_agent(slow_agent)
        channel = rt.establish_channel(sender, dest)

        # Trigger timeout and quarantine
        with pytest.raises(AgentError):
            rt.send(sender, channel, b"test1")

        # Subsequent sends should fail because channel is quarantined
        with pytest.raises(AgentError) as exc_info:
            rt.send(sender, channel, b"test2")

        # Should be channel quarantined error, not timeout
        # (when agent is quarantined, channel is also quarantined)
        assert exc_info.value.code == AgentErrorCode.CHANNEL_QUARANTINED

    def test_configurable_timeout_values(self):
        """Different timeout values should work correctly."""
        # Very permissive timeout
        config_long = RuntimeConfig(agent_timeout_seconds=10.0)
        rt_long = Runtime(config_long)

        sender = rt_long.bind_agent(fast_agent)
        dest = rt_long.bind_agent(fast_agent)
        channel = rt_long.establish_channel(sender, dest)

        # Should complete
        receipt = rt_long.send(sender, channel, b"test")
        assert receipt.step == 0

        # Very strict timeout
        config_short = RuntimeConfig(agent_timeout_seconds=0.1)
        rt_short = Runtime(config_short)

        sender2 = rt_short.bind_agent(fast_agent)

        def medium_slow_agent(message):
            time.sleep(0.3)  # Exceeds 0.1s timeout

        dest2 = rt_short.bind_agent(medium_slow_agent)
        channel2 = rt_short.establish_channel(sender2, dest2)

        # Should timeout
        with pytest.raises(AgentError) as exc_info:
            rt_short.send(sender2, channel2, b"test")

        assert exc_info.value.code == AgentErrorCode.TIMEOUT
