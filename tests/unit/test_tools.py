"""Unit tests for mfp/agent/tools.py (I-12)."""

import pytest

from mfp.agent.lifecycle import AgentHandle, bind, unbind
from mfp.agent.tools import mfp_channels, mfp_send, mfp_status
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentState,
    AgentStatus,
    ChannelId,
    ChannelInfo,
    DeliveredMessage,
    Receipt,
)
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime() -> Runtime:
    """Create a Runtime with deterministic identity for testing."""
    return Runtime(RuntimeConfig(
        deployment_id=b"test-deploy-id-00000000000000000",
        instance_id=b"test-instance-id-000000000000000",
    ))


def _noop_agent(msg: DeliveredMessage) -> None:
    """Agent callable that does nothing."""
    pass


def _make_collecting_agent() -> tuple[list[DeliveredMessage], "AgentCallable"]:
    """Return a list and a callable that appends delivered messages to it."""
    inbox: list[DeliveredMessage] = []

    def agent(msg: DeliveredMessage) -> None:
        inbox.append(msg)

    return inbox, agent


def _bind_agent(runtime: Runtime) -> AgentHandle:
    """Bind a no-op agent and return its handle."""
    return bind(runtime, _noop_agent)


def _activate_agent(runtime: Runtime, handle: AgentHandle) -> tuple[AgentHandle, ChannelId]:
    """Bind a second agent, establish a channel, making the first ACTIVE.
    Returns (second_handle, channel_id)."""
    handle_b = bind(runtime, _noop_agent)
    ch_id = runtime.establish_channel(handle.agent_id, handle_b.agent_id)
    return handle_b, ch_id


# ---------------------------------------------------------------------------
# mfp_send — delegation
# ---------------------------------------------------------------------------

class TestMfpSend:
    def test_delegates_to_handle_send(self):
        """mfp_send delegates to handle.send and returns the receipt."""
        rt = _make_runtime()
        inbox, collector = _make_collecting_agent()
        handle_a = bind(rt, _noop_agent)
        handle_b = bind(rt, collector)
        ch_id = rt.establish_channel(handle_a.agent_id, handle_b.agent_id)
        receipt = mfp_send(handle_a, ch_id, b"tool-payload")
        assert isinstance(receipt, Receipt)
        assert receipt.channel == ch_id
        assert len(inbox) == 1
        assert inbox[0].payload == b"tool-payload"
        assert inbox[0].sender == handle_a.agent_id

    def test_requires_active_fails_in_bound(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            mfp_send(handle, dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_requires_active_fails_after_unbind(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        unbind(handle)
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            mfp_send(handle, dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_requires_active_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "quarantined")
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            mfp_send(handle, dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.QUARANTINED


# ---------------------------------------------------------------------------
# mfp_channels — delegation
# ---------------------------------------------------------------------------

class TestMfpChannels:
    def test_delegates_to_handle_channels(self):
        rt = _make_runtime()
        handle_a = _bind_agent(rt)
        handle_b, ch_id = _activate_agent(rt, handle_a)
        result = mfp_channels(handle_a)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ChannelInfo)
        assert result[0].channel_id == ch_id

    def test_requires_active_fails_in_bound(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        with pytest.raises(AgentError) as exc_info:
            mfp_channels(handle)
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_requires_active_fails_after_unbind(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            mfp_channels(handle)
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_requires_active_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "quarantined")
        with pytest.raises(AgentError) as exc_info:
            mfp_channels(handle)
        assert exc_info.value.code == AgentErrorCode.QUARANTINED


# ---------------------------------------------------------------------------
# mfp_status — delegation
# ---------------------------------------------------------------------------

class TestMfpStatus:
    def test_delegates_to_handle_status_bound(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        result = mfp_status(handle)
        assert isinstance(result, AgentStatus)
        assert result.state == AgentState.BOUND
        assert result.channel_count == 0

    def test_delegates_to_handle_status_active(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        result = mfp_status(handle)
        assert result.state == AgentState.ACTIVE
        assert result.channel_count == 1

    def test_requires_bound_or_active_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "quarantined")
        with pytest.raises(AgentError) as exc_info:
            mfp_status(handle)
        assert exc_info.value.code == AgentErrorCode.QUARANTINED

    def test_requires_bound_or_active_fails_after_unbind(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            mfp_status(handle)
        assert exc_info.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# Full Roundtrip via Tools
# ---------------------------------------------------------------------------

class TestFullRoundtrip:
    def test_bind_channel_mfp_send_verify_delivery(self):
        """Full roundtrip: bind, establish channel, mfp_send, verify delivery."""
        rt = _make_runtime()
        inbox, collector = _make_collecting_agent()

        # Bind sender and receiver
        handle_sender = bind(rt, _noop_agent)
        handle_receiver = bind(rt, collector)

        # Establish channel (both become ACTIVE)
        ch_id = rt.establish_channel(
            handle_sender.agent_id, handle_receiver.agent_id,
        )

        # Verify state via tools
        sender_status = mfp_status(handle_sender)
        assert sender_status.state == AgentState.ACTIVE

        receiver_status = mfp_status(handle_receiver)
        assert receiver_status.state == AgentState.ACTIVE

        # Verify channels via tools
        sender_channels = mfp_channels(handle_sender)
        assert len(sender_channels) == 1
        assert sender_channels[0].channel_id == ch_id

        # Send message via tool
        receipt = mfp_send(handle_sender, ch_id, b"roundtrip-payload")
        assert isinstance(receipt, Receipt)
        assert receipt.channel == ch_id

        # Verify delivery
        assert len(inbox) == 1
        delivered = inbox[0]
        assert delivered.payload == b"roundtrip-payload"
        assert delivered.sender == handle_sender.agent_id
        assert delivered.channel == ch_id

    def test_multiple_sends_via_tools(self):
        """Send multiple messages and verify all are delivered in order."""
        rt = _make_runtime()
        inbox, collector = _make_collecting_agent()

        handle_a = bind(rt, _noop_agent)
        handle_b = bind(rt, collector)
        ch_id = rt.establish_channel(handle_a.agent_id, handle_b.agent_id)

        payloads = [f"msg-{i}".encode() for i in range(5)]
        for payload in payloads:
            mfp_send(handle_a, ch_id, payload)

        assert len(inbox) == 5
        for i, msg in enumerate(inbox):
            assert msg.payload == payloads[i]
