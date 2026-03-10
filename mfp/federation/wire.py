"""MFP Wire Format — envelope assembly, parsing, validation.

Serializes cross-runtime messages for TCP transmission.
64-byte fixed envelope header + variable-length protocol message body.

Maps to: impl/I-16_wire.md
"""

from __future__ import annotations

from mfp.core.types import (
    BLOCK_SIZE,
    ENVELOPE_HEADER_SIZE,
    PROTOCOL_MAGIC,
    PROTOCOL_VERSION,
    ChannelId,
    EnvelopeFlags,
    EnvelopeHeader,
    EnvelopeError,
    MIN_FRAME_DEPTH,
    ProtocolMessage,
    WireMessage,
)


# ---------------------------------------------------------------------------
# Envelope Construction
# ---------------------------------------------------------------------------

def build_envelope_header(
    channel_id: ChannelId,
    step: int,
    frame_depth: int,
    payload_len: int,
    sender_runtime: bytes,
    flags: EnvelopeFlags = EnvelopeFlags.NONE,
) -> EnvelopeHeader:
    """Build an envelope header with protocol defaults.

    sender_runtime is truncated to first 16 bytes if longer.
    """
    return EnvelopeHeader(
        magic=PROTOCOL_MAGIC,
        version=PROTOCOL_VERSION,
        flags=flags,
        frame_depth=frame_depth,
        payload_len=payload_len,
        channel_id=channel_id,
        step=step,
        sender_runtime=sender_runtime[:16].ljust(16, b"\x00"),
        reserved=b"\x00" * 8,
    )


# ---------------------------------------------------------------------------
# Wire Message Assembly / Parsing
# ---------------------------------------------------------------------------

def assemble_wire_message(
    header: EnvelopeHeader,
    protocol_msg: ProtocolMessage,
) -> bytes:
    """Assemble a complete wire message: header + protocol message."""
    return header.to_bytes() + protocol_msg.to_bytes()


def parse_wire_message(data: bytes) -> tuple[EnvelopeHeader, ProtocolMessage]:
    """Parse a complete wire message from contiguous bytes.

    Raises ValueError or EnvelopeError on invalid data.
    """
    if len(data) < ENVELOPE_HEADER_SIZE:
        raise EnvelopeError(
            f"Data too short for envelope: {len(data)} < {ENVELOPE_HEADER_SIZE}"
        )

    header = EnvelopeHeader.from_bytes(data[:ENVELOPE_HEADER_SIZE])
    body = data[ENVELOPE_HEADER_SIZE:]

    frame_size = header.frame_depth * BLOCK_SIZE
    expected_body = 2 * frame_size + header.payload_len
    if len(body) != expected_body:
        raise EnvelopeError(
            f"Body size mismatch: got {len(body)}, expected {expected_body}"
        )

    protocol_msg = ProtocolMessage.from_bytes(body, header.frame_depth)
    return header, protocol_msg


# ---------------------------------------------------------------------------
# Envelope Validation
# ---------------------------------------------------------------------------

def validate_envelope(
    header: EnvelopeHeader,
    known_channels: set[bytes] | None = None,
    expected_peer: bytes | None = None,
) -> list[str]:
    """Validate an envelope header. Returns list of error strings (empty = valid).

    Checks are ordered for fast rejection of garbage.
    """
    errors: list[str] = []

    # 1. Magic
    if header.magic != PROTOCOL_MAGIC:
        errors.append(
            f"Invalid magic: {header.magic!r}, expected {PROTOCOL_MAGIC!r}"
        )

    # 2. Version
    if header.version != PROTOCOL_VERSION:
        errors.append(
            f"Unsupported version: {header.version:#06x}"
        )

    # 3. Reserved must be zero
    if header.reserved != b"\x00" * 8:
        errors.append("Non-zero reserved field")

    # 4. Frame depth bounds
    if header.frame_depth < MIN_FRAME_DEPTH:
        errors.append(
            f"Frame depth {header.frame_depth} below minimum {MIN_FRAME_DEPTH}"
        )

    # 5. Channel lookup (optional)
    if known_channels is not None:
        if header.channel_id.value not in known_channels:
            errors.append("Unknown channel_id")

    # 6. Sender runtime (optional)
    if expected_peer is not None:
        if header.sender_runtime != expected_peer[:16].ljust(16, b"\x00"):
            errors.append("Sender runtime mismatch")

    return errors


def compute_body_size(frame_depth: int, payload_len: int) -> int:
    """Compute the expected body size from header fields."""
    return 2 * frame_depth * BLOCK_SIZE + payload_len
