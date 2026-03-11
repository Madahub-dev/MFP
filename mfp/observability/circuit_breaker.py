"""Circuit breaker for handling transient failures.

Implements the circuit breaker pattern to prevent cascading failures
when a dependency (e.g., storage) is experiencing issues.

Maps to: Production hardening P2.2
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar

from mfp.observability.logging import LogContext, get_logger

logger = get_logger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failures detected, reject requests
    HALF_OPEN = "half_open"  # Testing if dependency recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5       # Open after N consecutive failures
    timeout_seconds: float = 30.0    # Try recovery after this timeout
    half_open_max_attempts: int = 3  # Max test attempts in HALF_OPEN
    success_threshold: int = 2       # Successes needed to close from HALF_OPEN


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is OPEN and rejects operations."""
    pass


class CircuitBreaker(Generic[T]):
    """Circuit breaker for protecting against cascading failures.

    Usage:
        breaker = CircuitBreaker(config)
        try:
            result = breaker.execute(lambda: risky_operation())
        except CircuitBreakerOpen:
            # Handle degraded mode
            pass
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_attempts = 0

    def execute(self, operation: Callable[[], T], context: LogContext | None = None) -> T:
        """Execute operation through circuit breaker.

        Raises:
            CircuitBreakerOpen: If circuit is OPEN and timeout not elapsed
            Exception: If operation fails (and circuit state updated)
        """
        # Check if we should attempt the operation
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.config.timeout_seconds:
                # Timeout elapsed, try recovery
                self._transition_to_half_open(context)
            else:
                # Still in timeout, reject immediately
                logger.warning(
                    f"Circuit breaker '{self.name}' OPEN, rejecting operation",
                    context=context,
                    state=self.state.value,
                    failures=self.failure_count,
                )
                raise CircuitBreakerOpen(f"Circuit breaker '{self.name}' is OPEN")

        # HALF_OPEN: limit number of test attempts
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_attempts >= self.config.half_open_max_attempts:
                # Too many test attempts, reopen circuit
                self._transition_to_open(context, Exception("Max half-open attempts reached"))
                raise CircuitBreakerOpen(f"Circuit breaker '{self.name}' max test attempts exceeded")

            self.half_open_attempts += 1

        # Execute the operation
        try:
            result = operation()
            self._on_success(context)
            return result
        except Exception as e:
            self._on_failure(context, e)
            raise

    def _on_success(self, context: LogContext | None = None) -> None:
        """Handle successful operation."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.info(
                f"Circuit breaker '{self.name}' test success ({self.success_count}/{self.config.success_threshold})",
                context=context,
                state=self.state.value,
            )

            if self.success_count >= self.config.success_threshold:
                # Enough successes, close circuit
                self._transition_to_closed(context)
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            if self.failure_count > 0:
                logger.debug(
                    f"Circuit breaker '{self.name}' recovered, resetting failure count",
                    context=context,
                    previous_failures=self.failure_count,
                )
                self.failure_count = 0

    def _on_failure(self, context: LogContext | None, error: Exception) -> None:
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        logger.error(
            f"Circuit breaker '{self.name}' operation failed",
            context=context,
            state=self.state.value,
            failures=self.failure_count,
            error=str(error),
        )

        if self.state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN reopens circuit
            self._transition_to_open(context, error)
        elif self.state == CircuitState.CLOSED:
            # Check if we should open
            if self.failure_count >= self.config.failure_threshold:
                self._transition_to_open(context, error)

    def _transition_to_open(self, context: LogContext | None, error: Exception) -> None:
        """Transition to OPEN state."""
        self.state = CircuitState.OPEN
        self.success_count = 0
        self.half_open_attempts = 0

        logger.warning(
            f"Circuit breaker '{self.name}' opened after {self.failure_count} failures",
            context=context,
            error=str(error),
            timeout_seconds=self.config.timeout_seconds,
        )

    def _transition_to_half_open(self, context: LogContext | None) -> None:
        """Transition to HALF_OPEN state."""
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0
        self.half_open_attempts = 0

        logger.info(
            f"Circuit breaker '{self.name}' entering HALF_OPEN, testing recovery",
            context=context,
        )

    def _transition_to_closed(self, context: LogContext | None) -> None:
        """Transition to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_attempts = 0

        logger.info(
            f"Circuit breaker '{self.name}' closed, normal operation resumed",
            context=context,
        )

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_attempts = 0
        self.last_failure_time = 0.0

    def get_state(self) -> CircuitState:
        """Get current circuit state."""
        return self.state

    def is_open(self) -> bool:
        """Check if circuit is OPEN."""
        return self.state == CircuitState.OPEN
