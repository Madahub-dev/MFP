"""Input validation for security-critical operations."""

from __future__ import annotations

from mfp.core.types import AgentId, ChannelId, StateValue, ValidationError

# Security bounds
MIN_FRAME_DEPTH = 2
MAX_FRAME_DEPTH = 32
STEP_WARN_THRESHOLD = 2**60  # Warn when approaching overflow
STEP_HALT_THRESHOLD = 2**62  # Halt before overflow risk
MAX_PAYLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB absolute max


def validate_frame_depth(depth: int) -> None:
    """Validate frame depth is within secure bounds.

    Args:
        depth: Requested frame depth

    Raises:
        ValidationError: If depth is out of bounds
    """
    if not isinstance(depth, int):
        raise ValidationError(
            f"Frame depth must be integer, got {type(depth).__name__}"
        )

    if depth < MIN_FRAME_DEPTH:
        raise ValidationError(
            f"Frame depth {depth} below minimum {MIN_FRAME_DEPTH} (security risk)"
        )

    if depth > MAX_FRAME_DEPTH:
        raise ValidationError(
            f"Frame depth {depth} exceeds maximum {MAX_FRAME_DEPTH} (performance risk)"
        )


def validate_step_counter(step: int) -> tuple[bool, str | None]:
    """Check step counter for overflow risk.

    Args:
        step: Current step value

    Returns:
        Tuple of (safe, warning_message)
        - safe=False means halt immediately
        - warning_message is set if approaching overflow
    """
    if step < 0:
        return False, "Step counter is negative (corruption)"

    if step >= STEP_HALT_THRESHOLD:
        return False, f"Step counter {step} at critical overflow threshold"

    if step >= STEP_WARN_THRESHOLD:
        return True, f"Step counter {step} approaching overflow (plan key rotation)"

    return True, None


def validate_payload_size(size: int, max_size: int) -> None:
    """Validate payload size against limits.

    Args:
        size: Payload size in bytes
        max_size: Configured maximum (0 = use absolute max)

    Raises:
        ValidationError: If size exceeds limit
    """
    if size < 0:
        raise ValidationError("Payload size cannot be negative")

    effective_max = max_size if max_size > 0 else MAX_PAYLOAD_SIZE_BYTES

    if size > effective_max:
        raise ValidationError(
            f"Payload size {size} bytes exceeds limit {effective_max} bytes"
        )


def validate_agent_id(agent_id: bytes) -> None:
    """Validate agent ID format.

    Args:
        agent_id: Agent identifier

    Raises:
        ValidationError: If ID is invalid
    """
    if not isinstance(agent_id, bytes):
        raise ValidationError(
            f"Agent ID must be bytes, got {type(agent_id).__name__}"
        )

    if len(agent_id) != 32:
        raise ValidationError(
            f"Agent ID must be 32 bytes, got {len(agent_id)} bytes"
        )


def validate_channel_id(channel_id: bytes) -> None:
    """Validate channel ID format.

    Args:
        channel_id: Channel identifier

    Raises:
        ValidationError: If ID is invalid
    """
    if not isinstance(channel_id, bytes):
        raise ValidationError(
            f"Channel ID must be bytes, got {type(channel_id).__name__}"
        )

    if len(channel_id) != 32:
        raise ValidationError(
            f"Channel ID must be 32 bytes, got {len(channel_id)} bytes"
        )


def validate_state_value(state: bytes) -> None:
    """Validate ratchet state value format.

    Args:
        state: State value

    Raises:
        ValidationError: If state is invalid
    """
    if not isinstance(state, bytes):
        raise ValidationError(
            f"State value must be bytes, got {type(state).__name__}"
        )

    if len(state) != 32:
        raise ValidationError(
            f"State value must be 32 bytes, got {len(state)} bytes"
        )


def validate_master_key(key: bytes) -> None:
    """Validate encryption master key.

    Args:
        key: Master key

    Raises:
        ValidationError: If key is invalid or weak
    """
    if not isinstance(key, bytes):
        raise ValidationError(
            f"Master key must be bytes, got {type(key).__name__}"
        )

    if len(key) != 32:
        raise ValidationError(
            f"Master key must be exactly 32 bytes, got {len(key)} bytes (weak key)"
        )

    # Check for all-zero key (weak)
    if key == b"\x00" * 32:
        raise ValidationError(
            "Master key is all zeros (insecure, generate random key)"
        )


def sanitize_error_message(message: str, redact_ids: bool = True) -> str:
    """Sanitize error messages for agent-facing output.

    Removes internal details that could leak state information.

    Args:
        message: Original error message
        redact_ids: If True, redact hex ID prefixes

    Returns:
        Sanitized message
    """
    if not redact_ids:
        return message

    # Redact hex patterns (likely IDs)
    import re

    # Pattern: 8+ hex chars (likely ID prefixes)
    sanitized = re.sub(r"\b[0-9a-f]{8,}\b", "[REDACTED]", message, flags=re.IGNORECASE)

    return sanitized
