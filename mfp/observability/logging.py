"""Structured logging with correlation IDs for distributed tracing.

Provides JSON-formatted logs with contextual metadata for production observability.
All critical operations are logged with correlation IDs to enable end-to-end tracing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# Global logging configuration
_LOGGING_CONFIG: LoggingConfig | None = None


class LogFormat(Enum):
    """Log output format."""

    JSON = "json"
    TEXT = "text"


@dataclass
class LoggingConfig:
    """Configuration for structured logging."""

    format: LogFormat = LogFormat.JSON
    level: str = "INFO"
    include_correlation_ids: bool = True
    include_timestamps: bool = True
    include_caller: bool = False
    audit_events: bool = True


@dataclass
class LogContext:
    """Structured logging context for correlation and metadata.

    Flows through the entire message pipeline to enable distributed tracing.
    """

    correlation_id: str  # Unique ID for request/message lifecycle
    runtime_id: str  # Deployment/instance identifier
    agent_id: str | None = None  # Agent involved (8-char hex prefix)
    channel_id: str | None = None  # Channel involved (8-char hex prefix)
    operation: str = ""  # Current operation (e.g., "send", "deliver")
    stage: str | None = None  # Pipeline stage (e.g., "FRAME", "ENCODE")
    metadata: dict[str, Any] = field(default_factory=dict)  # Additional context

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "correlation_id": self.correlation_id,
            "runtime_id": self.runtime_id,
            "operation": self.operation,
        }

        if self.agent_id:
            result["agent_id"] = self.agent_id
        if self.channel_id:
            result["channel_id"] = self.channel_id
        if self.stage:
            result["stage"] = self.stage
        if self.metadata:
            result["metadata"] = self.metadata

        return result

    @staticmethod
    def truncate_id(full_id: bytes) -> str:
        """Truncate ID to 8-char hex prefix for logs."""
        return full_id.hex()[:8]


class StructuredLogger:
    """Logger that outputs JSON or structured text with correlation IDs."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.config = _LOGGING_CONFIG or LoggingConfig()

    def _format_message(
        self, level: str, message: str, context: LogContext | None, **kwargs: Any
    ) -> str:
        """Format log message based on configuration."""
        if self.config.format == LogFormat.JSON:
            return self._format_json(level, message, context, **kwargs)
        else:
            return self._format_text(level, message, context, **kwargs)

    def _format_json(
        self, level: str, message: str, context: LogContext | None, **kwargs: Any
    ) -> str:
        """Format as JSON."""
        log_entry: dict[str, Any] = {
            "level": level,
            "message": message,
        }

        if self.config.include_timestamps:
            log_entry["timestamp"] = time.time()

        if context and self.config.include_correlation_ids:
            log_entry["context"] = context.to_dict()

        # Add any extra fields
        if kwargs:
            log_entry["extra"] = kwargs

        return json.dumps(log_entry)

    def _format_text(
        self, level: str, message: str, context: LogContext | None, **kwargs: Any
    ) -> str:
        """Format as human-readable text."""
        parts = [f"[{level}]"]

        if context and self.config.include_correlation_ids:
            parts.append(f"[{context.correlation_id[:8]}]")
            if context.operation:
                parts.append(f"[{context.operation}]")
            if context.stage:
                parts.append(f"[{context.stage}]")

        parts.append(message)

        if kwargs:
            extras = " ".join(f"{k}={v}" for k, v in kwargs.items())
            parts.append(f"({extras})")

        return " ".join(parts)

    def debug(self, message: str, context: LogContext | None = None, **kwargs: Any):
        """Log debug message."""
        if self.logger.isEnabledFor(logging.DEBUG):
            formatted = self._format_message("DEBUG", message, context, **kwargs)
            self.logger.debug(formatted)

    def info(self, message: str, context: LogContext | None = None, **kwargs: Any):
        """Log info message."""
        if self.logger.isEnabledFor(logging.INFO):
            formatted = self._format_message("INFO", message, context, **kwargs)
            self.logger.info(formatted)

    def warning(self, message: str, context: LogContext | None = None, **kwargs: Any):
        """Log warning message."""
        if self.logger.isEnabledFor(logging.WARNING):
            formatted = self._format_message("WARNING", message, context, **kwargs)
            self.logger.warning(formatted)

    def error(self, message: str, context: LogContext | None = None, **kwargs: Any):
        """Log error message."""
        if self.logger.isEnabledFor(logging.ERROR):
            formatted = self._format_message("ERROR", message, context, **kwargs)
            self.logger.error(formatted)

    def critical(
        self, message: str, context: LogContext | None = None, **kwargs: Any
    ):
        """Log critical message."""
        formatted = self._format_message("CRITICAL", message, context, **kwargs)
        self.logger.critical(formatted)


# Logger cache
_LOGGERS: dict[str, StructuredLogger] = {}


def get_logger(name: str) -> StructuredLogger:
    """Get or create a structured logger for the given name.

    Args:
        name: Logger name (typically module name)

    Returns:
        StructuredLogger instance
    """
    if name not in _LOGGERS:
        _LOGGERS[name] = StructuredLogger(name)
    return _LOGGERS[name]


def set_logging_config(config: LoggingConfig):
    """Set global logging configuration.

    Args:
        config: Logging configuration to apply globally
    """
    global _LOGGING_CONFIG
    _LOGGING_CONFIG = config

    # Update all existing loggers
    for logger in _LOGGERS.values():
        logger.config = config


# Convenience functions for common log patterns


def log_audit_event(
    event_type: str,
    context: LogContext,
    success: bool = True,
    **details: Any,
):
    """Log an audit event (channel creation, quarantine, etc.).

    Args:
        event_type: Type of audit event (e.g., "channel_established", "agent_quarantined")
        context: Log context with correlation ID
        success: Whether the operation succeeded
        **details: Additional event-specific details
    """
    config = _LOGGING_CONFIG or LoggingConfig()
    if not config.audit_events:
        return

    logger = get_logger("mfp.audit")
    status = "success" if success else "failure"

    logger.info(
        f"Audit: {event_type}",
        context=context,
        event_type=event_type,
        status=status,
        **details,
    )


def log_performance(
    operation: str,
    duration_ms: float,
    context: LogContext,
    **metadata: Any,
):
    """Log performance metrics for an operation.

    Args:
        operation: Operation name (e.g., "sg_computation", "frame_validation")
        duration_ms: Operation duration in milliseconds
        context: Log context with correlation ID
        **metadata: Additional performance metadata
    """
    logger = get_logger("mfp.performance")

    logger.info(
        f"Performance: {operation}",
        context=context,
        operation=operation,
        duration_ms=duration_ms,
        **metadata,
    )


@dataclass
class TimedOperation:
    """Context manager for timing operations with automatic logging and metrics.

    Usage:
        with TimedOperation("sg_computation", context) as timer:
            result = compute_sg()
        # Automatically logs duration and records metrics
    """

    operation: str
    context: LogContext
    metadata: dict[str, Any] = field(default_factory=dict)
    record_metrics: bool = field(default=True)
    start_time: float = field(default=0.0, init=False)
    end_time: float = field(default=0.0, init=False)

    def __enter__(self) -> TimedOperation:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.perf_counter()
        duration_ms = (self.end_time - self.start_time) * 1000
        duration_s = duration_ms / 1000.0

        if exc_type is None:
            log_performance(self.operation, duration_ms, self.context, **self.metadata)

            # Record metrics if enabled
            if self.record_metrics:
                try:
                    from mfp.observability.metrics import get_metrics_collector
                    metrics = get_metrics_collector()

                    # Map operation to appropriate metric
                    if "pipeline" in self.operation or self.context.stage:
                        stage = self.context.stage or self.operation
                        metrics.observe_pipeline_duration(stage, duration_s)
                    elif "sg_computation" in self.operation:
                        metrics.observe_sg_computation_duration(duration_s)
                    elif "storage" in self.operation:
                        metrics.observe_storage_operation_duration(self.operation, duration_s)
                except Exception:
                    # Metrics failures should not break the operation
                    pass
        else:
            # Log error with timing
            logger = get_logger("mfp.performance")
            logger.error(
                f"Performance: {self.operation} failed",
                context=self.context,
                operation=self.operation,
                duration_ms=duration_ms,
                error=str(exc_val),
                **self.metadata,
            )

    @property
    def duration_ms(self) -> float:
        """Get duration in milliseconds."""
        if self.end_time == 0.0:
            return (time.perf_counter() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000
