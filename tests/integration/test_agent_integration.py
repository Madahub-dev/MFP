"""Integration tests for the Agent layer — cross-module composition.

Tests AgentHandle, bind/unbind, tools, and identity working together
across the agent, runtime, and core modules.
"""

import pytest

from mfp.agent.identity import generate_agent_id
from mfp.agent.lifecycle import AgentHandle, bind, unbind
from mfp.agent.tools import mfp_channels, mfp_send, mfp_status
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentState,
    ChannelStatus,
    DeliveredMessage,
    StateValue,
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
# Bind Creates Handle with Valid Agent ID
# ---------------------------------------------------------------------------

class TestBindCreatesHandle:

    def test_bind_returns_agent_handle(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        assert isinstance(handle, AgentHandle)

    def test_bind_agent_id_is_32_bytes(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        assert len(handle.agent_id.value) == 32

    def test_bind_agent_id_is_not_all_zeros(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        assert handle.agent_id.value != b"\x00" * 32


# ---------------------------------------------------------------------------
# Agent Starts in BOUND State
# ---------------------------------------------------------------------------

class TestAgentStartsBound:

    def test_bound_state_via_handle_status(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        status = handle.status()
        assert status.state == AgentState.BOUND

    def test_bound_state_via_tool(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        status = mfp_status(handle)
        assert status.state == AgentState.BOUND

    def test_bound_state_channel_count_zero(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        status = handle.status()
        assert status.channel_count == 0


# ---------------------------------------------------------------------------
# After establish_channel, Status Shows ACTIVE
# ---------------------------------------------------------------------------

class TestEstablishChannelActivates:

    def test_active_after_establish(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)
        assert h_a.status().state == AgentState.ACTIVE
        assert h_b.status().state == AgentState.ACTIVE

    def test_channel_count_increments(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)
        assert h_a.status().channel_count == 1
        assert h_b.status().channel_count == 1

    def test_channels_visible_after_establish(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        channels_a = h_a.channels()
        assert len(channels_a) == 1
        assert channels_a[0].channel_id == ch
        assert channels_a[0].status == ChannelStatus.ACTIVE


# ---------------------------------------------------------------------------
# AgentHandle.send Delivers to Destination Agent
# ---------------------------------------------------------------------------

class TestHandleSendDelivers:

    def test_send_delivers_payload(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        receipt = h_a.send(ch, b"hello via handle")
        assert receipt.channel == ch
        assert len(inbox_b) == 1
        assert inbox_b[0].payload == b"hello via handle"
        assert inbox_b[0].sender == h_a.agent_id

    def test_send_via_tool_delivers_payload(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        receipt = mfp_send(h_a, ch, b"hello via tool")
        assert receipt.channel == ch
        assert len(inbox_b) == 1
        assert inbox_b[0].payload == b"hello via tool"

    def test_bidirectional_send(self):
        rt = make_runtime()
        inbox_a, handler_a = collecting_agent()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, handler_a)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        h_a.send(ch, b"A to B")
        h_b.send(ch, b"B to A")

        assert len(inbox_b) == 1
        assert inbox_b[0].payload == b"A to B"
        assert len(inbox_a) == 1
        assert inbox_a[0].payload == b"B to A"


# ---------------------------------------------------------------------------
# Multiple Agents, Each with Own Handle, Isolated Tool Access
# ---------------------------------------------------------------------------

class TestMultipleAgentsIsolation:

    def test_each_agent_has_unique_handle(self):
        rt = make_runtime()
        handles = [bind(rt, noop_agent) for _ in range(5)]
        ids = [h.agent_id.value for h in handles]
        assert len(set(ids)) == 5

    def test_each_agent_sees_own_channels(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        h_c = bind(rt, noop_agent)

        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        ch_bc = rt.establish_channel(h_b.agent_id, h_c.agent_id)

        # A sees only ch_ab
        channels_a = mfp_channels(h_a)
        assert len(channels_a) == 1
        assert channels_a[0].channel_id == ch_ab

        # B sees both ch_ab and ch_bc
        channels_b = mfp_channels(h_b)
        assert len(channels_b) == 2
        ch_b_ids = {ci.channel_id.value for ci in channels_b}
        assert ch_ab.value in ch_b_ids
        assert ch_bc.value in ch_b_ids

        # C sees only ch_bc
        channels_c = mfp_channels(h_c)
        assert len(channels_c) == 1
        assert channels_c[0].channel_id == ch_bc

    def test_each_agent_has_own_status(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)

        # Both BOUND initially
        assert mfp_status(h_a).state == AgentState.BOUND
        assert mfp_status(h_b).state == AgentState.BOUND

        # After establishing channel, both ACTIVE
        rt.establish_channel(h_a.agent_id, h_b.agent_id)
        assert mfp_status(h_a).state == AgentState.ACTIVE
        assert mfp_status(h_b).state == AgentState.ACTIVE


# ---------------------------------------------------------------------------
# Quarantine via Runtime Blocks All Handle Tools
# ---------------------------------------------------------------------------

class TestQuarantineBlocksHandleTools:

    def test_quarantine_blocks_send(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)

        with pytest.raises(AgentError) as exc:
            h_a.send(ch, b"blocked")
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_quarantine_blocks_channels(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)

        with pytest.raises(AgentError) as exc:
            h_a.channels()
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_quarantine_blocks_status(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)

        with pytest.raises(AgentError) as exc:
            h_a.status()
        assert exc.value.code == AgentErrorCode.QUARANTINED

    def test_quarantine_blocks_tools(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)

        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"blocked")
        assert exc.value.code == AgentErrorCode.QUARANTINED

        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED

        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.QUARANTINED


# ---------------------------------------------------------------------------
# Restore Re-enables Tools
# ---------------------------------------------------------------------------

class TestRestoreReenablesTools:

    def test_restore_enables_send(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)
        rt.restore_agent(h_a.agent_id)

        receipt = h_a.send(ch, b"restored message")
        assert receipt.step == 0
        assert len(inbox_b) == 1

    def test_restore_enables_channels(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)
        rt.restore_agent(h_a.agent_id)

        channels = h_a.channels()
        assert len(channels) == 1
        assert channels[0].status == ChannelStatus.ACTIVE

    def test_restore_enables_status(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)
        rt.restore_agent(h_a.agent_id)

        status = h_a.status()
        assert status.state == AgentState.ACTIVE

    def test_restore_enables_tools(self):
        rt = make_runtime()
        inbox_b, handler_b = collecting_agent()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, handler_b)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        rt.quarantine_agent(h_a.agent_id)
        rt.restore_agent(h_a.agent_id)

        # All tools work
        mfp_send(h_a, ch, b"ok")
        channels = mfp_channels(h_a)
        status = mfp_status(h_a)

        assert len(inbox_b) == 1
        assert len(channels) == 1
        assert status.state == AgentState.ACTIVE


# ---------------------------------------------------------------------------
# After close_channel with Last Channel, Agent Returns to BOUND
# ---------------------------------------------------------------------------

class TestCloseLastChannelDeactivates:

    def test_active_to_bound_on_last_close(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        assert h_a.status().state == AgentState.ACTIVE
        rt.close_channel(ch)
        assert h_a.status().state == AgentState.BOUND
        assert h_b.status().state == AgentState.BOUND

    def test_stays_active_with_remaining_channel(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        h_c = bind(rt, noop_agent)
        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        ch_ac = rt.establish_channel(h_a.agent_id, h_c.agent_id)

        assert h_a.status().state == AgentState.ACTIVE
        rt.close_channel(ch_ab)
        # A still has ch_ac, so stays ACTIVE
        assert h_a.status().state == AgentState.ACTIVE
        assert h_a.status().channel_count == 1

    def test_bound_after_closing_all_channels(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        h_c = bind(rt, noop_agent)
        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        ch_ac = rt.establish_channel(h_a.agent_id, h_c.agent_id)

        rt.close_channel(ch_ab)
        rt.close_channel(ch_ac)
        assert h_a.status().state == AgentState.BOUND
        assert h_a.status().channel_count == 0


# ---------------------------------------------------------------------------
# After Unbind, Handle Raises UNBOUND on All Operations
# ---------------------------------------------------------------------------

class TestUnbindInvalidatesHandle:

    def test_unbind_raises_on_send(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        unbind(h_a)

        with pytest.raises(AgentError) as exc:
            h_a.send(ch, b"should fail")
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_unbind_raises_on_channels(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        rt.establish_channel(h_a.agent_id, h_b.agent_id)

        unbind(h_a)

        with pytest.raises(AgentError) as exc:
            h_a.channels()
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_unbind_raises_on_status(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)

        unbind(h_a)

        with pytest.raises(AgentError) as exc:
            h_a.status()
        assert exc.value.code == AgentErrorCode.UNBOUND

    def test_unbind_raises_on_tools(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        ch = rt.establish_channel(h_a.agent_id, h_b.agent_id)

        unbind(h_a)

        with pytest.raises(AgentError) as exc:
            mfp_send(h_a, ch, b"fail")
        assert exc.value.code == AgentErrorCode.UNBOUND

        with pytest.raises(AgentError) as exc:
            mfp_channels(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND

        with pytest.raises(AgentError) as exc:
            mfp_status(h_a)
        assert exc.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# Agent Can't Send on Another Agent's Channel
# ---------------------------------------------------------------------------

class TestCrossChannelSendBlocked:

    def test_sender_not_on_channel_error(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        h_c = bind(rt, noop_agent)

        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        # Make C active so it passes the ACTIVE check
        rt.establish_channel(h_a.agent_id, h_c.agent_id)

        with pytest.raises(AgentError) as exc:
            h_c.send(ch_ab, b"intruder")
        assert exc.value.code == AgentErrorCode.INVALID_CHANNEL

    def test_sender_not_on_channel_via_tool(self):
        rt = make_runtime()
        h_a = bind(rt, noop_agent)
        h_b = bind(rt, noop_agent)
        h_c = bind(rt, noop_agent)

        ch_ab = rt.establish_channel(h_a.agent_id, h_b.agent_id)
        rt.establish_channel(h_a.agent_id, h_c.agent_id)

        with pytest.raises(AgentError) as exc:
            mfp_send(h_c, ch_ab, b"intruder via tool")
        assert exc.value.code == AgentErrorCode.INVALID_CHANNEL


# ---------------------------------------------------------------------------
# Identity Scheme: 32 bytes, Unique, Incorporates Runtime Identity
# ---------------------------------------------------------------------------

class TestIdentityScheme:

    def test_agent_id_is_32_bytes(self):
        rt = make_runtime()
        handle = bind(rt, noop_agent)
        assert len(handle.agent_id.value) == 32

    def test_agent_ids_unique_per_agent(self):
        rt = make_runtime()
        handles = [bind(rt, noop_agent) for _ in range(10)]
        ids = [h.agent_id.value for h in handles]
        assert len(set(ids)) == 10

    def test_agent_ids_differ_across_runtimes(self):
        """Same counter value on different runtimes produces different IDs."""
        cfg1 = RuntimeConfig(
            deployment_id=b"dep1" + b"\x00" * 28,
            instance_id=b"ins1" + b"\x00" * 28,
        )
        cfg2 = RuntimeConfig(
            deployment_id=b"dep2" + b"\x00" * 28,
            instance_id=b"ins2" + b"\x00" * 28,
        )
        rt1 = make_runtime(cfg1)
        rt2 = make_runtime(cfg2)

        h1 = bind(rt1, noop_agent)
        h2 = bind(rt2, noop_agent)

        # Different runtimes produce different agent IDs
        assert h1.agent_id.value != h2.agent_id.value

    def test_identity_incorporates_runtime_identity(self):
        """Agents from the same runtime share the runtime identity prefix
        in their ID derivation (SHA-256 preimage includes runtime_identity)."""
        rt = make_runtime()
        identity = rt.identity

        # generate_agent_id uses runtime_identity in preimage
        aid = generate_agent_id(identity, counter=1)
        assert len(aid.value) == 32

        # Different runtime identity -> different agent ID (even with same counter)
        other_identity = StateValue(b"\xff" * 32)
        aid_other = generate_agent_id(other_identity, counter=1)
        assert aid.value != aid_other.value
