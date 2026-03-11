"""Tests for circuit breaker implementation."""

import time
import pytest

from mfp.observability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
)


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    def test_initial_state_is_closed(self):
        """Circuit breaker should start in CLOSED state."""
        breaker = CircuitBreaker("test")
        assert breaker.get_state() == CircuitState.CLOSED
        assert not breaker.is_open()

    def test_successful_operation_in_closed_state(self):
        """Successful operations should work in CLOSED state."""
        breaker = CircuitBreaker("test")

        result = breaker.execute(lambda: "success")
        assert result == "success"
        assert breaker.get_state() == CircuitState.CLOSED

    def test_failure_increments_counter(self):
        """Failures should increment failure counter."""
        breaker = CircuitBreaker("test")

        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.failure_count == 1
        assert breaker.get_state() == CircuitState.CLOSED

    def test_opens_after_threshold_failures(self):
        """Circuit should open after reaching failure threshold."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker("test", config)

        # First two failures
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.CLOSED

        # Third failure opens circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.OPEN
        assert breaker.is_open()

    def test_rejects_operations_when_open(self):
        """Circuit should reject operations when OPEN."""
        config = CircuitBreakerConfig(failure_threshold=1, timeout_seconds=10)
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.OPEN

        # Should reject subsequent operations
        with pytest.raises(CircuitBreakerOpen):
            breaker.execute(lambda: "should not execute")

    def test_transitions_to_half_open_after_timeout(self):
        """Circuit should transition to HALF_OPEN after timeout."""
        config = CircuitBreakerConfig(failure_threshold=1, timeout_seconds=0.1)
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # Next operation should transition to HALF_OPEN and execute
        result = breaker.execute(lambda: "success")
        assert result == "success"
        # After successful operation in HALF_OPEN, might close or stay HALF_OPEN
        assert breaker.get_state() in [CircuitState.HALF_OPEN, CircuitState.CLOSED]

    def test_closes_after_success_threshold_in_half_open(self):
        """Circuit should close after enough successes in HALF_OPEN."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
            success_threshold=2,
        )
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.15)

        # First success in HALF_OPEN
        breaker.execute(lambda: "success1")
        assert breaker.get_state() == CircuitState.HALF_OPEN

        # Second success should close circuit
        breaker.execute(lambda: "success2")
        assert breaker.get_state() == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        """Circuit should reopen on any failure in HALF_OPEN."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
        )
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        # Wait for timeout
        time.sleep(0.15)

        # Failure in HALF_OPEN should reopen
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test again")))

        assert breaker.get_state() == CircuitState.OPEN

    def test_max_half_open_attempts(self):
        """Circuit should limit test attempts in HALF_OPEN."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.1,
            half_open_max_attempts=2,
            success_threshold=10,  # High threshold so circuit stays HALF_OPEN
        )
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        # Wait for timeout
        time.sleep(0.15)

        # Two test attempts
        breaker.execute(lambda: "success1")
        assert breaker.get_state() == CircuitState.HALF_OPEN
        breaker.execute(lambda: "success2")
        assert breaker.get_state() == CircuitState.HALF_OPEN

        # Third attempt should fail with CircuitBreakerOpen
        with pytest.raises(CircuitBreakerOpen):
            breaker.execute(lambda: "should not execute")

    def test_reset_returns_to_closed(self):
        """Reset should return circuit to CLOSED state."""
        config = CircuitBreakerConfig(failure_threshold=1)
        breaker = CircuitBreaker("test", config)

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.get_state() == CircuitState.OPEN

        # Reset
        breaker.reset()

        assert breaker.get_state() == CircuitState.CLOSED
        assert breaker.failure_count == 0
        assert breaker.success_count == 0

    def test_success_resets_failure_count_in_closed(self):
        """Success in CLOSED state should reset failure count."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker("test", config)

        # Two failures
        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.execute(lambda: raise_error(ValueError("test")))

        assert breaker.failure_count == 2

        # Success resets counter
        breaker.execute(lambda: "success")
        assert breaker.failure_count == 0

    def test_multiple_circuits_independent(self):
        """Multiple circuit breakers should be independent."""
        config = CircuitBreakerConfig(failure_threshold=1)
        breaker1 = CircuitBreaker("test1", config)
        breaker2 = CircuitBreaker("test2", config)

        # Open first circuit
        with pytest.raises(ValueError):
            breaker1.execute(lambda: raise_error(ValueError("test")))

        assert breaker1.get_state() == CircuitState.OPEN
        assert breaker2.get_state() == CircuitState.CLOSED

        # Second circuit still works
        result = breaker2.execute(lambda: "success")
        assert result == "success"


def raise_error(error: Exception):
    """Helper to raise an error."""
    raise error
