"""Timeout utilities for operation execution.

Provides thread-based timeout enforcement for synchronous operations.

Maps to: Production hardening P2.3
"""

from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar

T = TypeVar('T')


class TimeoutError(Exception):
    """Raised when an operation exceeds its timeout."""
    pass


def with_timeout(func: Callable[[], T], timeout_seconds: float, operation_name: str = "operation") -> T:
    """Execute a function with a timeout.

    Args:
        func: Function to execute (no arguments)
        timeout_seconds: Maximum execution time in seconds
        operation_name: Name for error messages

    Returns:
        Result of func()

    Raises:
        TimeoutError: If func() does not complete within timeout_seconds

    Example:
        result = with_timeout(lambda: slow_operation(), 5.0, "slow_operation")
    """
    result_container: list[T | Exception] = []

    def target():
        try:
            result_container.append(func())
        except Exception as e:
            result_container.append(e)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        # Thread is still running after timeout
        raise TimeoutError(f"{operation_name} exceeded timeout of {timeout_seconds}s")

    if not result_container:
        # Thread terminated without setting result (should not happen)
        raise TimeoutError(f"{operation_name} terminated unexpectedly")

    result_or_error = result_container[0]
    if isinstance(result_or_error, Exception):
        raise result_or_error

    return result_or_error  # type: ignore
