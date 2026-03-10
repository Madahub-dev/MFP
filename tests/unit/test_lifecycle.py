"""Unit tests for mfp/agent/lifecycle.py (I-10)."""

import pytest

from mfp.agent.lifecycle import AgentHandle, bind, unbind
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
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


def _bind_two_agents(runtime: Runtime):
    """Bind two no-op agents and return their handles."""
    return bind(runtime, _noop_agent), bind(runtime, _noop_agent)


def _activate_agent(runtime: Runtime, handle: AgentHandle) -> tuple[AgentHandle, ChannelId]:
    """Bind a second agent, establish a channel, making the first ACTIVE.
    Returns (second_handle, channel_id)."""
    handle_b = bind(runtime, _noop_agent)
    ch_id = runtime.establish_channel(handle.agent_id, handle_b.agent_id)
    return handle_b, ch_id


# ---------------------------------------------------------------------------
# AgentHandle construction
# ---------------------------------------------------------------------------

class TestAgentHandleConstruction:
    def test_construction(self):
        rt = _make_runtime()
        agent_id = rt.bind_agent(_noop_agent)
        handle = AgentHandle(rt, agent_id)
        assert handle.agent_id == agent_id

    def test_agent_id_property(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        assert isinstance(handle.agent_id, AgentId)
        assert len(handle.agent_id.value) == 32


# ---------------------------------------------------------------------------
# AgentHandle.send — state requirements
# ---------------------------------------------------------------------------

class TestAgentHandleSend:
    def test_send_requires_active_fails_in_bound(self):
        """send() must fail when agent is in BOUND state (no channel yet)."""
        rt = _make_runtime()
        handle = _bind_agent(rt)
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            handle.send(dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_send_requires_active_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "test quarantine")
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            handle.send(dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.QUARANTINED

    def test_send_fails_after_unbind(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        unbind(handle)
        dummy_ch = ChannelId(b"\x00" * 16)
        with pytest.raises(AgentError) as exc_info:
            handle.send(dummy_ch, b"hello")
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_send_delegates_to_runtime(self):
        """send() passes this handle's agent_id as the implicit sender."""
        rt = _make_runtime()
        inbox, collector = _make_collecting_agent()
        handle_a = bind(rt, _noop_agent)
        handle_b = bind(rt, collector)
        ch_id = rt.establish_channel(handle_a.agent_id, handle_b.agent_id)
        receipt = handle_a.send(ch_id, b"payload-data")
        assert isinstance(receipt, Receipt)
        assert receipt.channel == ch_id
        assert len(inbox) == 1
        assert inbox[0].payload == b"payload-data"
        assert inbox[0].sender == handle_a.agent_id


# ---------------------------------------------------------------------------
# AgentHandle.channels — state requirements
# ---------------------------------------------------------------------------

class TestAgentHandleChannels:
    def test_channels_requires_active_fails_in_bound(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        with pytest.raises(AgentError) as exc_info:
            handle.channels()
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_channels_requires_active_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "test quarantine")
        with pytest.raises(AgentError) as exc_info:
            handle.channels()
        assert exc_info.value.code == AgentErrorCode.QUARANTINED

    def test_channels_returns_list_when_active(self):
        rt = _make_runtime()
        handle_a = _bind_agent(rt)
        handle_b, ch_id = _activate_agent(rt, handle_a)
        result = handle_a.channels()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ChannelInfo)
        assert result[0].channel_id == ch_id


# ---------------------------------------------------------------------------
# AgentHandle.status — state requirements
# ---------------------------------------------------------------------------

class TestAgentHandleStatus:
    def test_status_works_in_bound(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        result = handle.status()
        assert isinstance(result, AgentStatus)
        assert result.state == AgentState.BOUND
        assert result.channel_count == 0

    def test_status_works_in_active(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        result = handle.status()
        assert result.state == AgentState.ACTIVE
        assert result.channel_count == 1

    def test_status_fails_when_quarantined(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        _activate_agent(rt, handle)
        rt.quarantine_agent(handle.agent_id, "test")
        with pytest.raises(AgentError) as exc_info:
            handle.status()
        assert exc_info.value.code == AgentErrorCode.QUARANTINED

    def test_status_fails_after_unbind(self):
        rt = _make_runtime()
        handle = _bind_agent(rt)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            handle.status()
        assert exc_info.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# bind()
# ---------------------------------------------------------------------------

class TestBind:
    def test_returns_agent_handle(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        assert isinstance(handle, AgentHandle)

    def test_handle_has_valid_agent_id(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        assert isinstance(handle.agent_id, AgentId)
        assert len(handle.agent_id.value) == 32

    def test_creates_agent_in_bound_state(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        status = handle.status()
        assert status.state == AgentState.BOUND


# ---------------------------------------------------------------------------
# unbind()
# ---------------------------------------------------------------------------

class TestUnbind:
    def test_unbind_removes_agent(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            handle.status()
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_unbind_subsequent_send_fails(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            handle.send(ChannelId(b"\x00" * 16), b"data")
        assert exc_info.value.code == AgentErrorCode.UNBOUND

    def test_unbind_subsequent_channels_fails(self):
        rt = _make_runtime()
        handle = bind(rt, _noop_agent)
        unbind(handle)
        with pytest.raises(AgentError) as exc_info:
            handle.channels()
        assert exc_info.value.code == AgentErrorCode.UNBOUND


# ---------------------------------------------------------------------------
# Full Lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_bind_channel_send_close_status_unbind(self):
        """Full lifecycle: bind -> establish_channel -> send -> close -> status -> unbind."""
        rt = _make_runtime()
        inbox, collector = _make_collecting_agent()

        # Bind two agents
        handle_a = bind(rt, _noop_agent)
        handle_b = bind(rt, collector)

        # Both start in BOUND
        assert handle_a.status().state == AgentState.BOUND
        assert handle_b.status().state == AgentState.BOUND

        # Establish channel transitions both to ACTIVE
        ch_id = rt.establish_channel(handle_a.agent_id, handle_b.agent_id)
        assert handle_a.status().state == AgentState.ACTIVE
        assert handle_b.status().state == AgentState.ACTIVE
        assert handle_a.status().channel_count == 1
        assert handle_b.status().channel_count == 1

        # Send a message
        receipt = handle_a.send(ch_id, b"lifecycle-test")
        assert isinstance(receipt, Receipt)
        assert len(inbox) == 1
        assert inbox[0].payload == b"lifecycle-test"

        # Close channel — agents revert to BOUND
        rt.close_channel(ch_id)
        assert handle_a.status().state == AgentState.BOUND
        assert handle_a.status().channel_count == 0

        # Unbind
        unbind(handle_a)
        with pytest.raises(AgentError) as exc_info:
            handle_a.status()
        assert exc_info.value.code == AgentErrorCode.UNBOUND
