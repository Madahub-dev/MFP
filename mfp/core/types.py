"""MFP Core Types — all data classes for the Mirror Frame Protocol.

Every type maps to a construct in the design documents. This module
imports only from the Python standard library.

Maps to: impl/I-01_types.md
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum, IntFlag


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOCK_SIZE: int = 16                # b = 16 bytes (128 bits) — spec.md §3.1
STATE_SIZE: int = 32                # 32 bytes (256 bits) — spec.md §2
MIN_FRAME_DEPTH: int = 2            # k >= 2 — spec.md §8.8
DEFAULT_FRAME_DEPTH: int = 4        # k = 4 — spec.md §5.6
PROTOCOL_MAGIC: bytes = b"MFP1"     # federation.md §5.3
PROTOCOL_VERSION: int = 0x0001      # federation.md §5.3
ENVELOPE_HEADER_SIZE: int = 64      # federation.md §5.3
ALGORITHM_AES_256_GCM: bytes = b"aes-256-gcm"  # spec.md §6.5


# ---------------------------------------------------------------------------
# Primitive Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StateValue:
    """A 32-byte (256-bit) value used for ratchet state, keys, and seeds.

    Maps to: spec.md §2.
    """
    data: bytes

    def __post_init__(self) -> None:
        if len(self.data) != 32:
            raise ValueError(f"StateValue must be 32 bytes, got {len(self.data)}")

    def __repr__(self) -> str:
        return f"StateValue({self.data[:4].hex()}...)"


@dataclass(frozen=True)
class Block:
    """A 16-byte (128-bit) frame block.

    Maps to: spec.md §3.1.
    """
    data: bytes

    def __post_init__(self) -> None:
        if len(self.data) != 16:
            raise ValueError(f"Block must be 16 bytes, got {len(self.data)}")

    def reverse(self) -> Block:
        """Per-block byte reversal.

        Maps to: spec.md §3.4 — reverse(B) = (bn, bn-1, ..., b1).
        """
        return Block(self.data[::-1])


# ---------------------------------------------------------------------------
# Frame Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """An ordered sequence of k blocks forming the frame_open.

    Maps to: spec.md §3.3.
    """
    blocks: tuple[Block, ...]

    def __post_init__(self) -> None:
        if len(self.blocks) < MIN_FRAME_DEPTH:
            raise ValueError(
                f"Frame depth must be >= {MIN_FRAME_DEPTH}, got {len(self.blocks)}"
            )

    @property
    def depth(self) -> int:
        return len(self.blocks)

    def mirror(self) -> Frame:
        """Produce frame_close: reverse block order, reverse each block's bytes.

        Maps to: spec.md §3.4.
        """
        return Frame(tuple(b.reverse() for b in reversed(self.blocks)))

    def to_bytes(self) -> bytes:
        return b"".join(b.data for b in self.blocks)

    @classmethod
    def from_bytes(cls, data: bytes, depth: int) -> Frame:
        expected = depth * BLOCK_SIZE
        if len(data) != expected:
            raise ValueError(f"Expected {expected} bytes for depth {depth}, got {len(data)}")
        blocks = tuple(
            Block(data[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE])
            for i in range(depth)
        )
        return cls(blocks)


# ---------------------------------------------------------------------------
# Ratchet State Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelState:
    """Local ratchet state for a single channel.

    Maps to: spec.md §4.
    """
    local_state: StateValue
    step: int

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError(f"Step must be >= 0, got {self.step}")


@dataclass(frozen=True)
class GlobalState:
    """Runtime-wide global ratchet state.

    Maps to: spec.md §4.3.
    """
    value: StateValue


@dataclass(frozen=True)
class BilateralState:
    """Bilateral ratchet state between two runtimes.

    Maps to: spec.md §7.3.
    """
    ratchet_state: StateValue
    shared_prng_seed: StateValue
    step: int

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError(f"Step must be >= 0, got {self.step}")


# ---------------------------------------------------------------------------
# Encoding Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EncodingContext:
    """Parameters for encoding/decoding a payload.

    Maps to: spec.md §6.3.
    """
    algorithm_id: bytes
    key: StateValue
    channel_id: ChannelId
    step: int


# ---------------------------------------------------------------------------
# Channel Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelId:
    """Runtime-assigned channel identifier.

    Maps to: runtime-interface.md §6.1.
    """
    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 16:
            raise ValueError(f"ChannelId must be 16 bytes, got {len(self.value)}")


class ChannelStatus(Enum):
    """Channel lifecycle status."""
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    CLOSED = "closed"


@dataclass
class Channel:
    """A bidirectional communication path between two agents.

    NOTE: Mutable. The runtime mutates channel state during message processing.
    Agents never hold Channel references.
    """
    channel_id: ChannelId
    agent_a: AgentId
    agent_b: AgentId
    state: ChannelState
    depth: int
    status: ChannelStatus
    validation_failure_count: int = 0

    def __post_init__(self) -> None:
        if self.depth < MIN_FRAME_DEPTH:
            raise ValueError(f"Frame depth must be >= {MIN_FRAME_DEPTH}, got {self.depth}")


@dataclass(frozen=True)
class ChannelInfo:
    """Agent-visible channel information. No protocol-internal state."""
    channel_id: ChannelId
    peer: AgentId
    status: ChannelStatus


# ---------------------------------------------------------------------------
# Agent Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentId:
    """Runtime-assigned agent identifier."""
    value: bytes

    def __lt__(self, other: AgentId) -> bool:
        return self.value < other.value

    def __le__(self, other: AgentId) -> bool:
        return self.value <= other.value

    def __gt__(self, other: AgentId) -> bool:
        return self.value > other.value

    def __ge__(self, other: AgentId) -> bool:
        return self.value >= other.value


class AgentState(Enum):
    """Agent lifecycle state.

    Maps to: agent-lifecycle.md §2.
    """
    UNREGISTERED = "unregistered"
    BINDING = "binding"
    BOUND = "bound"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class AgentStatus:
    """Agent-visible status. Returned by mfp_status()."""
    agent_id: AgentId
    state: AgentState
    channel_count: int


# ---------------------------------------------------------------------------
# Message Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MessageId:
    """Runtime-assigned message identifier."""
    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 16:
            raise ValueError(f"MessageId must be 16 bytes, got {len(self.value)}")


@dataclass(frozen=True)
class Receipt:
    """Confirmation that a message was accepted by the runtime."""
    message_id: MessageId
    channel: ChannelId
    step: int
    correlation_id: str = field(default="")  # Trace ID for observability


@dataclass(frozen=True)
class DeliveredMessage:
    """Decoded message delivered to the destination agent."""
    payload: bytes
    sender: AgentId
    channel: ChannelId
    message_id: MessageId
    correlation_id: str = field(default="")  # Trace ID for observability
    message_id: MessageId


@dataclass(frozen=True)
class ProtocolMessage:
    """A complete protocol message: frame_open || E(P) || frame_close.

    Maps to: spec.md §3.5.
    """
    frame_open: Frame
    encoded_payload: bytes
    frame_close: Frame

    def to_bytes(self) -> bytes:
        return self.frame_open.to_bytes() + self.encoded_payload + self.frame_close.to_bytes()

    @classmethod
    def from_bytes(cls, data: bytes, depth: int) -> ProtocolMessage:
        frame_size = depth * BLOCK_SIZE
        if len(data) < 2 * frame_size:
            raise ValueError(f"Message too short for depth {depth}")
        frame_open = Frame.from_bytes(data[:frame_size], depth)
        frame_close = Frame.from_bytes(data[-frame_size:], depth)
        encoded_payload = data[frame_size:-frame_size]
        return cls(frame_open, encoded_payload, frame_close)


# ---------------------------------------------------------------------------
# Federation Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeId:
    """Runtime identity for federation.

    Maps to: federation.md §9.
    """
    value: StateValue


class EnvelopeFlags(IntFlag):
    """Wire format envelope flags."""
    NONE = 0
    ACK = 1 << 0
    RECOVERY = 1 << 1


@dataclass(frozen=True)
class EnvelopeHeader:
    """Wire format envelope header for cross-runtime messages.

    Maps to: federation.md §5.3 — 64 bytes fixed.
    """
    magic: bytes
    version: int
    flags: EnvelopeFlags
    frame_depth: int
    payload_len: int
    channel_id: ChannelId
    step: int
    sender_runtime: bytes
    reserved: bytes

    def to_bytes(self) -> bytes:
        return struct.pack(
            ">4sHHII16sQ16s8s",
            self.magic,
            self.version,
            self.flags,
            self.frame_depth,
            self.payload_len,
            self.channel_id.value,
            self.step,
            self.sender_runtime,
            self.reserved,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> EnvelopeHeader:
        if len(data) != ENVELOPE_HEADER_SIZE:
            raise ValueError(
                f"Envelope header must be {ENVELOPE_HEADER_SIZE} bytes, got {len(data)}"
            )
        (
            magic, version, flags, frame_depth, payload_len,
            channel_id, step, sender_runtime, reserved,
        ) = struct.unpack(">4sHHII16sQ16s8s", data)
        return cls(
            magic=magic,
            version=version,
            flags=EnvelopeFlags(flags),
            frame_depth=frame_depth,
            payload_len=payload_len,
            channel_id=ChannelId(channel_id),
            step=step,
            sender_runtime=sender_runtime,
            reserved=reserved,
        )


@dataclass(frozen=True)
class WireMessage:
    """Complete cross-runtime message: envelope + protocol message."""
    header: EnvelopeHeader
    message: ProtocolMessage

    def to_bytes(self) -> bytes:
        return self.header.to_bytes() + self.message.to_bytes()


@dataclass(frozen=True)
class RecoveryMessage:
    """Recovery protocol negotiation message."""
    channel_id: ChannelId
    step: int
    state_hash: StateValue


# ---------------------------------------------------------------------------
# Error Types
# ---------------------------------------------------------------------------

class MFPError(Exception):
    """Base exception for all MFP errors."""
    pass


class AgentErrorCode(Enum):
    """Error codes visible to agents."""
    UNBOUND = "unbound"
    QUARANTINED = "quarantined"
    INVALID_CHANNEL = "invalid_channel"
    CHANNEL_CLOSED = "channel_closed"
    CHANNEL_QUARANTINED = "channel_quarantined"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    TIMEOUT = "timeout"


class AgentError(MFPError):
    """Error returned to agents from protocol tools."""
    def __init__(self, code: AgentErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class ValidationError(MFPError):
    """Frame or decode validation failure. Never visible to agents."""
    pass


class FrameValidationError(ValidationError):
    """Frame check failed."""
    pass


class DecodeError(ValidationError):
    """Payload decode failed."""
    pass


class InfrastructureError(MFPError):
    """Runtime internal failure. Never visible to agents."""
    pass


class CSPRNGError(InfrastructureError):
    """OS CSPRNG unavailable or failed."""
    pass


class StateCorruptionError(InfrastructureError):
    """Ratchet state inconsistency detected."""
    pass


class FederationError(MFPError):
    """Cross-runtime communication failure."""
    pass


class EnvelopeError(FederationError):
    """Wire format envelope validation failure."""
    pass


class RecoveryError(FederationError):
    """Bilateral state recovery failure."""
    pass


class RecoveryEscalation(FederationError):
    """Recovery limits exceeded — operator intervention required."""
    pass
