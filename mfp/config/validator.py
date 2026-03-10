"""YAML configuration validation with security checks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ValidationWarning:
    """Configuration validation warning."""

    field: str
    message: str
    severity: str  # "warning" | "error"


class ConfigValidator:
    """Validates server configuration for security and correctness."""

    # Security limits
    MIN_VALIDATION_THRESHOLD = 3
    MIN_FRAME_DEPTH = 2
    MAX_FRAME_DEPTH = 32
    MIN_PORT = 1
    MAX_PORT = 65535
    MASTER_KEY_SIZE = 32
    MAX_STEP_SAFE = 2**60  # Warn threshold for step counter

    def __init__(self, strict: bool = False):
        """
        Args:
            strict: If True, treat warnings as errors
        """
        self.strict = strict

    def validate(self, raw: dict[str, Any]) -> list[ValidationWarning]:
        """Validate configuration and return warnings.

        Args:
            raw: Raw configuration dictionary from YAML

        Returns:
            List of validation warnings (empty if valid)

        Raises:
            ValueError: If critical validation fails
        """
        warnings: list[ValidationWarning] = []

        # Runtime validation
        warnings.extend(self._validate_runtime(raw.get("runtime", {})))

        # Storage validation
        warnings.extend(self._validate_storage(raw.get("storage", {})))

        # Transport validation
        warnings.extend(self._validate_transport(raw.get("transport", {})))

        # Recovery validation
        warnings.extend(self._validate_recovery(raw.get("recovery", {})))

        # Federation validation
        warnings.extend(
            self._validate_federation(
                raw.get("federation", {}), raw.get("runtime", {})
            )
        )

        # Quarantine validation
        warnings.extend(self._validate_quarantine(raw.get("quarantine", {})))

        # Check for errors if strict mode
        if self.strict:
            errors = [w for w in warnings if w.severity == "error"]
            if errors:
                msg = "\n".join(f"  - {w.field}: {w.message}" for w in errors)
                raise ValueError(f"Configuration validation failed:\n{msg}")

        return warnings

    def _validate_runtime(self, runtime: dict[str, Any]) -> list[ValidationWarning]:
        """Validate runtime configuration."""
        warnings = []

        # Frame depth bounds
        depth = runtime.get("default_frame_depth", 4)
        if not isinstance(depth, int):
            warnings.append(
                ValidationWarning(
                    "runtime.default_frame_depth",
                    f"Must be integer, got {type(depth).__name__}",
                    "error",
                )
            )
        elif depth < self.MIN_FRAME_DEPTH or depth > self.MAX_FRAME_DEPTH:
            warnings.append(
                ValidationWarning(
                    "runtime.default_frame_depth",
                    f"Must be in range [{self.MIN_FRAME_DEPTH}, {self.MAX_FRAME_DEPTH}]",
                    "error",
                )
            )

        return warnings

    def _validate_storage(self, storage: dict[str, Any]) -> list[ValidationWarning]:
        """Validate storage configuration."""
        warnings = []

        db_path = storage.get("path", "")
        encrypt_at_rest = storage.get("encrypt_at_rest", False)
        master_key_file = storage.get("master_key_file", "")

        # Warn if encryption disabled for persistent storage
        if db_path and db_path != ":memory:" and not encrypt_at_rest:
            warnings.append(
                ValidationWarning(
                    "storage.encrypt_at_rest",
                    "Encryption at rest is disabled for persistent database (security risk)",
                    "warning",
                )
            )

        # Require master key if encryption enabled
        if encrypt_at_rest:
            if not master_key_file:
                warnings.append(
                    ValidationWarning(
                        "storage.master_key_file",
                        "encrypt_at_rest=true requires master_key_file",
                        "error",
                    )
                )
            else:
                # Validate key file exists and has correct size
                key_path = Path(master_key_file)
                if not key_path.exists():
                    warnings.append(
                        ValidationWarning(
                            "storage.master_key_file",
                            f"File not found: {master_key_file}",
                            "error",
                        )
                    )
                elif not key_path.is_file():
                    warnings.append(
                        ValidationWarning(
                            "storage.master_key_file",
                            f"Not a file: {master_key_file}",
                            "error",
                        )
                    )
                else:
                    # Check file size
                    try:
                        size = os.path.getsize(master_key_file)
                        if size != self.MASTER_KEY_SIZE:
                            warnings.append(
                                ValidationWarning(
                                    "storage.master_key_file",
                                    f"Key file must be exactly {self.MASTER_KEY_SIZE} bytes (got {size})",
                                    "error",
                                )
                            )
                    except OSError as e:
                        warnings.append(
                            ValidationWarning(
                                "storage.master_key_file",
                                f"Cannot read file: {e}",
                                "error",
                            )
                        )

        # Warn if WAL mode disabled
        if not storage.get("wal_mode", True):
            warnings.append(
                ValidationWarning(
                    "storage.wal_mode",
                    "WAL mode disabled (reduces atomicity guarantees)",
                    "warning",
                )
            )

        return warnings

    def _validate_transport(
        self, transport: dict[str, Any]
    ) -> list[ValidationWarning]:
        """Validate transport configuration."""
        warnings = []

        port = transport.get("port", 9876)
        if not isinstance(port, int):
            warnings.append(
                ValidationWarning(
                    "transport.port",
                    f"Must be integer, got {type(port).__name__}",
                    "error",
                )
            )
        elif port < self.MIN_PORT or port > self.MAX_PORT:
            warnings.append(
                ValidationWarning(
                    "transport.port",
                    f"Must be in range [{self.MIN_PORT}, {self.MAX_PORT}]",
                    "error",
                )
            )

        # Warn about 0.0.0.0 binding
        host = transport.get("host", "0.0.0.0")
        if host == "0.0.0.0":
            warnings.append(
                ValidationWarning(
                    "transport.host",
                    "Listening on 0.0.0.0 (all interfaces) - ensure firewall rules restrict access",
                    "warning",
                )
            )

        # Validate timeout values
        for timeout_field in ["connect_timeout", "read_timeout", "write_timeout"]:
            timeout = transport.get(timeout_field, 30.0)
            if not isinstance(timeout, (int, float)):
                warnings.append(
                    ValidationWarning(
                        f"transport.{timeout_field}",
                        f"Must be numeric, got {type(timeout).__name__}",
                        "error",
                    )
                )
            elif timeout <= 0:
                warnings.append(
                    ValidationWarning(
                        f"transport.{timeout_field}",
                        "Must be positive",
                        "error",
                    )
                )

        return warnings

    def _validate_recovery(self, recovery: dict[str, Any]) -> list[ValidationWarning]:
        """Validate recovery configuration."""
        warnings = []

        max_attempts = recovery.get("max_attempts", 3)
        if max_attempts < 1:
            warnings.append(
                ValidationWarning(
                    "recovery.max_attempts",
                    "Must be at least 1",
                    "error",
                )
            )

        timeout = recovery.get("timeout_seconds", 30)
        if timeout <= 0:
            warnings.append(
                ValidationWarning(
                    "recovery.timeout_seconds",
                    "Must be positive",
                    "error",
                )
            )

        return warnings

    def _validate_federation(
        self, federation: dict[str, Any], runtime: dict[str, Any]
    ) -> list[ValidationWarning]:
        """Validate federation configuration."""
        warnings = []

        peers = federation.get("peers", [])
        if not peers:
            return warnings  # No federation, skip validation

        # Require deployment_id and instance_id for federation
        deployment_id = runtime.get("deployment_id", "")
        instance_id = runtime.get("instance_id", "")

        if not deployment_id or not instance_id:
            warnings.append(
                ValidationWarning(
                    "runtime.deployment_id/instance_id",
                    "Federation configured but deployment_id or instance_id is empty (insecure)",
                    "error",
                )
            )

        # Validate each peer
        for idx, peer in enumerate(peers):
            if not isinstance(peer, dict):
                continue

            runtime_id = peer.get("runtime_id", "")
            if not runtime_id:
                warnings.append(
                    ValidationWarning(
                        f"federation.peers[{idx}].runtime_id",
                        "Peer runtime_id is empty",
                        "error",
                    )
                )

            endpoint = peer.get("endpoint", "")
            if not endpoint:
                warnings.append(
                    ValidationWarning(
                        f"federation.peers[{idx}].endpoint",
                        "Peer endpoint is empty",
                        "error",
                    )
                )
            elif ":" not in endpoint:
                warnings.append(
                    ValidationWarning(
                        f"federation.peers[{idx}].endpoint",
                        "Endpoint must be in 'host:port' format",
                        "error",
                    )
                )

            bootstrap = peer.get("bootstrap", "deterministic")
            if bootstrap not in ("deterministic", "ceremonial"):
                warnings.append(
                    ValidationWarning(
                        f"federation.peers[{idx}].bootstrap",
                        f"Invalid bootstrap mode: {bootstrap} (must be 'deterministic' or 'ceremonial')",
                        "error",
                    )
                )

        return warnings

    def _validate_quarantine(
        self, quarantine: dict[str, Any]
    ) -> list[ValidationWarning]:
        """Validate quarantine configuration."""
        warnings = []

        threshold = quarantine.get("validation_failure_threshold", 3)
        if not isinstance(threshold, int):
            warnings.append(
                ValidationWarning(
                    "quarantine.validation_failure_threshold",
                    f"Must be integer, got {type(threshold).__name__}",
                    "error",
                )
            )
        elif threshold < self.MIN_VALIDATION_THRESHOLD:
            warnings.append(
                ValidationWarning(
                    "quarantine.validation_failure_threshold",
                    f"Must be at least {self.MIN_VALIDATION_THRESHOLD} for security",
                    "error",
                )
            )

        # Warn if rate limiting disabled
        max_rate = quarantine.get("max_message_rate", 0)
        if max_rate == 0:
            warnings.append(
                ValidationWarning(
                    "quarantine.max_message_rate",
                    "Message rate limiting disabled (DoS risk)",
                    "warning",
                )
            )

        # Warn if payload size unlimited
        max_size = quarantine.get("max_payload_size", 0)
        if max_size == 0:
            warnings.append(
                ValidationWarning(
                    "quarantine.max_payload_size",
                    "Payload size unlimited (memory exhaustion risk)",
                    "warning",
                )
            )

        return warnings
