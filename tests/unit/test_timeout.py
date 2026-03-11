"""Tests for timeout utilities."""

import time

import pytest

from mfp.observability.timeout import TimeoutError, with_timeout


class TestWithTimeout:
    """Tests for with_timeout function."""

    def test_fast_operation_completes(self):
        """Fast operation should complete successfully."""
        def fast_op():
            return "success"

        result = with_timeout(fast_op, timeout_seconds=1.0, operation_name="fast_op")
        assert result == "success"

    def test_slow_operation_times_out(self):
        """Slow operation should raise TimeoutError."""
        def slow_op():
            time.sleep(2.0)
            return "should not return"

        with pytest.raises(TimeoutError) as exc_info:
            with_timeout(slow_op, timeout_seconds=0.5, operation_name="slow_op")

        assert "slow_op" in str(exc_info.value)
        assert "0.5" in str(exc_info.value)

    def test_operation_raises_exception(self):
        """Exception from operation should propagate."""
        def failing_op():
            raise ValueError("test error")

        with pytest.raises(ValueError) as exc_info:
            with_timeout(failing_op, timeout_seconds=1.0, operation_name="failing_op")

        assert "test error" in str(exc_info.value)

    def test_very_short_timeout(self):
        """Very short timeout should timeout for slow operations."""
        def slow_op():
            time.sleep(1.0)
            return "result"

        with pytest.raises(TimeoutError):
            with_timeout(slow_op, timeout_seconds=0.01, operation_name="slow_op")

    def test_large_timeout_completes(self):
        """Large timeout should allow operation to complete."""
        def medium_op():
            time.sleep(0.1)
            return 42

        result = with_timeout(medium_op, timeout_seconds=10.0, operation_name="medium_op")
        assert result == 42

    def test_returns_complex_type(self):
        """Should handle complex return types."""
        def dict_op():
            return {"key": "value", "number": 123}

        result = with_timeout(dict_op, timeout_seconds=1.0, operation_name="dict_op")
        assert result == {"key": "value", "number": 123}

    def test_timeout_error_message(self):
        """Timeout error should contain operation name and timeout."""
        def slow():
            time.sleep(10.0)

        with pytest.raises(TimeoutError) as exc_info:
            with_timeout(slow, timeout_seconds=0.1, operation_name="slow_task")

        error_msg = str(exc_info.value)
        assert "slow_task" in error_msg
        assert "0.1" in error_msg
        assert "timeout" in error_msg.lower()
