"""MFP Message Pipeline — the six-stage message lifecycle.

ACCEPT → FRAME → ENCODE → VALIDATE → DECODE → DELIVER

The pipeline is a function, not a class. It receives state, calls core
functions, and returns results. State advancement is the caller's job.

Maps to: impl/I-07_pipeline.md
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mfp.core.encoding import build_encoding_context, decode, encode
from mfp.core.frame import assemble_message, sample_frame, validate_frame
from mfp.core.primitives import random_id
from mfp.core.ratchet import advance
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
    Channel,
    ChannelId,
    ChannelStatus,
    DecodeError,
    DeliveredMessage,
    Frame,
    FrameValidationError,
    GlobalState,
    MessageId,
    ProtocolMessage,
    Receipt,
    StateValue,
)

AgentCallable = Callable[[DeliveredMessage], None]


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime configuration parameters."""
    # Identity (empty = auto-generate random)
    deployment_id: bytes = b""
    instance_id: bytes = b""
    # Frame
    default_frame_depth: int = 4
    # Quarantine (secure defaults for production)
    validation_failure_threshold: int = 5
    max_message_rate: int = 1000  # messages per second
    max_payload_size: int = 1_048_576  # 1 MB
    # Encoding
    encoding_algorithm: bytes = b"aes-256-gcm"


@dataclass(frozen=True)
class PipelineResult:
    """Result of a successful pipeline execution."""
    receipt: Receipt
    delivered: DeliveredMessage
    new_local_state: StateValue
    frame: Frame


# ---------------------------------------------------------------------------
# Stage 1 — ACCEPT
# ---------------------------------------------------------------------------

def accept(
    sender: AgentId,
    channel: Channel,
    payload: bytes,
    config: RuntimeConfig,
) -> MessageId:
    """Validate the send request.

    Maps to: runtime-interface.md §4.2.
    """
    # Verify sender is on this channel
    if sender != channel.agent_a and sender != channel.agent_b:
        raise AgentError(AgentErrorCode.INVALID_CHANNEL, "Sender not bound to channel")

    # Verify channel status
    if channel.status == ChannelStatus.QUARANTINED:
        raise AgentError(AgentErrorCode.CHANNEL_QUARANTINED, "Channel is quarantined")
    if channel.status == ChannelStatus.CLOSED:
        raise AgentError(AgentErrorCode.CHANNEL_CLOSED, "Channel is closed")

    # Verify payload size
    if config.max_payload_size > 0 and len(payload) > config.max_payload_size:
        raise AgentError(
            AgentErrorCode.PAYLOAD_TOO_LARGE,
            f"Payload {len(payload)} bytes exceeds limit {config.max_payload_size}",
        )

    return MessageId(random_id(16))


# ---------------------------------------------------------------------------
# Stage 2 — FRAME
# ---------------------------------------------------------------------------

def frame_stage(channel: Channel, global_state: GlobalState) -> Frame:
    """Derive and sample the frame.

    Maps to: runtime-interface.md §4.3.
    """
    return sample_frame(
        local_state=channel.state.local_state,
        step=channel.state.step,
        global_state=global_state.value,
        depth=channel.depth,
    )


# ---------------------------------------------------------------------------
# Stage 3 — ENCODE
# ---------------------------------------------------------------------------

def encode_stage(
    channel: Channel,
    payload: bytes,
    frame: Frame,
    config: RuntimeConfig,
) -> ProtocolMessage:
    """Encode payload and assemble the protocol message.

    Maps to: runtime-interface.md §4.4.
    """
    ctx = build_encoding_context(
        local_state=channel.state.local_state,
        channel_id=channel.channel_id,
        step=channel.state.step,
        algorithm_id=config.encoding_algorithm,
    )
    encoded = encode(payload, ctx)
    return assemble_message(frame, encoded)


# ---------------------------------------------------------------------------
# Stage 4 — VALIDATE
# ---------------------------------------------------------------------------

def validate_stage(msg: ProtocolMessage, expected_frame: Frame) -> None:
    """Validate the message's frame pair.

    Maps to: runtime-interface.md §4.5.
    """
    if not validate_frame(msg.frame_open, msg.frame_close, expected_frame):
        raise FrameValidationError("Frame validation failed")


# ---------------------------------------------------------------------------
# Stage 5 — DECODE
# ---------------------------------------------------------------------------

def decode_stage(
    msg: ProtocolMessage,
    channel: Channel,
    config: RuntimeConfig,
) -> bytes:
    """Decode the encrypted payload.

    Maps to: runtime-interface.md §4.6.
    """
    ctx = build_encoding_context(
        local_state=channel.state.local_state,
        channel_id=channel.channel_id,
        step=channel.state.step,
        algorithm_id=config.encoding_algorithm,
    )
    payload = decode(msg.encoded_payload, ctx)
    if payload is None:
        raise DecodeError("Payload integrity check failed")
    return payload


# ---------------------------------------------------------------------------
# Stage 6 — DELIVER
# ---------------------------------------------------------------------------

def deliver_stage(
    payload: bytes,
    sender: AgentId,
    channel_id: ChannelId,
    message_id: MessageId,
    deliver: AgentCallable,
) -> DeliveredMessage:
    """Deliver decoded message to the destination agent.

    Maps to: runtime-interface.md §4.7.
    """
    msg = DeliveredMessage(
        payload=payload,
        sender=sender,
        channel=channel_id,
        message_id=message_id,
    )
    deliver(msg)
    return msg


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def process_message(
    sender: AgentId,
    channel: Channel,
    payload: bytes,
    global_state: GlobalState,
    config: RuntimeConfig,
    deliver: AgentCallable,
) -> PipelineResult:
    """Execute the six-stage message pipeline.

    Returns PipelineResult with new state. The caller (Runtime) applies
    the state mutation and Sg recomputation.

    Maps to: runtime-interface.md §4.
    """
    # Stage 1: ACCEPT
    message_id = accept(sender, channel, payload, config)

    # Stage 2: FRAME
    frame = frame_stage(channel, global_state)

    # Stage 3: ENCODE
    protocol_msg = encode_stage(channel, payload, frame, config)

    # Stage 4: VALIDATE (intra-runtime self-check)
    validate_stage(protocol_msg, frame)

    # Stage 5: DECODE
    decoded = decode_stage(protocol_msg, channel, config)

    # Stage 6: DELIVER
    delivered = deliver_stage(decoded, sender, channel.channel_id, message_id, deliver)

    # Compute new state (not yet applied)
    new_local = advance(channel.state.local_state, frame)

    return PipelineResult(
        receipt=Receipt(
            message_id=message_id,
            channel=channel.channel_id,
            step=channel.state.step,
        ),
        delivered=delivered,
        new_local_state=new_local,
        frame=frame,
    )
