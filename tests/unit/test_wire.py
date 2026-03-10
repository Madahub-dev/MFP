"""Unit tests for mfp/federation/wire.py (I-16)."""

import pytest

from mfp.core.types import (
    BLOCK_SIZE,
    ENVELOPE_HEADER_SIZE,
    MIN_FRAME_DEPTH,
    PROTOCOL_MAGIC,
    PROTOCOL_VERSION,
    Block,
    ChannelId,
    EnvelopeError,
    EnvelopeFlags,
    EnvelopeHeader,
    Frame,
    ProtocolMessage,
)
from mfp.federation.wire import (
    assemble_wire_message,
    build_envelope_header,
    compute_body_size,
    parse_wire_message,
    validate_envelope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel_id(n: int = 1) -> ChannelId:
    return ChannelId(bytes([n]) * 16)


def _sender(n: int = 1) -> bytes:
    return bytes([n]) * 16


def _frame(depth: int = 2) -> Frame:
    return Frame(tuple(Block(bytes([i + 1]) * 16) for i in range(depth)))


def _protocol_msg(depth: int = 2, payload: bytes = b"hello") -> ProtocolMessage:
    f = _frame(depth)
    return ProtocolMessage(frame_open=f, encoded_payload=payload, frame_close=f.mirror())


def _header(depth: int = 2, payload_len: int = 5) -> EnvelopeHeader:
    return build_envelope_header(
        channel_id=_channel_id(),
        step=1,
        frame_depth=depth,
        payload_len=payload_len,
        sender_runtime=_sender(),
    )


# ---------------------------------------------------------------------------
# build_envelope_header
# ---------------------------------------------------------------------------

class TestBuildEnvelopeHeader:
    def test_magic_and_version(self):
        h = _header()
        assert h.magic == PROTOCOL_MAGIC
        assert h.version == PROTOCOL_VERSION

    def test_flags_default_none(self):
        h = _header()
        assert h.flags == EnvelopeFlags.NONE

    def test_flags_explicit(self):
        h = build_envelope_header(
            channel_id=_channel_id(),
            step=0,
            frame_depth=2,
            payload_len=0,
            sender_runtime=_sender(),
            flags=EnvelopeFlags.ACK,
        )
        assert h.flags == EnvelopeFlags.ACK

    def test_sender_truncated_to_16(self):
        long_sender = b"\xff" * 64
        h = build_envelope_header(
            channel_id=_channel_id(),
            step=0,
            frame_depth=2,
            payload_len=0,
            sender_runtime=long_sender,
        )
        assert len(h.sender_runtime) == 16
        assert h.sender_runtime == b"\xff" * 16

    def test_sender_padded_if_short(self):
        short_sender = b"\xaa" * 4
        h = build_envelope_header(
            channel_id=_channel_id(),
            step=0,
            frame_depth=2,
            payload_len=0,
            sender_runtime=short_sender,
        )
        assert len(h.sender_runtime) == 16
        assert h.sender_runtime == b"\xaa" * 4 + b"\x00" * 12

    def test_reserved_is_zeroed(self):
        h = _header()
        assert h.reserved == b"\x00" * 8


# ---------------------------------------------------------------------------
# Serialization roundtrip
# ---------------------------------------------------------------------------

class TestEnvelopeRoundtrip:
    def test_header_roundtrip(self):
        h = _header()
        data = h.to_bytes()
        assert len(data) == ENVELOPE_HEADER_SIZE
        h2 = EnvelopeHeader.from_bytes(data)
        assert h2.magic == h.magic
        assert h2.version == h.version
        assert h2.flags == h.flags
        assert h2.frame_depth == h.frame_depth
        assert h2.payload_len == h.payload_len
        assert h2.channel_id == h.channel_id
        assert h2.step == h.step
        assert h2.sender_runtime == h.sender_runtime
        assert h2.reserved == h.reserved


# ---------------------------------------------------------------------------
# assemble / parse wire messages
# ---------------------------------------------------------------------------

class TestWireMessageRoundtrip:
    def test_roundtrip(self):
        h = _header(depth=2, payload_len=5)
        msg = _protocol_msg(depth=2, payload=b"hello")
        wire_data = assemble_wire_message(h, msg)
        h2, msg2 = parse_wire_message(wire_data)
        assert h2.step == h.step
        assert msg2.encoded_payload == b"hello"
        assert msg2.frame_open.depth == 2

    def test_empty_payload(self):
        h = _header(depth=2, payload_len=0)
        msg = _protocol_msg(depth=2, payload=b"")
        wire_data = assemble_wire_message(h, msg)
        h2, msg2 = parse_wire_message(wire_data)
        assert msg2.encoded_payload == b""

    def test_large_payload(self):
        payload = b"\xab" * 1024
        h = _header(depth=3, payload_len=len(payload))
        msg = _protocol_msg(depth=3, payload=payload)
        wire_data = assemble_wire_message(h, msg)
        _, msg2 = parse_wire_message(wire_data)
        assert msg2.encoded_payload == payload

    def test_parse_too_short_raises(self):
        with pytest.raises(EnvelopeError, match="too short"):
            parse_wire_message(b"\x00" * 10)

    def test_parse_body_size_mismatch_raises(self):
        h = _header(depth=2, payload_len=100)
        # Only append a small body
        data = h.to_bytes() + b"\x00" * 10
        with pytest.raises(EnvelopeError, match="Body size mismatch"):
            parse_wire_message(data)


# ---------------------------------------------------------------------------
# validate_envelope
# ---------------------------------------------------------------------------

class TestValidateEnvelope:
    def test_valid_envelope_no_errors(self):
        h = _header()
        errors = validate_envelope(h)
        assert errors == []

    def test_bad_magic(self):
        h = EnvelopeHeader(
            magic=b"BAD!",
            version=PROTOCOL_VERSION,
            flags=EnvelopeFlags.NONE,
            frame_depth=2,
            payload_len=0,
            channel_id=_channel_id(),
            step=0,
            sender_runtime=_sender(),
            reserved=b"\x00" * 8,
        )
        errors = validate_envelope(h)
        assert any("magic" in e.lower() for e in errors)

    def test_bad_version(self):
        h = EnvelopeHeader(
            magic=PROTOCOL_MAGIC,
            version=0xFFFF,
            flags=EnvelopeFlags.NONE,
            frame_depth=2,
            payload_len=0,
            channel_id=_channel_id(),
            step=0,
            sender_runtime=_sender(),
            reserved=b"\x00" * 8,
        )
        errors = validate_envelope(h)
        assert any("version" in e.lower() for e in errors)

    def test_nonzero_reserved(self):
        h = EnvelopeHeader(
            magic=PROTOCOL_MAGIC,
            version=PROTOCOL_VERSION,
            flags=EnvelopeFlags.NONE,
            frame_depth=2,
            payload_len=0,
            channel_id=_channel_id(),
            step=0,
            sender_runtime=_sender(),
            reserved=b"\xff" * 8,
        )
        errors = validate_envelope(h)
        assert any("reserved" in e.lower() for e in errors)

    def test_frame_depth_below_minimum(self):
        h = EnvelopeHeader(
            magic=PROTOCOL_MAGIC,
            version=PROTOCOL_VERSION,
            flags=EnvelopeFlags.NONE,
            frame_depth=1,
            payload_len=0,
            channel_id=_channel_id(),
            step=0,
            sender_runtime=_sender(),
            reserved=b"\x00" * 8,
        )
        errors = validate_envelope(h)
        assert any("depth" in e.lower() for e in errors)

    def test_unknown_channel(self):
        h = _header()
        known = {b"\xff" * 16}
        errors = validate_envelope(h, known_channels=known)
        assert any("channel" in e.lower() for e in errors)

    def test_known_channel_passes(self):
        h = _header()
        known = {h.channel_id.value}
        errors = validate_envelope(h, known_channels=known)
        assert errors == []

    def test_sender_mismatch(self):
        h = _header()
        errors = validate_envelope(h, expected_peer=b"\xee" * 16)
        assert any("sender" in e.lower() for e in errors)

    def test_sender_match(self):
        h = _header()
        errors = validate_envelope(h, expected_peer=_sender())
        assert errors == []

    def test_multiple_errors(self):
        h = EnvelopeHeader(
            magic=b"XXXX",
            version=0xFFFF,
            flags=EnvelopeFlags.NONE,
            frame_depth=1,
            payload_len=0,
            channel_id=_channel_id(),
            step=0,
            sender_runtime=_sender(),
            reserved=b"\xff" * 8,
        )
        errors = validate_envelope(h)
        assert len(errors) >= 3


# ---------------------------------------------------------------------------
# compute_body_size
# ---------------------------------------------------------------------------

class TestComputeBodySize:
    def test_basic(self):
        assert compute_body_size(2, 10) == 2 * 2 * BLOCK_SIZE + 10

    def test_zero_payload(self):
        assert compute_body_size(4, 0) == 2 * 4 * BLOCK_SIZE

    def test_matches_wire_format(self):
        depth, plen = 3, 100
        h = _header(depth=depth, payload_len=plen)
        msg = _protocol_msg(depth=depth, payload=b"\x00" * plen)
        wire = assemble_wire_message(h, msg)
        body = wire[ENVELOPE_HEADER_SIZE:]
        assert len(body) == compute_body_size(depth, plen)
