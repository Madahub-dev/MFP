"""Integration tests for storage circuit breaker."""

import sqlite3

import pytest

from mfp.core.primitives import random_id, random_state_value
from mfp.core.types import AgentId, Channel, ChannelId, ChannelState, ChannelStatus, GlobalState, StateValue
from mfp.observability.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
)
from mfp.storage.engine import StorageEngine, StorageConfig


# Helpers
def make_channel(channel_id: bytes, agent_a: bytes, agent_b: bytes) -> Channel:
    """Create a minimal Channel for testing."""
    return Channel(
        channel_id=ChannelId(channel_id),
        agent_a=AgentId(agent_a),
        agent_b=AgentId(agent_b),
        state=ChannelState(local_state=random_state_value(), step=0),
        depth=4,
        status=ChannelStatus.ACTIVE,
    )


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker protecting storage operations."""

    def test_save_channel_fails_opens_circuit(self):
        """Multiple storage failures should open circuit breaker."""
        config = StorageConfig(db_path=":memory:", encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=10.0,
        )
        engine = StorageEngine(config, breaker_config)

        channel = make_channel(
            channel_id=random_id(16),
            agent_a=random_id(32),
            agent_b=random_id(32),
        )

        # Close connection to cause failures
        engine._conn.close()

        # First 3 failures
        for i in range(3):
            with pytest.raises(sqlite3.ProgrammingError):  # "Cannot operate on closed database"
                engine.save_channel(channel, None)
            # Circuit should still be closed for first 2 failures
            if i < 2:
                assert engine._circuit_breaker.get_state() == CircuitState.CLOSED

        # Circuit should now be OPEN
        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Next attempt should be rejected immediately without trying
        with pytest.raises(CircuitBreakerOpen):
            engine.save_channel(channel, None)

    def test_advance_channel_circuit_breaker(self):
        """advance_channel should be protected by circuit breaker."""
        config = StorageConfig(db_path=":memory:", encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(failure_threshold=2)
        engine = StorageEngine(config, breaker_config)

        channel_id = random_id(16)
        new_state = StateValue(random_id(32))
        sg = GlobalState(value=StateValue(random_id(32)))

        # Close connection to cause failures
        engine._conn.close()

        # Two failures open the circuit
        for _ in range(2):
            with pytest.raises(sqlite3.ProgrammingError):
                engine.advance_channel(channel_id, new_state, sg)

        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Now should reject immediately
        with pytest.raises(CircuitBreakerOpen):
            engine.advance_channel(channel_id, new_state, sg)

    def test_save_agent_circuit_breaker(self):
        """save_agent should be protected by circuit breaker."""
        config = StorageConfig(db_path=":memory:", encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(failure_threshold=1)
        engine = StorageEngine(config, breaker_config)

        agent_id = random_id(32)
        runtime_id = random_id(32)

        # Close connection to cause failures
        engine._conn.close()

        # One failure opens the circuit (threshold=1)
        with pytest.raises(sqlite3.ProgrammingError):
            engine.save_agent(agent_id, "bound", runtime_id)

        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Now should reject immediately
        with pytest.raises(CircuitBreakerOpen):
            engine.save_agent(agent_id, "bound", runtime_id)

    def test_recover_circuit_breaker(self):
        """recover should be protected by circuit breaker."""
        config = StorageConfig(db_path=":memory:", encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(failure_threshold=2)
        engine = StorageEngine(config, breaker_config)

        # Close connection to cause failures
        engine._conn.close()

        # Two failures open the circuit
        for _ in range(2):
            with pytest.raises(sqlite3.ProgrammingError):
                engine.recover()

        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Now should reject immediately
        with pytest.raises(CircuitBreakerOpen):
            engine.recover()

    def test_circuit_opens_and_closes_on_recovery(self):
        """Circuit should transition to HALF_OPEN after timeout and close on success."""
        import time

        # Use a file-based database so we can close and reopen
        import tempfile
        db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = db_file.name
        db_file.close()

        config = StorageConfig(db_path=db_path, encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,  # Very short timeout for testing
            success_threshold=1,
        )
        engine = StorageEngine(config, breaker_config)

        agent_id = random_id(32)
        runtime_id = random_id(32)

        # Initialize runtime meta first (needed for save_agent)
        from mfp.storage.engine import RuntimeMeta
        meta = RuntimeMeta(
            runtime_id=runtime_id,
            deployment_id=b"deploy",
            instance_id=b"instance",
            agent_counter=0,
            schema_version=1,
            created_at=int(time.time()),
        )
        engine.save_runtime_meta(meta)

        # Close connection to cause failure
        engine._conn.close()

        # One failure opens the circuit
        with pytest.raises(sqlite3.ProgrammingError):
            engine.save_agent(agent_id, "bound", runtime_id)

        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # Restore working connection by creating new engine
        engine2 = StorageEngine(config, breaker_config)
        # Copy circuit breaker state from old engine
        engine2._circuit_breaker = engine._circuit_breaker

        # Next attempt should transition to HALF_OPEN and succeed
        engine2.save_agent(agent_id, "bound", runtime_id)

        # After success, circuit should close
        assert engine2._circuit_breaker.get_state() == CircuitState.CLOSED

        # Cleanup
        import os
        os.unlink(db_path)

    def test_graceful_degradation_on_circuit_open(self):
        """Application should handle CircuitBreakerOpen gracefully."""
        config = StorageConfig(db_path=":memory:", encrypt_at_rest=False)
        breaker_config = CircuitBreakerConfig(failure_threshold=1)
        engine = StorageEngine(config, breaker_config)

        channel = make_channel(
            channel_id=random_id(16),
            agent_a=random_id(32),
            agent_b=random_id(32),
        )

        # Close connection to cause failure
        engine._conn.close()

        # Force circuit to open
        with pytest.raises(sqlite3.ProgrammingError):
            engine.save_channel(channel, None)

        assert engine._circuit_breaker.get_state() == CircuitState.OPEN

        # Application code should catch CircuitBreakerOpen and handle gracefully
        try:
            engine.save_channel(channel, None)
            assert False, "Should have raised CircuitBreakerOpen"
        except CircuitBreakerOpen as e:
            # Graceful degradation: log error and continue in read-only mode
            assert "Circuit breaker 'storage' is OPEN" in str(e)
            # Application can choose to:
            # - Queue writes for later
            # - Continue in read-only mode
            # - Alert operators
            # - Return degraded service status
