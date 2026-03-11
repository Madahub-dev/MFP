"""Unit tests for bilateral circuit breaker (P3.1)."""

import pytest

from mfp.core.ratchet import bilateral_seed
from mfp.core.types import RuntimeId, StateValue
from mfp.federation.bilateral import BilateralChannel, derive_bilateral_id
from mfp.observability.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
)


class TestBilateralCircuitBreaker:
    """Tests for circuit breaker on bilateral channels."""

    def test_bilateral_channel_has_circuit_breaker(self):
        """BilateralChannel should provide circuit breaker access."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        # Should have no circuit breaker initially
        assert channel._circuit_breaker is None

        # get_circuit_breaker creates one
        breaker = channel.get_circuit_breaker()
        assert breaker is not None
        assert breaker.get_state() == CircuitState.CLOSED

        # Subsequent calls return same instance
        breaker2 = channel.get_circuit_breaker()
        assert breaker2 is breaker

    def test_circuit_breaker_protects_bilateral_operations(self):
        """Circuit breaker should protect bilateral operations."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = CircuitBreakerConfig(
            failure_threshold=3,
            timeout_seconds=10.0,
        )

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        breaker = channel.get_circuit_breaker(config)

        # Simulate failed bilateral operations
        def failing_send():
            raise ConnectionError("Peer unreachable")

        # First 3 failures
        for i in range(3):
            with pytest.raises(ConnectionError):
                breaker.execute(failing_send)

            # Should still be closed for first 2 failures
            if i < 2:
                assert breaker.get_state() == CircuitState.CLOSED

        # Circuit should now be OPEN
        assert breaker.get_state() == CircuitState.OPEN

        # Next attempt should be rejected
        with pytest.raises(CircuitBreakerOpen):
            breaker.execute(failing_send)

    def test_circuit_breaker_prevents_cascading_failures(self):
        """Circuit breaker should prevent repeated attempts to bad peers."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = CircuitBreakerConfig(failure_threshold=2)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        breaker = channel.get_circuit_breaker(config)
        failure_count = 0

        def failing_op():
            nonlocal failure_count
            failure_count += 1
            raise ConnectionError("Network error")

        # Trigger circuit open
        for _ in range(2):
            with pytest.raises(ConnectionError):
                breaker.execute(failing_op)

        assert breaker.get_state() == CircuitState.OPEN
        assert failure_count == 2

        # Multiple attempts while circuit is OPEN should not call failing_op
        for _ in range(5):
            with pytest.raises(CircuitBreakerOpen):
                breaker.execute(failing_op)

        # failing_op should not have been called again
        assert failure_count == 2

    def test_circuit_breaker_recovers_on_success(self):
        """Circuit breaker should close on successful operations."""
        import time

        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = CircuitBreakerConfig(
            failure_threshold=2,
            timeout_seconds=0.1,
            success_threshold=1,
        )

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        breaker = channel.get_circuit_breaker(config)

        # Open circuit
        def failing_op():
            raise ConnectionError("Temporary failure")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                breaker.execute(failing_op)

        assert breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # Success should close circuit
        def success_op():
            return "ok"

        result = breaker.execute(success_op)
        assert result == "ok"
        assert breaker.get_state() == CircuitState.CLOSED

    def test_multiple_bilateral_channels_independent_breakers(self):
        """Each bilateral channel should have independent circuit breaker."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        runtime_c = RuntimeId(value=StateValue(b"c" * 32))

        bilateral_ab_id = derive_bilateral_id(runtime_a, runtime_b)
        bilateral_ac_id = derive_bilateral_id(runtime_a, runtime_c)

        state_ab = bilateral_seed(runtime_a, runtime_b)
        state_ac = bilateral_seed(runtime_a, runtime_c)

        channel_ab = BilateralChannel(
            bilateral_id=bilateral_ab_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state_ab,
        )

        channel_ac = BilateralChannel(
            bilateral_id=bilateral_ac_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_c,
            state=state_ac,
        )

        config = CircuitBreakerConfig(failure_threshold=2)

        breaker_ab = channel_ab.get_circuit_breaker(config)
        breaker_ac = channel_ac.get_circuit_breaker(config)

        # Different breakers
        assert breaker_ab is not breaker_ac

        # Open circuit for AB
        def failing():
            raise ConnectionError("fail")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                breaker_ab.execute(failing)

        # AB should be open, AC should be closed
        assert breaker_ab.get_state() == CircuitState.OPEN
        assert breaker_ac.get_state() == CircuitState.CLOSED

    def test_circuit_breaker_name_includes_bilateral_id(self):
        """Circuit breaker name should include bilateral ID for identification."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        breaker = channel.get_circuit_breaker()

        # Name should include bilateral_id prefix
        assert "bilateral_" in breaker.name
        assert bilateral_id.hex()[:8] in breaker.name
