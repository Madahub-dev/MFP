"""End-to-end tests for the Agent layer — full lifecycle simulations.

Uses AgentHandle, bind/unbind, and protocol tools to exercise complete
agent-to-agent interactions through the real Runtime.
"""

import pytest

from mfp.agent.lifecycle import AgentHandle, bind, unbind
from mfp.agent.tools import mfp_channels, mfp_send, mfp_status
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
# Two Agents: 10-Message Alternating Conversation via Tools
# ---------------------------------------------------------------------------

class TestTwoAgentConversationViaTools:

    def test_10_message_alternating_via_tools(self):
        """Two agents exchange 10 messages using mfp_send, mfp_channels, mfp_status."""
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()

        h_a = bind(rt, handler_a)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        # Verify both are ACTIVE via tool
        assert mfp_status(h_a).state == AgentState.ACTIVE
        assert mfp_status(h_b).state == AgentState.ACTIVE

        # Verify channels visible via tool
        ch_a = mfp_channels(h_a)
        ch_b = mfp_channels(h_b)
        assert len(ch_a) == 1
        assert len(ch_b) == 1
        assert ch_a[0].channel_id == ch
        assert ch_b[0].channel_id == ch

        # 10-message alternating conversation
        for i in range(10):
            sender_h = h_a if i % 2 == 0 else h_b
            label = "A" if i % 2 == 0 else "B"
            payload = f"Message {i} from {label}".encode()
            receipt = mfp_send(sender_h, ch, payload)
            assert receipt.step == i
            assert receipt.channel == ch

        # A sent 0,2,4,6,8 -> B received 5
        assert len(inbox_b) == 5
        for idx, msg in enumerate(inbox_b):
            expected_i = idx * 2
            assert msg.payload == f"Message {expected_i} from A".encode()
            assert msg.sender == h_a.agent_id

        # B sent 1,3,5,7,9 -> A received 5
        assert len(inbox_a) == 5
        for idx, msg in enumerate(inbox_a):
            expected_i = idx * 2 + 1
            assert msg.payload == f"Message {expected_i} from B".encode()
            assert msg.sender == h_b.agent_id


# ---------------------------------------------------------------------------
# Three Agents with Star Topology, All Using AgentHandle
# ---------------------------------------------------------------------------

class TestThreeAgentStarTopologyViaHandles:

    def test_star_topology_via_handles(self):
        """A<->B, A<->C, B<->C — all communication via AgentHandle."""
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        inbox_c, handler_c = collecting_agent()

        h_a = bind(rt, handler_a)
        h_b = bind(rt, handler_b)
        h_c = bind(rt, handler_c)

        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        ch_ac = rt.establish_channel(h_a.agent_id, h_c.agent_id)
        ch_bc = rt.establish_channel(h_b.agent_id, h_c.agent_id)

        # All agents ACTIVE with 2 channels each
        assert h_a.status().channel_count == 2
        assert h_b.status().channel_count == 2
        assert h_c.status().channel_count == 2

        # Send on every link in both directions
        h_a.send(ch_ab, b"A->B: greetings")
        h_b.send(ch_ab, b"B->A: greetings back")

        h_a.send(ch_ac, b"A->C: greetings")
        h_c.send(ch_ac, b"C->A: greetings back")

        h_b.send(ch_bc, b"B->C: greetings")
        h_c.send(ch_bc, b"C->B: greetings back")

        # Each agent received 2 messages (one from each of the other two)
        assert len(inbox_a) == 2
        assert len(inbox_b) == 2
        assert len(inbox_c) == 2

        # Verify correct senders
        a_senders = {m.sender.value for m in inbox_a}
        assert h_b.agent_id.value in a_senders
        assert h_c.agent_id.value in a_senders

        b_senders = {m.sender.value for m in inbox_b}
        assert h_a.agent_id.value in b_senders
        assert h_c.agent_id.value in b_senders

        c_senders = {m.sender.value for m in inbox_c}
        assert h_a.agent_id.value in c_senders
        assert h_b.agent_id.value in c_senders

        # Verify correct channels
        a_channels = {m.channel.value for m in inbox_a}
        assert ch_ab.value in a_channels
        assert ch_ac.value in a_channels

        b_channels = {m.channel.value for m in inbox_b}
        assert ch_ab.value in b_channels
        assert ch_bc.value in b_channels

        c_channels = {m.channel.value for m in inbox_c}
        assert ch_ac.value in c_channels
        assert ch_bc.value in c_channels


# ---------------------------------------------------------------------------
# Full Lifecycle: bind -> establish -> send x N -> quarantine -> restore ->
#                 send -> close -> unbind
# ---------------------------------------------------------------------------

class TestFullLifecycleViaHandle:

    def test_complete_lifecycle(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()

        # BIND
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        assert h_a.status().state == AgentState.BOUND
        assert h_b.status().state == AgentState.BOUND

        # ESTABLISH -> ACTIVE
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        assert h_a.status().state == AgentState.ACTIVE
        assert h_b.status().state == AgentState.ACTIVE
        assert h_a.status().channel_count == 1

        # SEND x N
        for i in range(5):
            receipt = h_a.send(ch, f"msg-{i}".encode())
            assert receipt.step == i
        assert len(inbox_b) == 5

        # QUARANTINE
        rt.quarantine_agent(h_a.agent_id)
        with pytest.raises(AgentError) as exc:
            h_a.send(ch, b"blocked")
        assert exc.value.code == AgentErrorCode.QUARANTINED
        with pytest.raises(AgentError) as exc:
            h_a.channels()
        assert exc.value.code == AgentErrorCode.QUARANTINED
        with pytest.raises(AgentError) as exc:
            h_a.status()
        assert exc.value.code == AgentErrorCode.QUARANTINED

        # RESTORE
        rt.restore_agent(h_a.agent_id)
        assert h_a.status().state == AgentState.ACTIVE
        channels_after_restore = h_a.channels()
        assert len(channels_after_restore) == 1
        assert channels_after_restore[0].status == ChannelStatus.ACTIVE

        # SEND after restore (state continues)
        receipt = h_a.send(ch, b"after restore")
        assert receipt.step == 5
        assert len(inbox_b) == 6

        # CLOSE
        rt.close_channel(ch)
        assert h_a.status().state == AgentState.BOUND
        assert h_b.status().state == AgentState.BOUND
        assert h_a.status().channel_count == 0

        # UNBIND
        unbind(h_a)
        with pytest.raises(AgentError) as exc:
            h_a.status()
        assert exc.value.code == AgentErrorCode.UNBOUND

        unbind(h_b)
        with pytest.raises(AgentError) as exc:
            h_b.status()
        assert exc.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# Multiple Runtimes: Agent from Runtime A Cannot Use Runtime B's Channels
# ---------------------------------------------------------------------------

class TestMultipleRuntimesIsolation:

    def test_agent_from_rt_a_unknown_in_rt_b(self):
        rt_a = make_runtime()
        rt_b = make_runtime()

        h_a1 = bind(rt_a, noop_agent)

        # rt_b has no knowledge of h_a1's agent_id
        with pytest.raises(AgentError) as exc:
            rt_b.get_status(h_a1.agent_id)
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_cross_runtime_channel_isolation(self):
        """Agent from runtime A cannot send on runtime B's channel."""
        rt_a = make_runtime()
        rt_b = make_runtime()

        # Create agents and channel on rt_a
        h_a1 = bind(rt_a, noop_agent)
        h_a2 = bind(rt_a, noop_agent)
        ch_a = rt_a.establish_channel(h_a1.agent_id, h_a2.agent_id)

        # Create agents and channel on rt_b
        h_b1 = bind(rt_b, noop_agent)
        h_b2 = bind(rt_b, noop_agent)
        ch_b = rt_b.establish_channel(h_b1.agent_id, h_b2.agent_id)

        # h_a1 cannot look up ch_b via rt_a (channel doesn't exist there)
        with pytest.raises(AgentError):
            rt_a.send(h_a1.agent_id, ch_b, b"cross-runtime")

        # h_b1 cannot look up ch_a via rt_b (channel doesn't exist there)
        with pytest.raises(AgentError):
            rt_b.send(h_b1.agent_id, ch_a, b"cross-runtime")

    def test_multiple_runtimes_independent_state(self):
        """Operations on one runtime do not affect another."""
        rt_a = make_runtime()
        rt_b = make_runtime()

        h_a1 = bind(rt_a, noop_agent)
        h_a2 = bind(rt_a, noop_agent)
        ch_a = rt_a.establish_channel(h_a1.agent_id, h_a2.agent_id)

        h_b1 = bind(rt_b, noop_agent)
        h_b2 = bind(rt_b, noop_agent)
        ch_b = rt_b.establish_channel(h_b1.agent_id, h_b2.agent_id)

        # Send on rt_a
        rt_a.send(h_a1.agent_id, ch_a, b"hello from rt_a")
        sg_a = rt_a.global_state

        # rt_b's global state is unaffected
        sg_b_before = rt_b.global_state
        rt_b.send(h_b1.agent_id, ch_b, b"hello from rt_b")
        sg_b_after = rt_b.global_state

        # Both runtimes have different Sg values
        assert sg_a != sg_b_before
        assert sg_a != sg_b_after

    def test_handles_bound_to_their_runtime(self):
        """A handle created via bind(rt_a, ...) always routes through rt_a."""
        rt_a = make_runtime()
        rt_b = make_runtime()

        inbox_a2, handler_a2 = collecting_agent()
        h_a1 = bind(rt_a, noop_agent)
        h_a2 = bind(rt_a, handler_a2)
        ch_a = rt_a.establish_channel(h_a1.agent_id, h_a2.agent_id)

        # h_a1.send routes through rt_a, delivers to h_a2
        h_a1.send(ch_a, b"via handle")
        assert len(inbox_a2) == 1
        assert inbox_a2[0].payload == b"via handle"

        # rt_b knows nothing about these agents
        with pytest.raises(AgentError):
            rt_b.get_status(h_a1.agent_id)


# ---------------------------------------------------------------------------
# Agent Identity Uniqueness: Bind 20 Agents, Verify All IDs Distinct
# ---------------------------------------------------------------------------

class TestAgentIdentityUniqueness:

    def test_20_agents_all_distinct_ids(self):
        rt = make_runtime()
        handles = [bind(rt, noop_agent) for _ in range(20)]
        ids = [h.agent_id.value for h in handles]
        assert len(set(ids)) == 20

    def test_20_agents_all_32_bytes(self):
        rt = make_runtime()
        handles = [bind(rt, noop_agent) for _ in range(20)]
        for h in handles:
            assert len(h.agent_id.value) == 32

    def test_20_agents_across_two_runtimes(self):
        """10 agents on each of two runtimes — all 20 IDs distinct."""
        rt_a = make_runtime()
        rt_b = make_runtime()

        handles_a = [bind(rt_a, noop_agent) for _ in range(10)]
        handles_b = [bind(rt_b, noop_agent) for _ in range(10)]

        all_ids = [h.agent_id.value for h in handles_a + handles_b]
        assert len(set(all_ids)) == 20


# ---------------------------------------------------------------------------
# Tool Access Control Matrix: Every Tool in Every State
# ---------------------------------------------------------------------------

class TestToolAccessControlMatrix:
    """Test every tool (send, channels, status) in every state
    (BOUND, ACTIVE, QUARANTINED, unbound)."""

    def test_bound_state_tools(self):
        """In BOUND state: status is allowed, send and channels are not."""
        rt = make_runtime()
        h = bind(rt, noop_agent)

        # status is allowed in BOUND
        status = mfp_status(h)
        assert status.state == AgentState.BOUND

        # channels requires ACTIVE — raises UNBOUND error code
        with pytest.raises(AgentError) as exc:
            mfp_channels(h)
        assert exc.value.code == AgentErrorCode.UNBOUND

        # send requires ACTIVE — but we need a channel_id to even call it.
        # Create a dummy channel_id from another pair so we have a valid one.
        h2 = bind(rt, noop_agent)
        h3 = bind(rt, noop_agent)
        ch = rt.establish_channel(h2.agent_id, h3.agent_id)

        # h is still BOUND, so send should fail
        with pytest.raises(AgentError) as exc:
            mfp_send(h, ch, b"from bound agent")
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_active_state_tools(self):
        """In ACTIVE state: all tools are allowed."""
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        # status
        status = mfp_status(h_a)
        assert status.state == AgentState.ACTIVE

        # channels
        channels = mfp_channels(h_a)
        assert len(channels) == 1

        # send
        receipt = mfp_send(h_a, ch, b"from active agent")
        assert receipt.step == 0
        assert len(inbox_b) == 1

    def test_quarantined_state_tools(self):
        """In QUARANTINED state: all tools raise QUARANTINED error."""
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)

        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED

        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED

        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"from quarantined agent")
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_unbound_state_tools(self):
        """After unbind: all tools raise UNBOUND error."""
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        unbind(h_a)

        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND

        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND

        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"from unbound agent")
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_transition_through_all_states(self):
        """Walk through BOUND -> ACTIVE -> QUARANTINED -> ACTIVE -> unbound,
        verifying tool access at each stage."""
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)

        # -- BOUND --
        assert mfp_status(h_a).state == AgentState.BOUND
        with pytest.raises(AgentError):
            mfp_channels(h_a)

        # -- ACTIVE --
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        assert mfp_status(h_a).state == AgentState.ACTIVE
        assert len(mfp_channels(h_a)) == 1
        mfp_send(h_a, ch, b"active send")
        assert len(inbox_b) == 1

        # -- QUARANTINED --
        rt.quarantine_agent(h_a.agent_id)
        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED
        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED
        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"quarantined send")
        assert exc.value.code == AgentErrorCode.QUARANTINED

        # -- RESTORED (back to ACTIVE) --
        rt.restore_agent(h_a.agent_id)
        assert mfp_status(h_a).state == AgentState.ACTIVE
        assert len(mfp_channels(h_a)) == 1
        mfp_send(h_a, ch, b"restored send")
        assert len(inbox_b) == 2

        # -- UNBOUND --
        unbind(h_a)
        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND
        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND
        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"unbound send")
        assert exc.value.code == AgentErrorCode.UNBOUND
