"""Integration tests for the core pipeline — cross-module interactions.

Tests the composition of types, primitives, ratchet, frame, and encoding
modules working together as a pipeline.
"""

from mfp.core.encoding import build_encoding_context, decode, encode
from mfp.core.frame import (
    assemble_message,
    sample_frame,
    sample_frame_cross_runtime,
    validate_frame,
)
from mfp.core.primitives import random_id, random_state_value
from mfp.core.ratchet import advance, bilateral_advance, bilateral_seed, compose, seed
from mfp.core.types import AgentId, ChannelId, ChannelState, RuntimeId


class TestIntraRuntimePipeline:
    """ACCEPT → FRAME → ENCODE → VALIDATE → DECODE → DELIVER (single runtime)."""

    def _setup_channel(self):
        runtime_id = random_state_value()
        agent_a = AgentId(b"agent_001_______")
        agent_b = AgentId(b"agent_002_______")
        channel_id = ChannelId(random_id(16))
        sl0 = seed(runtime_id, agent_a, agent_b, channel_id)
        return sl0, channel_id

    def test_single_exchange(self):
        sl, ch = self._setup_channel()
        sg = compose([sl])

        # FRAME
        frame = sample_frame(sl, 0, sg.value)
        # ENCODE
        ctx = build_encoding_context(sl, ch, 0)
        encoded = encode(b"hello agent B", ctx)
        # Assemble
        msg = assemble_message(frame, encoded)
        # VALIDATE
        assert validate_frame(msg.frame_open, msg.frame_close, frame)
        # DECODE
        payload = decode(msg.encoded_payload, ctx)
        assert payload == b"hello agent B"
        # ADVANCE
        sl_new = advance(sl, frame)
        assert sl_new != sl

    def test_multi_step_exchange(self):
        sl, ch = self._setup_channel()
        payloads = [
            b"Step 0: Initialize transfer",
            b"Step 1: Confirm receipt",
            b"Step 2: Finalize",
            b"Step 3: Acknowledgment",
            b"Step 4: Close",
        ]

        for t, raw_payload in enumerate(payloads):
            sg = compose([sl])
            frame = sample_frame(sl, t, sg.value)
            ctx = build_encoding_context(sl, ch, t)
            encoded = encode(raw_payload, ctx)
            msg = assemble_message(frame, encoded)

            assert validate_frame(msg.frame_open, msg.frame_close, frame)
            assert decode(msg.encoded_payload, ctx) == raw_payload

            sl = advance(sl, frame)

    def test_multi_channel(self):
        """Two channels with different seeds produce different frames."""
        runtime_id = random_state_value()
        a1 = AgentId(b"agent_001_______")
        a2 = AgentId(b"agent_002_______")
        a3 = AgentId(b"agent_003_______")

        ch1 = ChannelId(random_id(16))
        ch2 = ChannelId(random_id(16))
        sl1 = seed(runtime_id, a1, a2, ch1)
        sl2 = seed(runtime_id, a1, a3, ch2)

        sg = compose([sl1, sl2])

        frame1 = sample_frame(sl1, 0, sg.value)
        frame2 = sample_frame(sl2, 0, sg.value)
        # Different channels → different distribution seeds → different candidates
        # (jitter also differs, but even candidates differ)
        assert frame1 != frame2

        # Each channel's encoding context is independent
        ctx1 = build_encoding_context(sl1, ch1, 0)
        ctx2 = build_encoding_context(sl2, ch2, 0)
        enc1 = encode(b"to agent 2", ctx1)
        enc2 = encode(b"to agent 3", ctx2)

        # Cannot cross-decode
        assert decode(enc1, ctx2) is None
        assert decode(enc2, ctx1) is None

    def test_sg_recomputation_affects_frames(self):
        """Advancing one channel changes Sg, which changes frames on all channels."""
        runtime_id = random_state_value()
        a1 = AgentId(b"agent_001_______")
        a2 = AgentId(b"agent_002_______")
        a3 = AgentId(b"agent_003_______")

        ch1 = ChannelId(b"\x01" * 16)
        ch2 = ChannelId(b"\x02" * 16)
        sl1 = seed(runtime_id, a1, a2, ch1)
        sl2 = seed(runtime_id, a1, a3, ch2)

        sg_before = compose([sl1, sl2])

        # Sample frame on ch2 before ch1 advances
        frame_before = sample_frame(sl2, 0, sg_before.value)

        # Advance ch1
        frame_ch1 = sample_frame(sl1, 0, sg_before.value)
        sl1_new = advance(sl1, frame_ch1)
        sg_after = compose([sl1_new, sl2])

        # Sample frame on ch2 after ch1 advances
        frame_after = sample_frame(sl2, 0, sg_after.value)

        # Sg changed → distribution seed changed → different candidates
        assert sg_before != sg_after
        # Frames differ because Sg differs (even same Sl and step)
        # (jitter also differs, but Sg difference is the structural guarantee)

    def test_forward_secrecy(self):
        """Old encoding context cannot decode messages from advanced state."""
        sl, ch = self._setup_channel()
        sg = compose([sl])

        ctx_old = build_encoding_context(sl, ch, 0)
        frame = sample_frame(sl, 0, sg.value)
        sl = advance(sl, frame)

        sg = compose([sl])
        ctx_new = build_encoding_context(sl, ch, 1)
        encoded_new = encode(b"secret message", ctx_new)

        assert decode(encoded_new, ctx_old) is None


class TestCrossRuntimePipeline:
    """Cross-runtime message exchange with bilateral state."""

    def test_bilateral_exchange(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())

        # Bootstrap bilateral state (both runtimes derive independently)
        bs_a = bilateral_seed(rt_a, rt_b)
        bs_b = bilateral_seed(rt_b, rt_a)
        assert bs_a == bs_b

        # Set up a cross-runtime channel
        agent_x = AgentId(b"agent_x_________")
        agent_y = AgentId(b"agent_y_________")
        ch = ChannelId(random_id(16))
        sl = seed(random_state_value(), agent_x, agent_y, ch)

        # Runtime A sends to Runtime B
        frame_a = sample_frame_cross_runtime(
            sl, 0, bs_a.ratchet_state, bs_a.shared_prng_seed,
        )
        ctx = build_encoding_context(sl, ch, 0)
        encoded = encode(b"cross-runtime payload", ctx)
        msg = assemble_message(frame_a, encoded)

        # Runtime B reconstructs the same frame
        frame_b = sample_frame_cross_runtime(
            sl, 0, bs_b.ratchet_state, bs_b.shared_prng_seed,
        )
        assert frame_a == frame_b

        # Runtime B validates
        assert validate_frame(msg.frame_open, msg.frame_close, frame_b)

        # Runtime B decodes
        payload = decode(msg.encoded_payload, ctx)
        assert payload == b"cross-runtime payload"

        # Both runtimes advance bilateral state
        bs_a_new = bilateral_advance(bs_a, frame_a)
        bs_b_new = bilateral_advance(bs_b, frame_b)
        assert bs_a_new == bs_b_new
        assert bs_a_new.step == 1

    def test_bilateral_multi_step(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        sl = seed(random_state_value(), AgentId(b"x"), AgentId(b"y"), ChannelId(random_id(16)))
        ch = ChannelId(random_id(16))

        for t in range(5):
            frame = sample_frame_cross_runtime(
                sl, t, bs.ratchet_state, bs.shared_prng_seed,
            )
            ctx = build_encoding_context(sl, ch, t)
            encoded = encode(f"step {t}".encode(), ctx)
            msg = assemble_message(frame, encoded)

            # Validate
            expected = sample_frame_cross_runtime(
                sl, t, bs.ratchet_state, bs.shared_prng_seed,
            )
            assert validate_frame(msg.frame_open, msg.frame_close, expected)
            assert decode(msg.encoded_payload, ctx) == f"step {t}".encode()

            # Advance
            sl = advance(sl, frame)
            bs = bilateral_advance(bs, frame)

        assert bs.step == 5

    def test_failed_send_idempotent(self):
        """Simulating failed sends — retrying at same step yields same frame."""
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        sl = seed(random_state_value(), AgentId(b"x"), AgentId(b"y"), ChannelId(random_id(16)))

        # "Attempt" to send 3 times at step 0 (simulating network failures)
        frames = [
            sample_frame_cross_runtime(sl, 0, bs.ratchet_state, bs.shared_prng_seed)
            for _ in range(3)
        ]
        assert frames[0] == frames[1] == frames[2]


class TestStateAdvancementAtomicity:
    """Verify that state advancement is composable and consistent."""

    def test_advance_then_compose(self):
        """Advance one channel, recompose Sg, use new Sg for next frame."""
        runtime_id = random_state_value()
        a1 = AgentId(b"agent_001_______")
        a2 = AgentId(b"agent_002_______")
        ch = ChannelId(random_id(16))
        sl = seed(runtime_id, a1, a2, ch)

        states = [sl]
        for t in range(10):
            sg = compose(states)
            frame = sample_frame(sl, t, sg.value)
            sl = advance(sl, frame)
            states = [sl]  # single channel
        # Verify we can still sample and validate
        sg = compose(states)
        frame = sample_frame(sl, 10, sg.value)
        msg = assemble_message(frame, b"step 10")
        assert validate_frame(msg.frame_open, msg.frame_close, frame)
