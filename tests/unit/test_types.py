"""Unit tests for mfp/core/types.py (I-01)."""

import pytest

from mfp.core.types import (
    BLOCK_SIZE,
    DEFAULT_FRAME_DEPTH,
    ENVELOPE_HEADER_SIZE,
    MIN_FRAME_DEPTH,
    PROTOCOL_MAGIC,
    PROTOCOL_VERSION,
    STATE_SIZE,
    AgentError,
    AgentErrorCode,
    AgentId,
    AgentState,
    AgentStatus,
    BilateralState,
    Block,
    Channel,
    ChannelId,
    ChannelInfo,
    ChannelState,
    ChannelStatus,
    CSPRNGError,
    DecodeError,
    DeliveredMessage,
    EncodingContext,
    EnvelopeFlags,
    EnvelopeHeader,
    Frame,
    FrameValidationError,
    GlobalState,
    InfrastructureError,
    MFPError,
    MessageId,
    ProtocolMessage,
    Receipt,
    RecoveryMessage,
    RuntimeId,
    StateCorruptionError,
    StateValue,
    ValidationError,
    WireMessage,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_block_size(self):
        assert BLOCK_SIZE == 16

    def test_state_size(self):
        assert STATE_SIZE == 32

    def test_min_frame_depth(self):
        assert MIN_FRAME_DEPTH == 2

    def test_default_frame_depth(self):
        assert DEFAULT_FRAME_DEPTH == 4

    def test_envelope_header_size(self):
        assert ENVELOPE_HEADER_SIZE == 64

    def test_protocol_magic(self):
        assert PROTOCOL_MAGIC == b"MFP1"


# ---------------------------------------------------------------------------
# StateValue
# ---------------------------------------------------------------------------

class TestStateValue:
    def test_valid(self):
        sv = StateValue(b"\x00" * 32)
        assert len(sv.data) == 32

    def test_wrong_size(self):
        with pytest.raises(ValueError, match="32 bytes"):
            StateValue(b"\x00" * 31)

    def test_frozen(self):
        sv = StateValue(b"\x00" * 32)
        with pytest.raises(AttributeError):
            sv.data = b"\x01" * 32  # type: ignore[misc]

    def test_equality(self):
        a = StateValue(b"\xaa" * 32)
        b = StateValue(b"\xaa" * 32)
        c = StateValue(b"\xbb" * 32)
        assert a == b
        assert a != c

    def test_repr(self):
        sv = StateValue(b"\xab\xcd\xef\x01" + b"\x00" * 28)
        assert "abcdef01" in repr(sv)


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------

class TestBlock:
    def test_valid(self):
        b = Block(b"\x01" * 16)
        assert len(b.data) == 16

    def test_wrong_size(self):
        with pytest.raises(ValueError, match="16 bytes"):
            Block(b"\x00" * 15)

    def test_reverse(self):
        b = Block(bytes(range(16)))
        r = b.reverse()
        assert r.data == bytes(reversed(range(16)))

    def test_reverse_involution(self):
        b = Block(b"\xab\xcd" * 8)
        assert b.reverse().reverse() == b


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------

class TestFrame:
    def _make_frame(self, depth=4):
        blocks = tuple(Block(bytes([i]) * 16) for i in range(depth))
        return Frame(blocks)

    def test_valid(self):
        f = self._make_frame(2)
        assert f.depth == 2

    def test_below_min_depth(self):
        with pytest.raises(ValueError, match="depth"):
            Frame((Block(b"\x00" * 16),))  # depth=1, min is 2

    def test_depth_property(self):
        f = self._make_frame(4)
        assert f.depth == 4

    def test_mirror(self):
        f = self._make_frame(3)
        m = f.mirror()
        assert m.depth == 3
        # Mirror: reverse block order, reverse each block
        assert m.blocks[0] == f.blocks[2].reverse()
        assert m.blocks[1] == f.blocks[1].reverse()
        assert m.blocks[2] == f.blocks[0].reverse()

    def test_mirror_involution(self):
        f = self._make_frame(4)
        assert f.mirror().mirror() == f

    def test_to_bytes(self):
        f = self._make_frame(2)
        b = f.to_bytes()
        assert len(b) == 2 * BLOCK_SIZE

    def test_from_bytes_roundtrip(self):
        f = self._make_frame(4)
        b = f.to_bytes()
        f2 = Frame.from_bytes(b, 4)
        assert f == f2

    def test_from_bytes_wrong_size(self):
        with pytest.raises(ValueError):
            Frame.from_bytes(b"\x00" * 10, 4)

    def test_frozen(self):
        f = self._make_frame(2)
        with pytest.raises(AttributeError):
            f.blocks = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ChannelState
# ---------------------------------------------------------------------------

class TestChannelState:
    def test_valid(self):
        cs = ChannelState(local_state=StateValue(b"\x00" * 32), step=0)
        assert cs.step == 0

    def test_negative_step(self):
        with pytest.raises(ValueError, match="Step"):
            ChannelState(local_state=StateValue(b"\x00" * 32), step=-1)


# ---------------------------------------------------------------------------
# BilateralState
# ---------------------------------------------------------------------------

class TestBilateralState:
    def test_valid(self):
        bs = BilateralState(
            ratchet_state=StateValue(b"\x00" * 32),
            shared_prng_seed=StateValue(b"\x01" * 32),
            step=0,
        )
        assert bs.step == 0

    def test_negative_step(self):
        with pytest.raises(ValueError):
            BilateralState(
                ratchet_state=StateValue(b"\x00" * 32),
                shared_prng_seed=StateValue(b"\x01" * 32),
                step=-1,
            )


# ---------------------------------------------------------------------------
# ChannelId
# ---------------------------------------------------------------------------

class TestChannelId:
    def test_valid(self):
        ch = ChannelId(b"\x00" * 16)
        assert len(ch.value) == 16

    def test_wrong_size(self):
        with pytest.raises(ValueError, match="16 bytes"):
            ChannelId(b"\x00" * 15)


# ---------------------------------------------------------------------------
# AgentId
# ---------------------------------------------------------------------------

class TestAgentId:
    def test_ordering(self):
        a = AgentId(b"aaa")
        b = AgentId(b"bbb")
        assert a < b
        assert b > a
        assert a <= a
        assert a >= a

    def test_equality(self):
        a1 = AgentId(b"same")
        a2 = AgentId(b"same")
        assert a1 == a2


# ---------------------------------------------------------------------------
# MessageId
# ---------------------------------------------------------------------------

class TestMessageId:
    def test_valid(self):
        mid = MessageId(b"\x00" * 16)
        assert len(mid.value) == 16

    def test_wrong_size(self):
        with pytest.raises(ValueError, match="16 bytes"):
            MessageId(b"\x00" * 8)


# ---------------------------------------------------------------------------
# ProtocolMessage
# ---------------------------------------------------------------------------

class TestProtocolMessage:
    def _make_frame(self, depth=2):
        return Frame(tuple(Block(bytes([i]) * 16) for i in range(depth)))

    def test_to_bytes(self):
        f = self._make_frame()
        pm = ProtocolMessage(f, b"payload", f.mirror())
        raw = pm.to_bytes()
        assert len(raw) == 2 * 2 * BLOCK_SIZE + len(b"payload")

    def test_from_bytes_roundtrip(self):
        f = self._make_frame()
        pm = ProtocolMessage(f, b"test_payload", f.mirror())
        raw = pm.to_bytes()
        pm2 = ProtocolMessage.from_bytes(raw, 2)
        assert pm2.frame_open == f
        assert pm2.frame_close == f.mirror()
        assert pm2.encoded_payload == b"test_payload"

    def test_from_bytes_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            ProtocolMessage.from_bytes(b"\x00" * 10, 4)


# ---------------------------------------------------------------------------
# EnvelopeHeader
# ---------------------------------------------------------------------------

class TestEnvelopeHeader:
    def _make_header(self):
        return EnvelopeHeader(
            magic=PROTOCOL_MAGIC,
            version=PROTOCOL_VERSION,
            flags=EnvelopeFlags.NONE,
            frame_depth=4,
            payload_len=128,
            channel_id=ChannelId(b"\xab" * 16),
            step=42,
            sender_runtime=b"\xcd" * 16,
            reserved=b"\x00" * 8,
        )

    def test_to_bytes_size(self):
        h = self._make_header()
        assert len(h.to_bytes()) == ENVELOPE_HEADER_SIZE

    def test_roundtrip(self):
        h = self._make_header()
        raw = h.to_bytes()
        h2 = EnvelopeHeader.from_bytes(raw)
        assert h2.magic == PROTOCOL_MAGIC
        assert h2.version == PROTOCOL_VERSION
        assert h2.flags == EnvelopeFlags.NONE
        assert h2.frame_depth == 4
        assert h2.payload_len == 128
        assert h2.step == 42

    def test_wrong_size(self):
        with pytest.raises(ValueError):
            EnvelopeHeader.from_bytes(b"\x00" * 32)

    def test_flags(self):
        h = EnvelopeHeader(
            magic=PROTOCOL_MAGIC,
            version=PROTOCOL_VERSION,
            flags=EnvelopeFlags.ACK | EnvelopeFlags.RECOVERY,
            frame_depth=4,
            payload_len=0,
            channel_id=ChannelId(b"\x00" * 16),
            step=0,
            sender_runtime=b"\x00" * 16,
            reserved=b"\x00" * 8,
        )
        raw = h.to_bytes()
        h2 = EnvelopeHeader.from_bytes(raw)
        assert h2.flags & EnvelopeFlags.ACK
        assert h2.flags & EnvelopeFlags.RECOVERY


# ---------------------------------------------------------------------------
# Channel (mutable)
# ---------------------------------------------------------------------------

class TestChannel:
    def test_below_min_depth(self):
        with pytest.raises(ValueError, match="depth"):
            Channel(
                channel_id=ChannelId(b"\x00" * 16),
                agent_a=AgentId(b"a"),
                agent_b=AgentId(b"b"),
                state=ChannelState(StateValue(b"\x00" * 32), 0),
                depth=1,
                status=ChannelStatus.ACTIVE,
            )

    def test_mutable(self):
        ch = Channel(
            channel_id=ChannelId(b"\x00" * 16),
            agent_a=AgentId(b"a"),
            agent_b=AgentId(b"b"),
            state=ChannelState(StateValue(b"\x00" * 32), 0),
            depth=2,
            status=ChannelStatus.ACTIVE,
        )
        ch.status = ChannelStatus.QUARANTINED
        assert ch.status == ChannelStatus.QUARANTINED


# ---------------------------------------------------------------------------
# Error Hierarchy
# ---------------------------------------------------------------------------

class TestErrors:
    def test_hierarchy(self):
        assert issubclass(AgentError, MFPError)
        assert issubclass(ValidationError, MFPError)
        assert issubclass(FrameValidationError, ValidationError)
        assert issubclass(DecodeError, ValidationError)
        assert issubclass(InfrastructureError, MFPError)
        assert issubclass(CSPRNGError, InfrastructureError)
        assert issubclass(StateCorruptionError, InfrastructureError)

    def test_agent_error_code(self):
        err = AgentError(AgentErrorCode.UNBOUND, "not bound")
        assert err.code == AgentErrorCode.UNBOUND
        assert "not bound" in str(err)
