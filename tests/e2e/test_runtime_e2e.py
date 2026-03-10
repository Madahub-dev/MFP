"""End-to-end tests for the Runtime — full lifecycle simulations.

Uses the real Runtime class (not simulated) to exercise complete
agent-to-agent message flows across the pipeline.
"""

import pytest

from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentState,
    ChannelStatus,
    DeliveredMessage,
)
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime


# ---------------------------------------------------------------------------
# Helpers (plain functions, no fixtures)
# ---------------------------------------------------------------------------

def make_runtime(config: RuntimeConfig | None = None) -> Runtime:
    """Create a fresh Runtime instance."""
    return Runtime(config)


def noop_agent(msg: DeliveredMessage) -> None:
    """Agent callable that does nothing."""
    pass


def collecting_agent() -> tuple[list[DeliveredMessage], "AgentCallable"]:
    """Return a list and an agent callable that appends delivered messages to it."""
    inbox: list[DeliveredMessage] = []

    def handler(msg: DeliveredMessage) -> None:
        inbox.append(msg)

    return inbox, handler


# ---------------------------------------------------------------------------
# Two-Agent 10-Message Conversation
# ---------------------------------------------------------------------------

class TestTwoAgentConversation:

    def test_10_message_alternating(self):
        """Two agents exchange 10 messages, alternating sender."""
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(handler_a)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        for i in range(10):
            sender = a if i % 2 == 0 else b
            payload = f"Message {i} from {'A' if i % 2 == 0 else 'B'}".encode()
            receipt = rt.send(sender, ch, payload)
            assert receipt.step == i
            assert receipt.channel == ch

        # A sent messages 0,2,4,6,8 -> delivered to B
        assert len(inbox_b) == 5
        for idx, msg in enumerate(inbox_b):
            expected_i = idx * 2
            assert msg.payload == f"Message {expected_i} from A".encode()
            assert msg.sender == a

        # B sent messages 1,3,5,7,9 -> delivered to A
        assert len(inbox_a) == 5
        for idx, msg in enumerate(inbox_a):
            expected_i = idx * 2 + 1
            assert msg.payload == f"Message {expected_i} from B".encode()
            assert msg.sender == b


# ---------------------------------------------------------------------------
# Three-Agent Star Topology
# ---------------------------------------------------------------------------

class TestThreeAgentStarTopology:

    def test_star_topology_messages_on_all_channels(self):
        """A<->B, A<->C, B<->C — messages on all three channels."""
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        inbox_c, handler_c = collecting_agent()

        a = rt.bind_agent(handler_a)
        b = rt.bind_agent(handler_b)
        c = rt.bind_agent(handler_c)

        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)
        ch_bc = rt.establish_channel(b, c)

        # All agents should be ACTIVE with correct channel counts
        assert rt.get_status(a).channel_count == 2
        assert rt.get_status(b).channel_count == 2
        assert rt.get_status(c).channel_count == 2

        # Send on each channel in both directions
        rt.send(a, ch_ab, b"A->B: hello")
        rt.send(b, ch_ab, b"B->A: hello back")

        rt.send(a, ch_ac, b"A->C: hello")
        rt.send(c, ch_ac, b"C->A: hello back")

        rt.send(b, ch_bc, b"B->C: hello")
        rt.send(c, ch_bc, b"C->B: hello back")

        # Verify delivery
        assert len(inbox_a) == 2  # from B via ch_ab, from C via ch_ac
        assert len(inbox_b) == 2  # from A via ch_ab, from C via ch_bc
        assert len(inbox_c) == 2  # from A via ch_ac, from B via ch_bc

        # Verify correct senders
        a_senders = {m.sender.value for m in inbox_a}
        assert b.value in a_senders
        assert c.value in a_senders

        b_senders = {m.sender.value for m in inbox_b}
        assert a.value in b_senders
        assert c.value in b_senders

        c_senders = {m.sender.value for m in inbox_c}
        assert a.value in c_senders
        assert b.value in c_senders


# ---------------------------------------------------------------------------
# Agent Lifecycle: bind -> establish -> send -> quarantine -> restore -> send -> unbind
# ---------------------------------------------------------------------------

class TestAgentLifecycleFull:

    def test_full_lifecycle(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)

        # BOUND
        assert rt.get_status(a).state == AgentState.BOUND

        # ESTABLISH -> ACTIVE
        ch = rt.establish_channel(a, b)
        assert rt.get_status(a).state == AgentState.ACTIVE

        # SEND
        r1 = rt.send(a, ch, b"first message")
        assert r1.step == 0
        assert len(inbox_b) == 1

        # QUARANTINE
        rt.quarantine_agent(a)
        assert rt.get_status(a).state == AgentState.QUARANTINED
        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"blocked")
        assert exc.value.code == AgentErrorCode.QUARANTINED

        # RESTORE
        rt.restore_agent(a)
        assert rt.get_status(a).state == AgentState.ACTIVE

        # SEND again (state continues from where it was frozen)
        r2 = rt.send(a, ch, b"second message after restore")
        assert r2.step == 1
        assert len(inbox_b) == 2

        # UNBIND
        rt.unbind_agent(a)
        with pytest.raises(AgentError):
            rt.get_status(a)

        # b's channels with a are closed
        assert rt.get_status(b).channel_count == 0


# ---------------------------------------------------------------------------
# High Step Count — 50 Messages on One Channel
# ---------------------------------------------------------------------------

class TestHighStepCount:

    def test_50_messages_state_divergence(self):
        """50 messages on a single channel, verify state diverges at each step."""
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        seen_sg_values: list[bytes] = [rt.global_state.value.data]

        for i in range(50):
            receipt = rt.send(a, ch, f"step-{i}".encode())
            assert receipt.step == i
            seen_sg_values.append(rt.global_state.value.data)

        # All 51 Sg values (initial + 50 sends) should be unique
        assert len(set(seen_sg_values)) == 51


# ---------------------------------------------------------------------------
# Multiple Runtimes — Independent Identities and State
# ---------------------------------------------------------------------------

class TestMultipleRuntimes:

    def test_different_identities(self):
        rt1 = make_runtime()
        rt2 = make_runtime()
        assert rt1.identity != rt2.identity

    def test_independent_state(self):
        rt1 = make_runtime()
        rt2 = make_runtime()

        a1 = rt1.bind_agent(noop_agent)
        b1 = rt1.bind_agent(noop_agent)
        ch1 = rt1.establish_channel(a1, b1)

        a2 = rt2.bind_agent(noop_agent)
        b2 = rt2.bind_agent(noop_agent)
        ch2 = rt2.establish_channel(a2, b2)

        # Both have Sg but they differ (different identities -> different seeds)
        assert rt1.global_state is not None
        assert rt2.global_state is not None
        assert rt1.global_state != rt2.global_state

        # Send on rt1 does not affect rt2
        rt1.send(a1, ch1, b"hello from rt1")
        sg1_after = rt1.global_state
        sg2_unchanged = rt2.global_state

        rt2.send(a2, ch2, b"hello from rt2")
        sg2_after = rt2.global_state

        # rt2 was unchanged before its own send
        assert sg2_unchanged != sg2_after

    def test_agent_from_one_runtime_unknown_in_other(self):
        rt1 = make_runtime()
        rt2 = make_runtime()

        a1 = rt1.bind_agent(noop_agent)

        with pytest.raises(AgentError) as exc:
            rt2.get_status(a1)
        assert exc.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# Shutdown Zeros All State
# ---------------------------------------------------------------------------

class TestShutdown:

    def test_shutdown_zeros_state(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)
        rt.send(a, ch, b"data")

        rt.shutdown()

        assert rt.global_state is None

        # Agents are gone
        with pytest.raises(AgentError):
            rt.get_status(a)
        with pytest.raises(AgentError):
            rt.get_status(b)

    def test_shutdown_then_rebind(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        rt.establish_channel(a, b)
        rt.send(a, rt.get_channels(a)[0].channel_id, b"data")

        rt.shutdown()

        # Can rebind fresh agents
        c = rt.bind_agent(noop_agent)
        d = rt.bind_agent(noop_agent)
        ch2 = rt.establish_channel(c, d)
        receipt = rt.send(c, ch2, b"fresh start")
        assert receipt.step == 0


# ---------------------------------------------------------------------------
# Custom RuntimeConfig
# ---------------------------------------------------------------------------

class TestCustomRuntimeConfig:

    def test_payload_size_limit(self):
        cfg = RuntimeConfig(max_payload_size=100)
        rt = make_runtime(cfg)
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        # Small payload OK
        rt.send(a, ch, b"small")

        # Payload exceeding limit fails
        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"X" * 101)
        assert exc.value.code == AgentErrorCode.PAYLOAD_TOO_LARGE

    def test_rate_limit_enforcement(self):
        cfg = RuntimeConfig(max_message_rate=2)
        rt = make_runtime(cfg)
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        # Send up to the limit
        rt.send(a, ch, b"msg 0")  # message_count becomes 1
        rt.send(a, ch, b"msg 1")  # message_count becomes 2

        # check_rate_limit(2, 2) -> 2 > 2 -> False, so 3rd send OK
        # After 3rd send, message_count = 3
        # check_rate_limit(3, 2) -> 3 > 2 -> True -> quarantine on 4th attempt
        rt.send(a, ch, b"msg 2")

        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"msg 3")
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_deterministic_identity_from_config(self):
        cfg = RuntimeConfig(
            deployment_id=b"myapp" + b"\x00" * 27,
            instance_id=b"node1" + b"\x00" * 27,
        )
        rt1 = make_runtime(cfg)
        rt2 = make_runtime(cfg)
        assert rt1.identity == rt2.identity

    def test_empty_payload(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)
        receipt = rt.send(a, ch, b"")
        assert receipt.step == 0
        assert inbox_b[0].payload == b""

    def test_large_payload_no_limit(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)
        large = b"Y" * 50_000
        receipt = rt.send(a, ch, large)
        assert receipt.step == 0
        assert inbox_b[0].payload == large
