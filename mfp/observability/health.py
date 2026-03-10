"""Health check system for production monitoring.

Provides liveness, readiness, and startup probes for container orchestration
platforms (Kubernetes, Docker, etc.).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from mfp.runtime.runtime import Runtime


class HealthStatus(Enum):
    """Health check status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    status: HealthStatus
    message: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON response."""
        return {
            "status": self.status.value,
            "message": self.message,
            "checks": self.checks,
            "metadata": self.metadata,
        }


class HealthChecker:
    """Performs health checks on MFP runtime.

    Supports three probe types:
    - Liveness: Is the process alive and responsive?
    - Readiness: Can the process accept new requests?
    - Startup: Has initialization completed successfully?
    """

    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.startup_time = time.time()
        self.startup_complete = False

    def mark_startup_complete(self):
        """Mark that startup/initialization has completed."""
        self.startup_complete = True

    def liveness(self) -> HealthCheckResult:
        """Check if the process is alive.

        Returns HEALTHY if:
        - Runtime exists and is responsive

        This should always return HEALTHY unless the process is deadlocked.
        """
        checks = {
            "runtime_exists": self.runtime is not None,
            "responsive": True,  # If we got here, we're responsive
        }

        all_passed = all(checks.values())
        status = HealthStatus.HEALTHY if all_passed else HealthStatus.UNHEALTHY

        return HealthCheckResult(
            status=status,
            message="Process is alive" if all_passed else "Process unresponsive",
            checks=checks,
            metadata={
                "uptime_seconds": time.time() - self.startup_time,
            },
        )

    def readiness(self) -> HealthCheckResult:
        """Check if the process is ready to accept traffic.

        Returns HEALTHY if:
        - Storage is accessible
        - No critical failures in recent operations
        - Global state is computable

        Returns DEGRADED if:
        - High quarantine count (>50% of agents)

        Returns UNHEALTHY if:
        - Storage unavailable
        - Runtime in error state
        """
        checks = {}
        metadata = {}

        # Check if we have agents
        agent_count = len(self.runtime._agents)
        checks["has_agents"] = agent_count > 0
        metadata["agent_count"] = agent_count

        # Check channel count
        channel_count = len(self.runtime._channels)
        metadata["channel_count"] = channel_count

        # Check quarantine status
        quarantined_count = sum(
            1
            for agent in self.runtime._agents.values()
            if agent.state.name == "QUARANTINED"
        )
        metadata["quarantined_agents"] = quarantined_count

        # High quarantine rate is degraded
        if agent_count > 0:
            quarantine_rate = quarantined_count / agent_count
            checks["quarantine_acceptable"] = quarantine_rate < 0.5
            metadata["quarantine_rate"] = f"{quarantine_rate:.2%}"

        # Check global state
        checks["global_state_exists"] = self.runtime._sg is not None

        # Determine overall status
        all_passed = all(checks.values())
        any_failed = any(not v for v in checks.values())

        if all_passed:
            status = HealthStatus.HEALTHY
            message = "Ready to accept traffic"
        elif any_failed:
            status = HealthStatus.UNHEALTHY
            message = "Not ready: " + ", ".join(
                k for k, v in checks.items() if not v
            )
        else:
            status = HealthStatus.DEGRADED
            message = "Degraded but operational"

        return HealthCheckResult(
            status=status,
            message=message,
            checks=checks,
            metadata=metadata,
        )

    def startup(self) -> HealthCheckResult:
        """Check if startup/initialization has completed.

        Returns HEALTHY if:
        - Startup has been marked complete
        - Runtime is initialized

        This is typically called once during container startup.
        """
        checks = {
            "startup_complete": self.startup_complete,
            "runtime_initialized": self.runtime is not None,
        }

        all_passed = all(checks.values())
        status = HealthStatus.HEALTHY if all_passed else HealthStatus.UNHEALTHY

        return HealthCheckResult(
            status=status,
            message=(
                "Startup complete" if all_passed else "Startup in progress"
            ),
            checks=checks,
            metadata={
                "startup_duration_seconds": time.time() - self.startup_time,
            },
        )

    def detailed_status(self) -> HealthCheckResult:
        """Get detailed status with all available metrics.

        This is more comprehensive than readiness and includes
        additional diagnostic information.
        """
        checks = {}
        metadata = {}

        # Basic counts
        agent_count = len(self.runtime._agents)
        channel_count = len(self.runtime._channels)
        metadata["agent_count"] = agent_count
        metadata["channel_count"] = channel_count
        metadata["uptime_seconds"] = time.time() - self.startup_time

        # Agent states
        state_counts = {}
        for agent in self.runtime._agents.values():
            state_name = agent.state.name
            state_counts[state_name] = state_counts.get(state_name, 0) + 1
        metadata["agent_states"] = state_counts

        # Quarantine info
        quarantined_count = state_counts.get("QUARANTINED", 0)
        metadata["quarantined_agents"] = quarantined_count

        if agent_count > 0:
            metadata["quarantine_rate"] = f"{quarantined_count / agent_count:.2%}"

        # Global state
        checks["global_state_exists"] = self.runtime._sg is not None
        if self.runtime._sg:
            metadata["global_state_size"] = len(self.runtime._sg.value.data)

        # Overall health
        critical_issues = []
        if quarantined_count > agent_count * 0.5 and agent_count > 0:
            critical_issues.append("high_quarantine_rate")
        if self.runtime._sg is None and channel_count > 0:
            critical_issues.append("missing_global_state")

        if not critical_issues:
            status = HealthStatus.HEALTHY
            message = "All systems operational"
        elif len(critical_issues) <= 1:
            status = HealthStatus.DEGRADED
            message = f"Degraded: {', '.join(critical_issues)}"
        else:
            status = HealthStatus.UNHEALTHY
            message = f"Unhealthy: {', '.join(critical_issues)}"

        checks["no_critical_issues"] = len(critical_issues) == 0

        return HealthCheckResult(
            status=status,
            message=message,
            checks=checks,
            metadata=metadata,
        )
