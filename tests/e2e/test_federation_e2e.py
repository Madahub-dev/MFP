"""End-to-end tests for Phase 5 — Federation full lifecycle.

Tests the complete federation workflow:
1. Two runtimes bootstrap bilateral state
2. Exchange messages via wire format over TCP
3. Advance bilateral state
4. Detect divergence and run recovery protocol
"""

import asyncio

import pytest

from mfp.core.types import (
    Block,
    ChannelId,
    EnvelopeFlags,
    Frame,
    ProtocolMessage,
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


def _protocol_msg(depth: int = 2, payload: bytes = b"e2e-payload") -> ProtocolMessage:
    f = _frame(depth)
    return ProtocolMessage(frame_open=f, encoded_payload=payload, frame_close=f.mirror())


# ---------------------------------------------------------------------------
# E2E: Deterministic bootstrap → message exchange → state advance
# ---------------------------------------------------------------------------

class TestDeterministicFederationE2E:
    def test_full_lifecycle(self):
        """Bootstrap → build wire message → parse → validate → advance."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)

        # 1. Bootstrap — both runtimes independently
        state_a = bootstrap_deterministic(r_a, r_b)
        state_b = bootstrap_deterministic(r_b, r_a)
        assert state_a == state_b

        bid = derive_bilateral_id(r_a, r_b)

        # 2. Runtime A sends a message
        payload = b"hello from A"
        header_a = build_envelope_header(
            channel_id=_channel_id(),
            step=state_a.step,
            frame_depth=2,
            payload_len=len(payload),
            sender_runtime=r_a.value.data,
        )
        msg_a = _protocol_msg(payload=payload)
        wire_data = assemble_wire_message(header_a, msg_a)

        # 3. Runtime B receives and validates
        h_recv, msg_recv = parse_wire_message(wire_data)
        errors = validate_envelope(
            h_recv,
            known_channels={_channel_id().value},
            expected_peer=r_a.value.data[:16],
        )
        assert errors == []
        assert msg_recv.encoded_payload == b"hello from A"

        # 4. Both runtimes advance state
        frame = _frame()
        state_a = advance_bilateral_state(state_a, frame)
        state_b = advance_bilateral_state(state_b, frame)
        assert state_a == state_b
        assert state_a.step == 1

        # 5. Second exchange at step 1
        header_b = build_envelope_header(
            channel_id=_channel_id(),
            step=state_b.step,
            frame_depth=2,
            payload_len=12,
            sender_runtime=r_b.value.data,
        )
        msg_b = _protocol_msg(payload=b"hello from B")
        wire_data_2 = assemble_wire_message(header_b, msg_b)
        h2, m2 = parse_wire_message(wire_data_2)
        assert h2.step == 1
        assert m2.encoded_payload == b"hello from B"


# ---------------------------------------------------------------------------
# E2E: Ceremonial (DH) bootstrap
# ---------------------------------------------------------------------------

class TestCeremonialFederationE2E:
    def test_dh_bootstrap_and_exchange(self):
        """Full DH key exchange → bootstrap → message → advance."""
        r_a, r_b = _runtime_id(10), _runtime_id(20)

        # 1. DH ceremony
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret_a = compute_shared_secret(priv_a, pub_b)
        secret_b = compute_shared_secret(priv_b, pub_a)
        assert secret_a == secret_b

        # 2. Bootstrap
        state_a = bootstrap_ceremonial(r_a, r_b, secret_a)
        state_b = bootstrap_ceremonial(r_b, r_a, secret_b)
        assert state_a == state_b

        # 3. Exchange
        payload = b"dh-encrypted"
        header = build_envelope_header(
            channel_id=_channel_id(5),
            step=0,
            frame_depth=3,
            payload_len=len(payload),
            sender_runtime=r_a.value.data,
        )
        msg = _protocol_msg(depth=3, payload=payload)
        wire = assemble_wire_message(header, msg)
        h, m = parse_wire_message(wire)
        assert m.encoded_payload == payload

        # 4. Advance
        frame = _frame(3)
        state_a = advance_bilateral_state(state_a, frame)
        state_b = advance_bilateral_state(state_b, frame)
        assert state_a == state_b
        assert state_a.step == 1


# ---------------------------------------------------------------------------
# E2E: Recovery after divergence
# ---------------------------------------------------------------------------

class TestRecoveryE2E:
    def test_detect_negotiate_resync_complete(self):
        """Full recovery protocol: DETECT → NEGOTIATE → RESYNC → COMPLETE."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state_a = bootstrap_deterministic(r_a, r_b)
        state_b = bootstrap_deterministic(r_b, r_a)

        # Simulate divergence: B advances by 3, A stays at 0
        for _ in range(3):
            state_b = advance_bilateral_state(state_b, _frame())

        bid = derive_bilateral_id(r_a, r_b)
        config = RecoveryConfig(max_step_gap=10, max_attempts=3)

        # Phase 1: DETECT
        rs_a = begin_recovery(bid, state_a.step)
        assert rs_a.phase == RecoveryPhase.DETECT

        # Phase 2: NEGOTIATE — A receives B's recovery message
        msg_b = build_recovery_message(_channel_id(), state_b)
        rs_a = process_negotiation(rs_a, msg_b, state_a, config)
        assert rs_a.diagnosis == Diagnosis.LOCAL_BEHIND
        assert rs_a.phase == RecoveryPhase.RESYNC

        # Phase 3: RESYNC — A catches up (simulated)
        for _ in range(3):
            state_a = advance_bilateral_state(state_a, _frame())
        assert state_a == state_b

        rs_a = complete_resync(rs_a)
        assert rs_a.phase == RecoveryPhase.COMPLETE

    def test_recovery_escalation(self):
        """Recovery with corruption → immediate escalation."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state_a = bootstrap_deterministic(r_a, r_b)

        # Simulate corruption: same step but different state
        from mfp.core.types import RecoveryMessage
        fake_hash = StateValue(b"\xff" * 32)
        msg_corrupt = RecoveryMessage(
            channel_id=_channel_id(),
            step=0,  # same as A
            state_hash=fake_hash,
        )

        rs = begin_recovery(derive_bilateral_id(r_a, r_b), 0)
        rs = process_negotiation(rs, msg_corrupt, state_a, RecoveryConfig())

        assert rs.diagnosis == Diagnosis.CORRUPTION
        assert rs.phase == RecoveryPhase.ESCALATED


# ---------------------------------------------------------------------------
# E2E: TCP transport full exchange
# ---------------------------------------------------------------------------

class TestTCPFederationE2E:
    @pytest.mark.asyncio
    async def test_bilateral_over_tcp(self):
        """Full: bootstrap → wire build → TCP send → receive → validate → advance."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        state_a = bootstrap_deterministic(r_a, r_b)
        state_b = bootstrap_deterministic(r_b, r_a)

        received_messages = []

        async def b_handler(header, msg):
            errors = validate_envelope(
                header,
                known_channels={_channel_id().value},
                expected_peer=r_a.value.data[:16],
            )
            assert errors == [], f"Validation errors: {errors}"
            received_messages.append((header, msg))

        config = TransportConfig(host="127.0.0.1", port=19879)
        server_b = TransportServer(config, b_handler)
        await server_b.start()

        try:
            client_a = TransportClient("127.0.0.1", 19879, config)
            await client_a.connect()

            # A sends to B
            payload = b"e2e-bilateral"
            header = build_envelope_header(
                channel_id=_channel_id(),
                step=state_a.step,
                frame_depth=2,
                payload_len=len(payload),
                sender_runtime=r_a.value.data,
            )
            msg = _protocol_msg(payload=payload)
            await client_a.send(header, msg)

            await asyncio.sleep(0.1)

            assert len(received_messages) == 1
            h_rcv, m_rcv = received_messages[0]
            assert m_rcv.encoded_payload == b"e2e-bilateral"
            assert h_rcv.step == 0

            # Both advance
            frame = _frame()
            state_a = advance_bilateral_state(state_a, frame)
            state_b = advance_bilateral_state(state_b, frame)
            assert state_a == state_b

            await client_a.close()
        finally:
            await server_b.stop()

    @pytest.mark.asyncio
    async def test_multiple_messages_over_tcp(self):
        """Send multiple messages, verify all received in order."""
        received = []

        async def handler(header, msg):
            received.append(msg.encoded_payload)

        config = TransportConfig(host="127.0.0.1", port=19880)
        server = TransportServer(config, handler)
        await server.start()

        try:
            client = TransportClient("127.0.0.1", 19880, config)
            await client.connect()

            for i in range(5):
                payload = f"msg-{i}".encode()
                header = build_envelope_header(
                    channel_id=_channel_id(),
                    step=i,
                    frame_depth=2,
                    payload_len=len(payload),
                    sender_runtime=b"\xdd" * 16,
                )
                msg = _protocol_msg(payload=payload)
                await client.send(header, msg)

            await asyncio.sleep(0.2)

            assert len(received) == 5
            for i in range(5):
                assert received[i] == f"msg-{i}".encode()

            await client.close()
        finally:
            await server.stop()
