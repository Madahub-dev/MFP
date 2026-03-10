"""Unit tests for mfp/core/frame.py (I-04)."""

import pytest

from mfp.core.frame import (
    assemble_message,
    derive_distribution_seed,
    sample_frame,
    sample_frame_cross_runtime,
    validate_frame,
    xor_bytes,
)
from mfp.core.primitives import random_block, random_state_value
from mfp.core.types import (
    BLOCK_SIZE,
    DEFAULT_FRAME_DEPTH,
    Block,
    Frame,
    ProtocolMessage,
    StateValue,
)


# ---------------------------------------------------------------------------
# xor_bytes()
# ---------------------------------------------------------------------------

class TestXorBytes:
    def test_basic(self):
        assert xor_bytes(b"\xff", b"\x0f") == b"\xf0"

    def test_zeros(self):
        assert xor_bytes(b"\x00" * 4, b"\x00" * 4) == b"\x00" * 4

    def test_identity(self):
        data = b"\xab\xcd\xef\x01"
        assert xor_bytes(data, b"\x00" * 4) == data

    def test_self_inverse(self):
        a = b"\xab\xcd\xef\x01"
        b = b"\x12\x34\x56\x78"
        result = xor_bytes(a, b)
        assert xor_bytes(result, b) == a

    def test_different_lengths_raises(self):
        with pytest.raises(ValueError, match="equal lengths"):
            xor_bytes(b"\x00" * 3, b"\x00" * 4)


# ---------------------------------------------------------------------------
# derive_distribution_seed()
# ---------------------------------------------------------------------------

class TestDeriveDistributionSeed:
    def test_returns_state_value(self):
        result = derive_distribution_seed(
            StateValue(b"\x00" * 32), 0, StateValue(b"\x01" * 32),
        )
        assert isinstance(result, StateValue)

    def test_deterministic(self):
        sl = StateValue(b"\x42" * 32)
        sg = StateValue(b"\x43" * 32)
        a = derive_distribution_seed(sl, 0, sg)
        b = derive_distribution_seed(sl, 0, sg)
        assert a == b

    def test_different_step(self):
        sl = StateValue(b"\x42" * 32)
        sg = StateValue(b"\x43" * 32)
        a = derive_distribution_seed(sl, 0, sg)
        b = derive_distribution_seed(sl, 1, sg)
        assert a != b

    def test_different_local_state(self):
        sg = StateValue(b"\x43" * 32)
        a = derive_distribution_seed(StateValue(b"\x01" * 32), 0, sg)
        b = derive_distribution_seed(StateValue(b"\x02" * 32), 0, sg)
        assert a != b

    def test_different_global_state(self):
        sl = StateValue(b"\x42" * 32)
        a = derive_distribution_seed(sl, 0, StateValue(b"\x01" * 32))
        b = derive_distribution_seed(sl, 0, StateValue(b"\x02" * 32))
        assert a != b


# ---------------------------------------------------------------------------
# sample_frame()
# ---------------------------------------------------------------------------

class TestSampleFrame:
    def test_returns_frame(self):
        f = sample_frame(random_state_value(), 0, random_state_value())
        assert isinstance(f, Frame)

    def test_default_depth(self):
        f = sample_frame(random_state_value(), 0, random_state_value())
        assert f.depth == DEFAULT_FRAME_DEPTH

    def test_custom_depth(self):
        f = sample_frame(random_state_value(), 0, random_state_value(), depth=2)
        assert f.depth == 2

    def test_block_sizes(self):
        f = sample_frame(random_state_value(), 0, random_state_value())
        for block in f.blocks:
            assert len(block.data) == BLOCK_SIZE

    def test_non_deterministic(self):
        """Intra-runtime sampling uses OS jitter — two samples should differ."""
        sl = StateValue(b"\x42" * 32)
        sg = StateValue(b"\x43" * 32)
        f1 = sample_frame(sl, 0, sg)
        f2 = sample_frame(sl, 0, sg)
        # Same distribution but different jitter → different frames
        # (overwhelmingly likely with 512 bits of randomness)
        assert f1 != f2

    def test_to_bytes_size(self):
        f = sample_frame(random_state_value(), 0, random_state_value(), depth=4)
        assert len(f.to_bytes()) == 4 * BLOCK_SIZE


# ---------------------------------------------------------------------------
# sample_frame_cross_runtime()
# ---------------------------------------------------------------------------

class TestSampleFrameCrossRuntime:
    def test_deterministic(self):
        sl = StateValue(b"\x42" * 32)
        br = StateValue(b"\x43" * 32)
        ps = StateValue(b"\x44" * 32)
        f1 = sample_frame_cross_runtime(sl, 0, br, ps)
        f2 = sample_frame_cross_runtime(sl, 0, br, ps)
        assert f1 == f2

    def test_different_step_different_frame(self):
        sl = StateValue(b"\x42" * 32)
        br = StateValue(b"\x43" * 32)
        ps = StateValue(b"\x44" * 32)
        f1 = sample_frame_cross_runtime(sl, 0, br, ps)
        f2 = sample_frame_cross_runtime(sl, 1, br, ps)
        assert f1 != f2

    def test_different_prng_seed_different_frame(self):
        sl = StateValue(b"\x42" * 32)
        br = StateValue(b"\x43" * 32)
        f1 = sample_frame_cross_runtime(sl, 0, br, StateValue(b"\x01" * 32))
        f2 = sample_frame_cross_runtime(sl, 0, br, StateValue(b"\x02" * 32))
        assert f1 != f2

    def test_idempotent(self):
        """Repeated calls at the same step yield the same frame (no PRNG consumption)."""
        sl = random_state_value()
        br = random_state_value()
        ps = random_state_value()
        results = [sample_frame_cross_runtime(sl, 5, br, ps) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_custom_depth(self):
        sl = random_state_value()
        br = random_state_value()
        ps = random_state_value()
        f = sample_frame_cross_runtime(sl, 0, br, ps, depth=2)
        assert f.depth == 2

    def test_simulated_two_runtimes(self):
        """Simulate two runtimes producing the same frame."""
        sl = StateValue(b"\x42" * 32)
        br = StateValue(b"\x43" * 32)
        ps = StateValue(b"\x44" * 32)
        # Runtime A
        frame_a = sample_frame_cross_runtime(sl, 0, br, ps)
        # Runtime B (same inputs)
        frame_b = sample_frame_cross_runtime(sl, 0, br, ps)
        assert frame_a == frame_b


# ---------------------------------------------------------------------------
# validate_frame()
# ---------------------------------------------------------------------------

class TestValidateFrame:
    def test_valid(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        frame_close = frame.mirror()
        assert validate_frame(frame, frame_close, frame) is True

    def test_wrong_expected(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        frame_close = frame.mirror()
        wrong = sample_frame(random_state_value(), 1, random_state_value())
        assert validate_frame(frame, frame_close, wrong) is False

    def test_broken_mirror(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        wrong_close = sample_frame(random_state_value(), 1, random_state_value())
        assert validate_frame(frame, wrong_close, frame) is False

    def test_both_wrong(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        wrong_close = sample_frame(random_state_value(), 1, random_state_value())
        wrong_expected = sample_frame(random_state_value(), 2, random_state_value())
        assert validate_frame(frame, wrong_close, wrong_expected) is False

    def test_cross_runtime_valid(self):
        sl = random_state_value()
        br = random_state_value()
        ps = random_state_value()
        frame = sample_frame_cross_runtime(sl, 0, br, ps)
        expected = sample_frame_cross_runtime(sl, 0, br, ps)
        assert validate_frame(frame, frame.mirror(), expected) is True


# ---------------------------------------------------------------------------
# assemble_message()
# ---------------------------------------------------------------------------

class TestAssembleMessage:
    def test_returns_protocol_message(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        msg = assemble_message(frame, b"payload")
        assert isinstance(msg, ProtocolMessage)

    def test_frame_close_is_mirror(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        msg = assemble_message(frame, b"payload")
        assert msg.frame_close == frame.mirror()

    def test_payload_preserved(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        msg = assemble_message(frame, b"test payload here")
        assert msg.encoded_payload == b"test payload here"

    def test_to_bytes_size(self):
        frame = sample_frame(random_state_value(), 0, random_state_value(), depth=4)
        payload = b"x" * 100
        msg = assemble_message(frame, payload)
        expected_size = 2 * 4 * BLOCK_SIZE + 100
        assert len(msg.to_bytes()) == expected_size

    def test_roundtrip_with_validation(self):
        frame = sample_frame(random_state_value(), 0, random_state_value())
        msg = assemble_message(frame, b"payload")
        assert validate_frame(msg.frame_open, msg.frame_close, frame)
