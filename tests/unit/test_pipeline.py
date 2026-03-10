"""Unit tests for mfp/runtime/pipeline.py (I-07)."""

import pytest

from mfp.core.primitives import random_id, random_state_value
from mfp.core.ratchet import compose
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
    Channel,
    ChannelId,
    ChannelState,
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
from mfp.runtime.pipeline import (
    RuntimeConfig,
    PipelineResult,
    accept,
    decode_stage,
    encode_stage,
    frame_stage,
    process_message,
    validate_stage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state_value(fill: int = 0x42) -> StateValue:
    return StateValue(bytes([fill]) * 32)


def make_agent_id(label: bytes = b"agent-a") -> AgentId:
    return AgentId(label)


def make_channel_id() -> ChannelId:
    return ChannelId(random_id(16))


def make_channel(
    agent_a: AgentId | None = None,
    agent_b: AgentId | None = None,
    status: ChannelStatus = ChannelStatus.ACTIVE,
    depth: int = 4,
    local_state: StateValue | None = None,
    step: int = 0,
) -> Channel:
    if agent_a is None:
        agent_a = make_agent_id(b"agent-a")
    if agent_b is None:
        agent_b = make_agent_id(b"agent-b")
    if local_state is None:
        local_state = random_state_value()
    return Channel(
        channel_id=make_channel_id(),
        agent_a=agent_a,
        agent_b=agent_b,
        state=ChannelState(local_state=local_state, step=step),
        depth=depth,
        status=status,
    )


def make_global_state() -> GlobalState:
    return compose([random_state_value()])


def make_config(**overrides) -> RuntimeConfig:
    return RuntimeConfig(**overrides)


def noop_deliver(msg: DeliveredMessage) -> None:
    """No-op delivery callable."""
    pass


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------

class TestRuntimeConfig:
    def test_defaults(self):
        cfg = RuntimeConfig()
        assert cfg.deployment_id == b""
        assert cfg.instance_id == b""
        assert cfg.default_frame_depth == 4
        assert cfg.validation_failure_threshold == 3
        assert cfg.max_message_rate == 0
        assert cfg.max_payload_size == 0
        assert cfg.encoding_algorithm == b"aes-256-gcm"

    def test_custom_values(self):
        cfg = RuntimeConfig(
            deployment_id=b"dep",
            instance_id=b"inst",
            default_frame_depth=8,
            validation_failure_threshold=5,
            max_message_rate=100,
            max_payload_size=4096,
            encoding_algorithm=b"custom-algo",
        )
        assert cfg.deployment_id == b"dep"
        assert cfg.instance_id == b"inst"
        assert cfg.default_frame_depth == 8
        assert cfg.validation_failure_threshold == 5
        assert cfg.max_message_rate == 100
        assert cfg.max_payload_size == 4096
        assert cfg.encoding_algorithm == b"custom-algo"

    def test_frozen(self):
        cfg = RuntimeConfig()
        with pytest.raises(AttributeError):
            cfg.max_payload_size = 999


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------

class TestPipelineResult:
    def test_construction(self):
        mid = MessageId(random_id(16))
        cid = make_channel_id()
        delivered = DeliveredMessage(
            payload=b"hello",
            sender=make_agent_id(),
            channel=cid,
            message_id=mid,
        )
        receipt = Receipt(message_id=mid, channel=cid, step=0)
        new_local = random_state_value()
        from mfp.core.types import Block
        frame = Frame(tuple(Block(random_id(16)) for _ in range(4)))

        result = PipelineResult(
            receipt=receipt,
            delivered=delivered,
            new_local_state=new_local,
            frame=frame,
        )
        assert result.receipt == receipt
        assert result.delivered == delivered
        assert result.new_local_state == new_local
        assert result.frame == frame


# ---------------------------------------------------------------------------
# accept()
# ---------------------------------------------------------------------------

class TestAccept:
    def test_valid_sender_agent_a(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        config = make_config()
        mid = accept(agent_a, channel, b"payload", config)
        assert isinstance(mid, MessageId)
        assert len(mid.value) == 16

    def test_valid_sender_agent_b(self):
        agent_b = make_agent_id(b"bob")
        channel = make_channel(agent_b=agent_b)
        config = make_config()
        mid = accept(agent_b, channel, b"payload", config)
        assert isinstance(mid, MessageId)

    def test_sender_not_on_channel(self):
        outsider = make_agent_id(b"outsider")
        channel = make_channel(
            agent_a=make_agent_id(b"alice"),
            agent_b=make_agent_id(b"bob"),
        )
        config = make_config()
        with pytest.raises(AgentError) as exc_info:
            accept(outsider, channel, b"payload", config)
        assert exc_info.value.code == AgentErrorCode.INVALID_CHANNEL

    def test_quarantined_channel(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a, status=ChannelStatus.QUARANTINED)
        config = make_config()
        with pytest.raises(AgentError) as exc_info:
            accept(agent_a, channel, b"payload", config)
        assert exc_info.value.code == AgentErrorCode.CHANNEL_QUARANTINED

    def test_closed_channel(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a, status=ChannelStatus.CLOSED)
        config = make_config()
        with pytest.raises(AgentError) as exc_info:
            accept(agent_a, channel, b"payload", config)
        assert exc_info.value.code == AgentErrorCode.CHANNEL_CLOSED

    def test_payload_too_large(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        config = make_config(max_payload_size=10)
        with pytest.raises(AgentError) as exc_info:
            accept(agent_a, channel, b"x" * 11, config)
        assert exc_info.value.code == AgentErrorCode.PAYLOAD_TOO_LARGE

    def test_payload_at_limit_ok(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        config = make_config(max_payload_size=10)
        mid = accept(agent_a, channel, b"x" * 10, config)
        assert isinstance(mid, MessageId)

    def test_unlimited_payload_size(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        config = make_config(max_payload_size=0)
        mid = accept(agent_a, channel, b"x" * 100_000, config)
        assert isinstance(mid, MessageId)


# ---------------------------------------------------------------------------
# frame_stage()
# ---------------------------------------------------------------------------

class TestFrameStage:
    def test_returns_frame(self):
        channel = make_channel(depth=4)
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        assert isinstance(frame, Frame)

    def test_correct_depth(self):
        channel = make_channel(depth=6)
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        assert frame.depth == 6

    def test_default_depth(self):
        channel = make_channel(depth=4)
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        assert frame.depth == 4


# ---------------------------------------------------------------------------
# encode_stage()
# ---------------------------------------------------------------------------

class TestEncodeStage:
    def test_returns_protocol_message(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)
        assert isinstance(msg, ProtocolMessage)

    def test_has_frame_open(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)
        assert isinstance(msg.frame_open, Frame)
        assert msg.frame_open == frame

    def test_has_encoded_payload(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)
        assert isinstance(msg.encoded_payload, bytes)
        assert len(msg.encoded_payload) > 0
        # AES-256-GCM adds 16 bytes tag
        assert len(msg.encoded_payload) == len(b"hello") + 16

    def test_has_frame_close(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)
        assert isinstance(msg.frame_close, Frame)
        # frame_close is mirror of frame_open
        assert msg.frame_close == frame.mirror()


# ---------------------------------------------------------------------------
# validate_stage()
# ---------------------------------------------------------------------------

class TestValidateStage:
    def test_passes_with_correct_frame(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)
        # Should not raise
        validate_stage(msg, frame)

    def test_raises_with_wrong_frame(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"hello", frame, config)

        # Create a different frame
        wrong_frame = frame_stage(channel, make_global_state())
        with pytest.raises(FrameValidationError):
            validate_stage(msg, wrong_frame)


# ---------------------------------------------------------------------------
# decode_stage()
# ---------------------------------------------------------------------------

class TestDecodeStage:
    def test_decodes_to_original(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        payload = b"secret message"
        msg = encode_stage(channel, payload, frame, config)
        decoded = decode_stage(msg, channel, config)
        assert decoded == payload

    def test_empty_payload_roundtrip(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"", frame, config)
        decoded = decode_stage(msg, channel, config)
        assert decoded == b""

    def test_tampered_payload_raises(self):
        channel = make_channel()
        gs = make_global_state()
        frame = frame_stage(channel, gs)
        config = make_config()
        msg = encode_stage(channel, b"payload", frame, config)

        # Tamper with the encoded payload
        tampered_payload = msg.encoded_payload[:-1] + bytes(
            [msg.encoded_payload[-1] ^ 0xFF]
        )
        tampered_msg = ProtocolMessage(
            frame_open=msg.frame_open,
            encoded_payload=tampered_payload,
            frame_close=msg.frame_close,
        )
        with pytest.raises(DecodeError):
            decode_stage(tampered_msg, channel, config)


# ---------------------------------------------------------------------------
# process_message() — full pipeline
# ---------------------------------------------------------------------------

class TestProcessMessage:
    def test_full_roundtrip(self):
        agent_a = make_agent_id(b"alice")
        agent_b = make_agent_id(b"bob")
        channel = make_channel(agent_a=agent_a, agent_b=agent_b)
        gs = make_global_state()
        config = make_config()
        payload = b"hello bob"

        delivered_messages = []

        def capture_deliver(msg: DeliveredMessage) -> None:
            delivered_messages.append(msg)

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=payload,
            global_state=gs,
            config=config,
            deliver=capture_deliver,
        )

        assert isinstance(result, PipelineResult)
        assert len(delivered_messages) == 1

    def test_payload_integrity(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        gs = make_global_state()
        config = make_config()
        payload = b"important data"

        delivered_messages = []

        def capture_deliver(msg: DeliveredMessage) -> None:
            delivered_messages.append(msg)

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=payload,
            global_state=gs,
            config=config,
            deliver=capture_deliver,
        )

        assert delivered_messages[0].payload == payload
        assert result.delivered.payload == payload

    def test_receipt_fields(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a, step=5)
        gs = make_global_state()
        config = make_config()

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=b"msg",
            global_state=gs,
            config=config,
            deliver=noop_deliver,
        )

        assert isinstance(result.receipt, Receipt)
        assert isinstance(result.receipt.message_id, MessageId)
        assert result.receipt.channel == channel.channel_id
        assert result.receipt.step == 5

    def test_new_local_state_differs(self):
        agent_a = make_agent_id(b"alice")
        local_state = random_state_value()
        channel = make_channel(agent_a=agent_a, local_state=local_state)
        gs = make_global_state()
        config = make_config()

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=b"msg",
            global_state=gs,
            config=config,
            deliver=noop_deliver,
        )

        assert isinstance(result.new_local_state, StateValue)
        assert result.new_local_state != local_state

    def test_delivered_message_has_sender(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a)
        gs = make_global_state()
        config = make_config()

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=b"msg",
            global_state=gs,
            config=config,
            deliver=noop_deliver,
        )

        assert result.delivered.sender == agent_a
        assert result.delivered.channel == channel.channel_id

    def test_frame_returned(self):
        agent_a = make_agent_id(b"alice")
        channel = make_channel(agent_a=agent_a, depth=4)
        gs = make_global_state()
        config = make_config()

        result = process_message(
            sender=agent_a,
            channel=channel,
            payload=b"msg",
            global_state=gs,
            config=config,
            deliver=noop_deliver,
        )

        assert isinstance(result.frame, Frame)
        assert result.frame.depth == 4
