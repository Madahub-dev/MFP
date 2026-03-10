"""Integration tests for the Runtime class — cross-module composition.

Tests the Runtime as the central engine composing pipeline, channels,
quarantine, and core modules into a working protocol runtime.
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
# Runtime Initialization
# ---------------------------------------------------------------------------

class TestRuntimeInitialization:

    def test_identity_is_32_bytes(self):
        rt = make_runtime()
        assert len(rt.identity.data) == 32

    def test_identity_is_deterministic_with_config(self):
        cfg = RuntimeConfig(deployment_id=b"dep1" * 8, instance_id=b"inst" * 8)
        rt1 = make_runtime(cfg)
        rt2 = make_runtime(cfg)
        assert rt1.identity == rt2.identity

    def test_identity_random_without_config(self):
        rt1 = make_runtime()
        rt2 = make_runtime()
        assert rt1.identity != rt2.identity

    def test_empty_state_on_init(self):
        rt = make_runtime()
        assert rt.global_state is None


# ---------------------------------------------------------------------------
# Agent Binding and Unbinding
# ---------------------------------------------------------------------------

class TestAgentBindingLifecycle:

    def test_bind_returns_agent_id(self):
        rt = make_runtime()
        aid = rt.bind_agent(noop_agent)
        assert len(aid.value) == 32  # SHA-256 output from I-11 identity scheme

    def test_bound_agent_state(self):
        rt = make_runtime()
        aid = rt.bind_agent(noop_agent)
        status = rt.get_status(aid)
        assert status.state == AgentState.BOUND
        assert status.channel_count == 0

    def test_unbind_removes_agent(self):
        rt = make_runtime()
        aid = rt.bind_agent(noop_agent)
        rt.unbind_agent(aid)
        with pytest.raises(AgentError) as exc:
            rt.get_status(aid)
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_bind_multiple_agents(self):
        rt = make_runtime()
        ids = [rt.bind_agent(noop_agent) for _ in range(5)]
        # All unique
        values = [a.value for a in ids]
        assert len(set(values)) == 5


# ---------------------------------------------------------------------------
# Channel Establishment and Sg
# ---------------------------------------------------------------------------

class TestChannelEstablishment:

    def test_establish_creates_channel(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)
        assert len(ch.value) == 16

    def test_establish_sets_agents_active(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        rt.establish_channel(a, b)
        assert rt.get_status(a).state == AgentState.ACTIVE
        assert rt.get_status(b).state == AgentState.ACTIVE

    def test_establish_computes_sg(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        assert rt.global_state is None
        rt.establish_channel(a, b)
        assert rt.global_state is not None
        assert len(rt.global_state.value.data) == 32

    def test_multi_channel_sg_changes(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)

        ch1 = rt.establish_channel(a, b)
        sg1 = rt.global_state

        ch2 = rt.establish_channel(a, c)
        sg2 = rt.global_state

        # Adding a second channel changes Sg
        assert sg1 != sg2

    def test_channel_info_visible_to_agents(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        channels_a = rt.get_channels(a)
        assert len(channels_a) == 1
        assert channels_a[0].channel_id == ch
        assert channels_a[0].peer == b
        assert channels_a[0].status == ChannelStatus.ACTIVE

        channels_b = rt.get_channels(b)
        assert len(channels_b) == 1
        assert channels_b[0].peer == a


# ---------------------------------------------------------------------------
# Send and Receive Through Full Pipeline
# ---------------------------------------------------------------------------

class TestSendReceive:

    def test_send_returns_receipt(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        receipt = rt.send(a, ch, b"hello")
        assert receipt.channel == ch
        assert receipt.step == 0

    def test_send_delivers_to_destination(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        rt.send(a, ch, b"hello from A")

        assert len(inbox_b) == 1
        assert inbox_b[0].payload == b"hello from A"
        assert inbox_b[0].sender == a
        assert inbox_b[0].channel == ch

    def test_send_bidirectional(self):
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(handler_a)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        rt.send(a, ch, b"A to B")
        rt.send(b, ch, b"B to A")

        assert len(inbox_b) == 1
        assert inbox_b[0].payload == b"A to B"
        assert len(inbox_a) == 1
        assert inbox_a[0].payload == b"B to A"

    def test_send_not_on_channel_fails(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch_ab = rt.establish_channel(a, b)
        # Need c to be active to pass the active check
        rt.establish_channel(a, c)

        with pytest.raises(AgentError) as exc:
            rt.send(c, ch_ab, b"intruder")
        assert exc.value.code == AgentErrorCode.INVALID_CHANNEL


# ---------------------------------------------------------------------------
# Multi-Step Conversation
# ---------------------------------------------------------------------------

class TestMultiStepConversation:

    def test_step_advances_on_each_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        for i in range(5):
            receipt = rt.send(a, ch, f"msg {i}".encode())
            assert receipt.step == i

    def test_sg_changes_on_each_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        seen_sg = set()
        seen_sg.add(rt.global_state.value.data)

        for i in range(5):
            rt.send(a, ch, f"msg {i}".encode())
            seen_sg.add(rt.global_state.value.data)

        # Each send produces a new Sg (initial + 5 sends = 6 unique values)
        assert len(seen_sg) == 6

    def test_alternating_senders(self):
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(handler_a)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        for i in range(6):
            sender = a if i % 2 == 0 else b
            rt.send(sender, ch, f"msg {i}".encode())

        # A sent 3, B sent 3
        assert len(inbox_b) == 3  # messages from A
        assert len(inbox_a) == 3  # messages from B


# ---------------------------------------------------------------------------
# State Advancement Atomicity
# ---------------------------------------------------------------------------

class TestStateAdvancementAtomicity:

    def test_sg_changes_after_each_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        sg_before = rt.global_state
        rt.send(a, ch, b"first")
        sg_after = rt.global_state
        assert sg_before != sg_after

    def test_failed_send_preserves_state(self):
        """A send to a quarantined channel does not change Sg."""
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.send(a, ch, b"first")
        sg_before = rt.global_state

        rt.quarantine_channel(ch)

        with pytest.raises(AgentError):
            rt.send(a, ch, b"should fail")

        assert rt.global_state == sg_before


# ---------------------------------------------------------------------------
# Channel Teardown and Sg Recomputation
# ---------------------------------------------------------------------------

class TestChannelTeardown:

    def test_close_channel_removes_from_agent(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.close_channel(ch)

        assert rt.get_status(a).channel_count == 0
        assert rt.get_status(b).channel_count == 0

    def test_close_channel_recomputes_sg(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch1 = rt.establish_channel(a, b)
        ch2 = rt.establish_channel(a, c)

        sg_two = rt.global_state
        rt.close_channel(ch1)
        sg_one = rt.global_state

        assert sg_two != sg_one

    def test_close_last_channel_nullifies_sg(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.close_channel(ch)
        assert rt.global_state is None

    def test_send_on_closed_channel_fails(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)
        rt.close_channel(ch)

        with pytest.raises(AgentError):
            rt.send(a, ch, b"should fail")


# ---------------------------------------------------------------------------
# Quarantine — Channel and Agent
# ---------------------------------------------------------------------------

class TestQuarantineChannel:

    def test_quarantine_channel_blocks_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.quarantine_channel(ch, "test reason")

        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"blocked")
        assert exc.value.code == AgentErrorCode.CHANNEL_QUARANTINED

    def test_restore_channel_allows_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.quarantine_channel(ch)
        rt.restore_channel(ch)

        receipt = rt.send(a, ch, b"restored")
        assert receipt.step == 0

    def test_quarantine_channel_visible_in_info(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.quarantine_channel(ch)

        channels = rt.get_channels(a)
        assert channels[0].status == ChannelStatus.QUARANTINED


class TestQuarantineAgent:

    def test_quarantine_agent_cascades_to_channels(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)

        rt.quarantine_agent(a)

        assert rt.get_status(a).state == AgentState.QUARANTINED
        channels = rt.get_channels(a)
        for info in channels:
            assert info.status == ChannelStatus.QUARANTINED

    def test_quarantine_agent_blocks_send(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        rt.quarantine_agent(a)

        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"blocked")
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_restore_agent_restores_channels(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(handler_b)
        ch = rt.establish_channel(a, b)

        rt.quarantine_agent(a)
        rt.restore_agent(a)

        assert rt.get_status(a).state == AgentState.ACTIVE
        channels = rt.get_channels(a)
        assert channels[0].status == ChannelStatus.ACTIVE

        receipt = rt.send(a, ch, b"restored")
        assert receipt.step == 0

    def test_quarantine_does_not_affect_peer_agent_state(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        rt.establish_channel(a, b)

        rt.quarantine_agent(a)

        assert rt.get_status(b).state == AgentState.ACTIVE


# ---------------------------------------------------------------------------
# Rate Limiting Triggers Quarantine
# ---------------------------------------------------------------------------

class TestRateLimiting:

    def test_rate_limit_quarantines_agent(self):
        cfg = RuntimeConfig(max_message_rate=3)
        rt = make_runtime(cfg)
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        ch = rt.establish_channel(a, b)

        # Send up to the limit (rate check is > max_rate, so send 1,2,3 OK, 4th triggers)
        for i in range(3):
            rt.send(a, ch, f"msg {i}".encode())

        # Next send should trigger rate limit (message_count becomes 3, then
        # check_rate_limit(3, 3) returns False because 3 > 3 is False.
        # After send #3 completes, message_count=3. The 4th send checks
        # check_rate_limit(3, 3) -> 3 > 3 = False.
        # After send #4 completes, message_count=4. The 5th send checks
        # check_rate_limit(4, 3) -> 4 > 3 = True -> quarantine.
        rt.send(a, ch, b"msg 3")  # 4th message OK

        with pytest.raises(AgentError) as exc:
            rt.send(a, ch, b"msg 4")  # 5th triggers quarantine
        assert exc.value.code == AgentErrorCode.QUARANTINED

        assert rt.get_status(a).state == AgentState.QUARANTINED


# ---------------------------------------------------------------------------
# Unbinding Closes All Channels
# ---------------------------------------------------------------------------

class TestUnbindingClosesChannels:

    def test_unbind_closes_all_channels(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch_ab = rt.establish_channel(a, b)
        ch_ac = rt.establish_channel(a, c)

        sg_before = rt.global_state
        rt.unbind_agent(a)

        # Agent a is gone
        with pytest.raises(AgentError):
            rt.get_status(a)

        # Channels are closed — b and c have no channels
        assert rt.get_status(b).channel_count == 0
        assert rt.get_status(c).channel_count == 0

        # Sg is None (no channels remain)
        assert rt.global_state is None

    def test_unbind_one_preserves_other_channels(self):
        rt = make_runtime()
        a = rt.bind_agent(noop_agent)
        b = rt.bind_agent(noop_agent)
        c = rt.bind_agent(noop_agent)
        ch_ab = rt.establish_channel(a, b)
        ch_bc = rt.establish_channel(b, c)

        rt.unbind_agent(a)

        # b-c channel should remain
        assert rt.get_status(b).channel_count == 1
        channels_b = rt.get_channels(b)
        assert channels_b[0].channel_id == ch_bc
