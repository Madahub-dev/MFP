"""End-to-end tests simulating full runtime message lifecycles.

These tests simulate the complete message lifecycle as it would be
executed by the runtime (Phase 2), using only Phase 1 core functions.
"""

from mfp.core.encoding import build_encoding_context, decode, encode
from mfp.core.frame import assemble_message, sample_frame, validate_frame
from mfp.core.primitives import random_id, random_state_value
from mfp.core.ratchet import advance, compose, compose_ordered, seed
from mfp.core.types import (
    AgentId,
    ChannelId,
    ChannelState,
    DeliveredMessage,
    MessageId,
    ProtocolMessage,
    Receipt,
)


class SimulatedRuntime:
    """Minimal runtime simulation for e2e testing.

    Manages channels, Sg, and the message pipeline using core functions.
    """

    def __init__(self):
        self.identity = random_state_value()
        self.channels: dict[bytes, dict] = {}  # channel_id.value -> channel data
        self._sg = None

    def establish_channel(self, agent_a: AgentId, agent_b: AgentId, depth: int = 4):
        ch_id = ChannelId(random_id(16))
        sl0 = seed(self.identity, agent_a, agent_b, ch_id)
        self.channels[ch_id.value] = {
            "channel_id": ch_id,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "local_state": sl0,
            "step": 0,
            "depth": depth,
        }
        self._recompute_sg()
        return ch_id

    def _recompute_sg(self):
        pairs = [
            (ch["channel_id"], ch["local_state"])
            for ch in self.channels.values()
        ]
        if pairs:
            self._sg = compose_ordered(pairs)

    def send(self, channel_id: ChannelId, payload: bytes) -> tuple[Receipt, ProtocolMessage]:
        ch = self.channels[channel_id.value]
        sl = ch["local_state"]
        t = ch["step"]
        sg = self._sg.value

        # FRAME
        frame = sample_frame(sl, t, sg, depth=ch["depth"])

        # ENCODE
        ctx = build_encoding_context(sl, channel_id, t)
        encoded = encode(payload, ctx)

        # ASSEMBLE
        msg = assemble_message(frame, encoded)

        # VALIDATE (self-check)
        assert validate_frame(msg.frame_open, msg.frame_close, frame)

        # DECODE (verify roundtrip)
        decoded = decode(msg.encoded_payload, ctx)
        assert decoded == payload

        # ADVANCE
        sl_new = advance(sl, frame)
        ch["local_state"] = sl_new
        ch["step"] = t + 1
        self._recompute_sg()

        receipt = Receipt(
            message_id=MessageId(random_id(16)),
            channel=channel_id,
            step=t,
        )
        return receipt, msg

    def receive(self, channel_id: ChannelId, msg: ProtocolMessage) -> DeliveredMessage:
        """Receive side — in single runtime, uses same state as send."""
        ch = self.channels[channel_id.value]
        # In single-runtime, the runtime already validated during send.
        # This simulates the DELIVER stage.
        sl = ch["local_state"]
        # Decode uses the state BEFORE the advancement that send() already did.
        # In a real runtime, decode happens before advance.
        # For this simulation, we just verify the message structure is valid.
        return DeliveredMessage(
            payload=msg.encoded_payload,  # In real runtime, this would be decoded
            sender=ch["agent_a"],
            channel=channel_id,
            message_id=MessageId(random_id(16)),
        )


class TestSingleRuntimeE2E:
    """Full single-runtime lifecycle simulation."""

    def test_two_agents_conversation(self):
        runtime = SimulatedRuntime()

        alice = AgentId(b"alice___________")
        bob = AgentId(b"bob_____________")

        ch = runtime.establish_channel(alice, bob)

        conversation = [
            b"Alice: Hello Bob, how are you?",
            b"Bob: I'm doing well, thanks!",
            b"Alice: Let's discuss the transfer.",
            b"Bob: Sure, what amount?",
            b"Alice: 100 units from account X.",
            b"Bob: Transfer confirmed.",
        ]

        for i, payload in enumerate(conversation):
            receipt, msg = runtime.send(ch, payload)
            assert receipt.step == i
            assert receipt.channel == ch

    def test_three_agents_star_topology(self):
        """Hub agent communicating with two spokes."""
        runtime = SimulatedRuntime()

        hub = AgentId(b"hub_____________")
        spoke_a = AgentId(b"spoke_a_________")
        spoke_b = AgentId(b"spoke_b_________")

        ch_a = runtime.establish_channel(hub, spoke_a)
        ch_b = runtime.establish_channel(hub, spoke_b)

        # Send on both channels interleaved
        runtime.send(ch_a, b"Hub to Spoke A: task 1")
        runtime.send(ch_b, b"Hub to Spoke B: task 1")
        runtime.send(ch_a, b"Hub to Spoke A: task 2")
        runtime.send(ch_b, b"Hub to Spoke B: task 2")

        # Verify state diverged
        assert (
            runtime.channels[ch_a.value]["local_state"]
            != runtime.channels[ch_b.value]["local_state"]
        )

    def test_many_channels(self):
        """Runtime with many concurrent channels."""
        runtime = SimulatedRuntime()

        agents = [AgentId(f"agent_{i:03d}_______".encode()) for i in range(10)]
        channels = []

        # Establish channels between consecutive agents
        for i in range(len(agents) - 1):
            ch = runtime.establish_channel(agents[i], agents[i + 1])
            channels.append(ch)

        # Send one message on each channel
        for i, ch in enumerate(channels):
            receipt, _ = runtime.send(ch, f"Message on channel {i}".encode())
            assert receipt.step == 0

    def test_high_step_count(self):
        """100 exchanges on a single channel."""
        runtime = SimulatedRuntime()

        a = AgentId(b"agent_a_________")
        b = AgentId(b"agent_b_________")
        ch = runtime.establish_channel(a, b)

        for t in range(100):
            receipt, _ = runtime.send(ch, f"Step {t}".encode())
            assert receipt.step == t

    def test_sg_changes_across_channels(self):
        """Sg changes when any channel advances, affecting all channels."""
        runtime = SimulatedRuntime()

        a1 = AgentId(b"agent_001_______")
        a2 = AgentId(b"agent_002_______")
        a3 = AgentId(b"agent_003_______")

        ch1 = runtime.establish_channel(a1, a2)
        ch2 = runtime.establish_channel(a1, a3)

        sg_before = runtime._sg

        # Advance ch1
        runtime.send(ch1, b"hello")

        sg_after = runtime._sg

        # Sg should have changed
        assert sg_before != sg_after

    def test_empty_payload(self):
        runtime = SimulatedRuntime()
        a = AgentId(b"agent_a_________")
        b = AgentId(b"agent_b_________")
        ch = runtime.establish_channel(a, b)
        receipt, _ = runtime.send(ch, b"")
        assert receipt.step == 0

    def test_large_payload(self):
        runtime = SimulatedRuntime()
        a = AgentId(b"agent_a_________")
        b = AgentId(b"agent_b_________")
        ch = runtime.establish_channel(a, b)
        large = b"X" * 100_000
        receipt, _ = runtime.send(ch, large)
        assert receipt.step == 0


class TestCrossRuntimeE2E:
    """Simulated cross-runtime message exchange."""

    def test_bilateral_conversation(self):
        from mfp.core.frame import sample_frame_cross_runtime
        from mfp.core.ratchet import bilateral_advance, bilateral_seed
        from mfp.core.types import RuntimeId

        rt_a_id = RuntimeId(value=random_state_value())
        rt_b_id = RuntimeId(value=random_state_value())

        # Both runtimes bootstrap bilateral state independently
        bs_a = bilateral_seed(rt_a_id, rt_b_id)
        bs_b = bilateral_seed(rt_b_id, rt_a_id)
        assert bs_a == bs_b

        # Shared channel
        agent_x = AgentId(b"agent_x_rt_a____")
        agent_y = AgentId(b"agent_y_rt_b____")
        ch = ChannelId(random_id(16))
        # Both runtimes derive the same seed (same runtime_identity used for seed)
        shared_rt_id = random_state_value()
        sl = seed(shared_rt_id, agent_x, agent_y, ch)

        messages = [
            b"X->Y: Initiate handshake",
            b"Y->X: Handshake accepted",
            b"X->Y: Transfer request",
            b"Y->X: Transfer confirmed",
        ]

        for t, payload in enumerate(messages):
            # Sender derives frame
            frame = sample_frame_cross_runtime(
                sl, t, bs_a.ratchet_state, bs_a.shared_prng_seed,
            )

            # Encode
            ctx = build_encoding_context(sl, ch, t)
            encoded = encode(payload, ctx)
            msg = assemble_message(frame, encoded)

            # Receiver reconstructs frame
            expected = sample_frame_cross_runtime(
                sl, t, bs_b.ratchet_state, bs_b.shared_prng_seed,
            )
            assert frame == expected

            # Validate
            assert validate_frame(msg.frame_open, msg.frame_close, expected)

            # Decode
            decoded = decode(msg.encoded_payload, ctx)
            assert decoded == payload

            # Both sides advance
            sl = advance(sl, frame)
            bs_a = bilateral_advance(bs_a, frame)
            bs_b = bilateral_advance(bs_b, frame)
            assert bs_a == bs_b

        assert bs_a.step == 4
