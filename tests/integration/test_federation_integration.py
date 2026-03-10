"""Integration tests for Phase 5 — Federation modules working together.

Tests cross-module interactions:
- bilateral bootstrap → wire format assembly → parse roundtrip
- bilateral state → recovery negotiation
- wire format → transport I/O
- recovery lifecycle across bilateral channels
"""

import asyncio

import pytest

from mfp.core.types import (
    BilateralState,
    Block,
    ChannelId,
    EnvelopeFlags,
    Frame,
    RecoveryMessage,
    RuntimeId,
    StateValue,
)
from mfp.federation.bilateral import (
    BilateralChannel,
    advance_bilateral_state,
    bootstrap_ceremonial,
    bootstrap_deterministic,
    compute_shared_secret,
    derive_bilateral_id,
    generate_dh_keypair,
)
from mfp.federation.recovery import (
    Diagnosis,
    RecoveryConfig,
    RecoveryPhase,
    begin_recovery,
    build_recovery_message,
    complete_resync,
    compute_state_hash,
    process_negotiation,
)
from mfp.federation.transport import (
    TransportClient,
    TransportConfig,
    TransportServer,
    read_message,
    write_message,
)
from mfp.federation.wire import (
    assemble_wire_message,
    build_envelope_header,
    parse_wire_message,
    validate_envelope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runtime_id(n: int) -> RuntimeId:
    return RuntimeId(value=StateValue(bytes([n]) * 32))


def _channel_id(n: int = 1) -> ChannelId:
    return ChannelId(bytes([n]) * 16)


def _frame(depth: int = 2) -> Frame:
    return Frame(tuple(Block(bytes([i + 1]) * 16) for i in range(depth)))


def _protocol_msg(depth: int = 2, payload: bytes = b"integration-test"):
    from mfp.core.types import ProtocolMessage
    f = _frame(depth)
    return ProtocolMessage(frame_open=f, encoded_payload=payload, frame_close=f.mirror())


# ---------------------------------------------------------------------------
# Bilateral → Wire roundtrip
# ---------------------------------------------------------------------------

class TestBilateralWireIntegration:
    def test_bootstrap_then_wire_roundtrip(self):
        """Bootstrap bilateral state, build wire message, parse back."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state = bootstrap_deterministic(r_a, r_b)

        header = build_envelope_header(
            channel_id=_channel_id(),
            step=state.step,
            frame_depth=2,
            payload_len=16,
            sender_runtime=r_a.value.data[:16],
        )
        msg = _protocol_msg(payload=b"bilateral-hello!")

        wire_data = assemble_wire_message(header, msg)
        h2, msg2 = parse_wire_message(wire_data)

        assert h2.step == 0
        assert msg2.encoded_payload == b"bilateral-hello!"

    def test_advance_then_send(self):
        """Advance bilateral state, then send at new step."""
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        state = advance_bilateral_state(state, _frame())

        header = build_envelope_header(
            channel_id=_channel_id(),
            step=state.step,
            frame_depth=2,
            payload_len=4,
            sender_runtime=_runtime_id(1).value.data[:16],
        )
        msg = _protocol_msg(payload=b"test")

        wire_data = assemble_wire_message(header, msg)
        h2, _ = parse_wire_message(wire_data)
        assert h2.step == 1

    def test_validate_after_bilateral_setup(self):
        """Validate envelope against known channels and sender."""
        r_a = _runtime_id(1)
        ch = _channel_id()
        header = build_envelope_header(
            channel_id=ch,
            step=0,
            frame_depth=2,
            payload_len=0,
            sender_runtime=r_a.value.data,
        )
        errors = validate_envelope(
            header,
            known_channels={ch.value},
            expected_peer=r_a.value.data,
        )
        assert errors == []

    def test_ceremonial_bootstrap_then_wire(self):
        """Ceremonial (DH) bootstrap → wire message roundtrip."""
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret = compute_shared_secret(priv_a, pub_b)

        r_a, r_b = _runtime_id(10), _runtime_id(20)
        state = bootstrap_ceremonial(r_a, r_b, secret)

        header = build_envelope_header(
            channel_id=_channel_id(5),
            step=state.step,
            frame_depth=3,
            payload_len=8,
            sender_runtime=r_a.value.data,
        )
        msg = _protocol_msg(depth=3, payload=b"dh-hello")
        wire_data = assemble_wire_message(header, msg)
        h2, msg2 = parse_wire_message(wire_data)
        assert msg2.encoded_payload == b"dh-hello"


# ---------------------------------------------------------------------------
# Bilateral → Recovery integration
# ---------------------------------------------------------------------------

class TestBilateralRecoveryIntegration:
    def test_synchronized_runtimes_spurious(self):
        """Two runtimes at same state → recovery diagnosis: SPURIOUS."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state_a = bootstrap_deterministic(r_a, r_b)
        state_b = bootstrap_deterministic(r_b, r_a)  # same thing

        msg_b = build_recovery_message(_channel_id(), state_b)
        rs = begin_recovery(derive_bilateral_id(r_a, r_b), state_a.step)
        rs = process_negotiation(rs, msg_b, state_a, RecoveryConfig())

        assert rs.diagnosis == Diagnosis.SPURIOUS
        assert rs.phase == RecoveryPhase.COMPLETE

    def test_diverged_runtimes_local_behind(self):
        """One runtime advanced, other didn't → LOCAL_BEHIND."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state_a = bootstrap_deterministic(r_a, r_b)
        state_b = bootstrap_deterministic(r_b, r_a)

        # Advance B by 3 steps
        for _ in range(3):
            state_b = advance_bilateral_state(state_b, _frame())

        msg_b = build_recovery_message(_channel_id(), state_b)
        rs = begin_recovery(derive_bilateral_id(r_a, r_b), state_a.step)
        rs = process_negotiation(rs, msg_b, state_a, RecoveryConfig(max_step_gap=10))

        assert rs.diagnosis == Diagnosis.LOCAL_BEHIND
        assert rs.phase == RecoveryPhase.RESYNC

    def test_diverged_too_far_escalates(self):
        """Gap exceeds max_step_gap → ESCALATE."""
        state_a = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        state_b = bootstrap_deterministic(_runtime_id(2), _runtime_id(1))

        for _ in range(10):
            state_b = advance_bilateral_state(state_b, _frame())

        msg_b = build_recovery_message(_channel_id(), state_b)
        rs = begin_recovery(b"\x00" * 32, state_a.step)
        rs = process_negotiation(rs, msg_b, state_a, RecoveryConfig(max_step_gap=5))

        assert rs.diagnosis == Diagnosis.ESCALATE
        assert rs.phase == RecoveryPhase.ESCALATED

    def test_full_recovery_lifecycle(self):
        """DETECT → NEGOTIATE → RESYNC → COMPLETE."""
        state_a = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        state_b = bootstrap_deterministic(_runtime_id(2), _runtime_id(1))

        # B advances by 2
        for _ in range(2):
            state_b = advance_bilateral_state(state_b, _frame())

        rs = begin_recovery(b"\x00" * 32, state_a.step)
        assert rs.phase == RecoveryPhase.DETECT

        msg_b = build_recovery_message(_channel_id(), state_b)
        rs = process_negotiation(rs, msg_b, state_a, RecoveryConfig(max_step_gap=10))
        assert rs.phase == RecoveryPhase.RESYNC

        # Simulate successful resync
        rs = complete_resync(rs)
        assert rs.phase == RecoveryPhase.COMPLETE


# ---------------------------------------------------------------------------
# Wire → Transport I/O integration
# ---------------------------------------------------------------------------

class TestWireTransportIntegration:
    @pytest.mark.asyncio
    async def test_wire_through_transport_roundtrip(self):
        """Assemble wire message → transport write → transport read → parse."""
        header = build_envelope_header(
            channel_id=_channel_id(),
            step=42,
            frame_depth=2,
            payload_len=11,
            sender_runtime=b"\xbb" * 16,
        )
        msg = _protocol_msg(payload=b"transported")

        # Write via transport into mock stream
        reader = asyncio.StreamReader()
        mock_transport = _MockTransport()
        protocol = asyncio.StreamReaderProtocol(reader)
        protocol.connection_made(mock_transport)
        writer = asyncio.StreamWriter(
            mock_transport, protocol, reader, asyncio.get_event_loop()
        )

        await write_message(writer, header, msg, timeout=5.0)

        # Read back
        parse_reader = asyncio.StreamReader()
        parse_reader.feed_data(mock_transport.written)
        parse_reader.feed_eof()

        h2, msg2 = await read_message(parse_reader, timeout=5.0)
        assert h2.step == 42
        assert msg2.encoded_payload == b"transported"

        # Validate the received header
        errors = validate_envelope(h2)
        assert errors == []

    @pytest.mark.asyncio
    async def test_tcp_server_client_roundtrip(self):
        """Full TCP server/client message exchange."""
        received = []

        async def handler(header, msg):
            received.append((header, msg))

        config = TransportConfig(host="127.0.0.1", port=19878)
        server = TransportServer(config, handler)
        await server.start()

        try:
            client = TransportClient("127.0.0.1", 19878, config)
            await client.connect()

            header = build_envelope_header(
                channel_id=_channel_id(),
                step=7,
                frame_depth=2,
                payload_len=6,
                sender_runtime=b"\xcc" * 16,
            )
            msg = _protocol_msg(payload=b"tcp-hi")
            await client.send(header, msg)

            # Give server time to process
            await asyncio.sleep(0.1)

            assert len(received) == 1
            h_rcv, msg_rcv = received[0]
            assert h_rcv.step == 7
            assert msg_rcv.encoded_payload == b"tcp-hi"

            await client.close()
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# BilateralChannel data structure integration
# ---------------------------------------------------------------------------

class TestBilateralChannelIntegration:
    def test_create_and_derive_id(self):
        """BilateralChannel creation uses derive_bilateral_id."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        bid = derive_bilateral_id(r_a, r_b)
        state = bootstrap_deterministic(r_a, r_b)

        bc = BilateralChannel(
            bilateral_id=bid,
            local_runtime=r_a,
            peer_runtime=r_b,
            state=state,
        )
        # Verify ID is same both ways
        assert bc.bilateral_id == derive_bilateral_id(r_b, r_a)

    def test_advance_bilateral_channel(self):
        """Advancing state through a BilateralChannel."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state = bootstrap_deterministic(r_a, r_b)
        bc = BilateralChannel(
            bilateral_id=derive_bilateral_id(r_a, r_b),
            local_runtime=r_a,
            peer_runtime=r_b,
            state=state,
        )

        frame = _frame()
        bc.state = advance_bilateral_state(bc.state, frame)
        assert bc.state.step == 1


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class _MockTransport(asyncio.Transport):
    def __init__(self):
        super().__init__()
        self.written = b""
        self._closing = False

    def write(self, data: bytes) -> None:
        self.written += data

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    def get_extra_info(self, name, default=None):
        return default
