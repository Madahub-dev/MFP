"""Unit tests for mfp/core/ratchet.py (I-03)."""

import pytest

from mfp.core.primitives import random_block, random_id, random_state_value
from mfp.core.ratchet import (
    advance,
    bilateral_advance,
    bilateral_seed,
    compose,
    compose_ordered,
    seed,
)
from mfp.core.types import (
    AgentId,
    BilateralState,
    Block,
    ChannelId,
    Frame,
    GlobalState,
    RuntimeId,
    StateValue,
)


# ---------------------------------------------------------------------------
# advance()
# ---------------------------------------------------------------------------

class TestAdvance:
    def test_returns_state_value(self):
        state = StateValue(b"\x00" * 32)
        frame = Frame((random_block(), random_block()))
        result = advance(state, frame)
        assert isinstance(result, StateValue)

    def test_deterministic(self):
        state = StateValue(b"\x42" * 32)
        frame = Frame((Block(b"\xaa" * 16), Block(b"\xbb" * 16)))
        a = advance(state, frame)
        b = advance(state, frame)
        assert a == b

    def test_different_state_different_result(self):
        s1 = StateValue(b"\x01" * 32)
        s2 = StateValue(b"\x02" * 32)
        frame = Frame((random_block(), random_block()))
        assert advance(s1, frame) != advance(s2, frame)

    def test_different_frame_different_result(self):
        state = StateValue(b"\x42" * 32)
        f1 = Frame((Block(b"\xaa" * 16), Block(b"\xbb" * 16)))
        f2 = Frame((Block(b"\xcc" * 16), Block(b"\xdd" * 16)))
        assert advance(state, f1) != advance(state, f2)

    def test_one_way(self):
        """Cannot recover input from output — just verify output differs from input."""
        state = StateValue(b"\x42" * 32)
        frame = Frame((random_block(), random_block()))
        result = advance(state, frame)
        assert result != state

    def test_chain(self):
        """Multiple advances produce distinct states."""
        state = StateValue(b"\x00" * 32)
        states = [state]
        for _ in range(5):
            frame = Frame((random_block(), random_block()))
            state = advance(state, frame)
            states.append(state)
        # All states should be distinct
        state_set = {s.data for s in states}
        assert len(state_set) == 6


# ---------------------------------------------------------------------------
# seed()
# ---------------------------------------------------------------------------

class TestSeed:
    def test_returns_state_value(self):
        result = seed(
            random_state_value(),
            AgentId(b"agent_a"),
            AgentId(b"agent_b"),
            ChannelId(random_id(16)),
        )
        assert isinstance(result, StateValue)

    def test_commutative_on_agents(self):
        rt = random_state_value()
        a = AgentId(b"alice")
        b = AgentId(b"bob")
        ch = ChannelId(random_id(16))
        assert seed(rt, a, b, ch) == seed(rt, b, a, ch)

    def test_different_runtime_different_seed(self):
        a = AgentId(b"agent_a")
        b = AgentId(b"agent_b")
        ch = ChannelId(random_id(16))
        s1 = seed(random_state_value(), a, b, ch)
        s2 = seed(random_state_value(), a, b, ch)
        assert s1 != s2  # overwhelming probability

    def test_different_channel_different_seed(self):
        rt = random_state_value()
        a = AgentId(b"agent_a")
        b = AgentId(b"agent_b")
        s1 = seed(rt, a, b, ChannelId(random_id(16)))
        s2 = seed(rt, a, b, ChannelId(random_id(16)))
        assert s1 != s2  # overwhelming probability

    def test_same_agent_pair_same_channel(self):
        rt = random_state_value()
        a = AgentId(b"agent_a")
        b = AgentId(b"agent_b")
        ch = ChannelId(b"\x42" * 16)
        assert seed(rt, a, b, ch) == seed(rt, a, b, ch)

    def test_self_channel(self):
        """seed() works with same agent as both endpoints."""
        rt = random_state_value()
        a = AgentId(b"self_agent")
        ch = ChannelId(random_id(16))
        result = seed(rt, a, a, ch)
        assert isinstance(result, StateValue)


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------

class TestCompose:
    def test_returns_global_state(self):
        result = compose([random_state_value()])
        assert isinstance(result, GlobalState)

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="zero states"):
            compose([])

    def test_single_state(self):
        sv = random_state_value()
        result = compose([sv])
        assert isinstance(result, GlobalState)

    def test_deterministic(self):
        states = [random_state_value() for _ in range(3)]
        a = compose(states)
        b = compose(states)
        assert a == b

    def test_order_matters(self):
        s1 = StateValue(b"\x01" * 32)
        s2 = StateValue(b"\x02" * 32)
        assert compose([s1, s2]) != compose([s2, s1])

    def test_different_states_different_result(self):
        a = compose([StateValue(b"\x01" * 32)])
        b = compose([StateValue(b"\x02" * 32)])
        assert a != b

    def test_avalanche(self):
        """Changing one state changes the output."""
        s1 = StateValue(b"\x01" * 32)
        s2 = StateValue(b"\x02" * 32)
        s3 = StateValue(b"\x03" * 32)
        g1 = compose([s1, s2])
        g2 = compose([s1, s3])
        assert g1 != g2


# ---------------------------------------------------------------------------
# compose_ordered()
# ---------------------------------------------------------------------------

class TestComposeOrdered:
    def test_sorts_by_channel_id(self):
        ch1 = ChannelId(b"\x01" * 16)
        ch2 = ChannelId(b"\x02" * 16)
        s1 = random_state_value()
        s2 = random_state_value()
        # Should produce same result regardless of input order
        a = compose_ordered([(ch1, s1), (ch2, s2)])
        b = compose_ordered([(ch2, s2), (ch1, s1)])
        assert a == b

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="zero states"):
            compose_ordered([])

    def test_with_bilateral(self):
        ch = ChannelId(b"\x01" * 16)
        sv = random_state_value()
        rt = RuntimeId(value=random_state_value())
        bsv = random_state_value()
        result = compose_ordered([(ch, sv)], [(rt, bsv)])
        assert isinstance(result, GlobalState)

    def test_bilateral_order_independent(self):
        ch = ChannelId(b"\x01" * 16)
        sv = random_state_value()
        rt1 = RuntimeId(value=StateValue(b"\x01" * 32))
        rt2 = RuntimeId(value=StateValue(b"\x02" * 32))
        bsv1 = random_state_value()
        bsv2 = random_state_value()
        a = compose_ordered([(ch, sv)], [(rt1, bsv1), (rt2, bsv2)])
        b = compose_ordered([(ch, sv)], [(rt2, bsv2), (rt1, bsv1)])
        assert a == b


# ---------------------------------------------------------------------------
# bilateral_seed()
# ---------------------------------------------------------------------------

class TestBilateralSeed:
    def test_returns_bilateral_state(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        result = bilateral_seed(rt_a, rt_b)
        assert isinstance(result, BilateralState)
        assert result.step == 0

    def test_commutative(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        assert bilateral_seed(rt_a, rt_b) == bilateral_seed(rt_b, rt_a)

    def test_different_runtimes_different_state(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        rt_c = RuntimeId(value=random_state_value())
        assert bilateral_seed(rt_a, rt_b) != bilateral_seed(rt_a, rt_c)

    def test_two_components_differ(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        assert bs.ratchet_state != bs.shared_prng_seed


# ---------------------------------------------------------------------------
# bilateral_advance()
# ---------------------------------------------------------------------------

class TestBilateralAdvance:
    def test_advances_step(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        frame = Frame((random_block(), random_block()))
        bs2 = bilateral_advance(bs, frame)
        assert bs2.step == 1

    def test_changes_both_components(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        frame = Frame((random_block(), random_block()))
        bs2 = bilateral_advance(bs, frame)
        assert bs2.ratchet_state != bs.ratchet_state
        assert bs2.shared_prng_seed != bs.shared_prng_seed

    def test_deterministic(self):
        bs = BilateralState(
            ratchet_state=StateValue(b"\x01" * 32),
            shared_prng_seed=StateValue(b"\x02" * 32),
            step=0,
        )
        frame = Frame((Block(b"\xaa" * 16), Block(b"\xbb" * 16)))
        a = bilateral_advance(bs, frame)
        b = bilateral_advance(bs, frame)
        assert a == b

    def test_chain(self):
        rt_a = RuntimeId(value=random_state_value())
        rt_b = RuntimeId(value=random_state_value())
        bs = bilateral_seed(rt_a, rt_b)
        for i in range(5):
            frame = Frame((random_block(), random_block()))
            bs = bilateral_advance(bs, frame)
        assert bs.step == 5
