"""Unit tests for mfp/runtime/quarantine.py (I-09)."""

import pytest

from mfp.core.primitives import random_id, random_state_value
from mfp.core.types import (
    AgentId,
    AgentState,
    Channel,
    ChannelId,
    ChannelState,
    ChannelStatus,
    StateValue,
)
from mfp.runtime.quarantine import (
    check_rate_limit,
    check_validation_failure,
    increment_failure_count,
    quarantine_agent,
    quarantine_channel,
    reset_failure_count,
    restore_agent,
    restore_channel,
)
from mfp.runtime.runtime import AgentRecord
from mfp.runtime.channels import ChannelRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent_id(label: bytes = b"agent-a") -> AgentId:
    return AgentId(label)


def make_channel_id() -> ChannelId:
    return ChannelId(random_id(16))


def make_channel(
    channel_id: ChannelId | None = None,
    status: ChannelStatus = ChannelStatus.ACTIVE,
    failure_count: int = 0,
) -> Channel:
    if channel_id is None:
        channel_id = make_channel_id()
    ch = Channel(
        channel_id=channel_id,
        agent_a=make_agent_id(b"agent-a"),
        agent_b=make_agent_id(b"agent-b"),
        state=ChannelState(local_state=random_state_value(), step=0),
        depth=4,
        status=status,
        validation_failure_count=failure_count,
    )
    return ch


def noop_callable(msg):
    pass


def make_agent_record(
    agent_id: AgentId | None = None,
    state: AgentState = AgentState.ACTIVE,
    channel_ids: set[bytes] | None = None,
) -> AgentRecord:
    if agent_id is None:
        agent_id = make_agent_id()
    return AgentRecord(
        agent_id=agent_id,
        state=state,
        callable=noop_callable,
        channels=channel_ids if channel_ids is not None else set(),
    )


def make_channel_registry(*channels: Channel) -> ChannelRegistry:
    return {ch.channel_id.value: ch for ch in channels}


# ---------------------------------------------------------------------------
# increment_failure_count / reset_failure_count
# ---------------------------------------------------------------------------

class TestFailureTracking:
    def test_increment_failure_count(self):
        ch = make_channel()
        assert ch.validation_failure_count == 0
        increment_failure_count(ch)
        assert ch.validation_failure_count == 1
        increment_failure_count(ch)
        assert ch.validation_failure_count == 2

    def test_reset_failure_count(self):
        ch = make_channel(failure_count=5)
        assert ch.validation_failure_count == 5
        reset_failure_count(ch)
        assert ch.validation_failure_count == 0

    def test_reset_already_zero(self):
        ch = make_channel(failure_count=0)
        reset_failure_count(ch)
        assert ch.validation_failure_count == 0


# ---------------------------------------------------------------------------
# check_validation_failure()
# ---------------------------------------------------------------------------

class TestCheckValidationFailure:
    def test_below_threshold(self):
        ch = make_channel(failure_count=2)
        assert check_validation_failure(ch, 3) is False

    def test_at_threshold(self):
        ch = make_channel(failure_count=3)
        assert check_validation_failure(ch, 3) is True

    def test_above_threshold(self):
        ch = make_channel(failure_count=5)
        assert check_validation_failure(ch, 3) is True

    def test_zero_failures(self):
        ch = make_channel(failure_count=0)
        assert check_validation_failure(ch, 3) is False

    def test_threshold_of_one(self):
        ch = make_channel(failure_count=1)
        assert check_validation_failure(ch, 1) is True


# ---------------------------------------------------------------------------
# check_rate_limit()
# ---------------------------------------------------------------------------

class TestCheckRateLimit:
    def test_unlimited_zero(self):
        assert check_rate_limit(1000, 0) is False

    def test_within_limit(self):
        assert check_rate_limit(5, 10) is False

    def test_at_limit(self):
        assert check_rate_limit(10, 10) is False

    def test_over_limit(self):
        assert check_rate_limit(11, 10) is True

    def test_zero_messages(self):
        assert check_rate_limit(0, 10) is False


# ---------------------------------------------------------------------------
# quarantine_channel()
# ---------------------------------------------------------------------------

class TestQuarantineChannel:
    def test_sets_quarantined_status(self):
        ch = make_channel(status=ChannelStatus.ACTIVE)
        quarantine_channel(ch, reason="test")
        assert ch.status == ChannelStatus.QUARANTINED

    def test_preserves_state(self):
        ch = make_channel()
        original_state = ch.state.local_state
        original_step = ch.state.step
        quarantine_channel(ch)
        assert ch.state.local_state == original_state
        assert ch.state.step == original_step

    def test_no_reason_ok(self):
        ch = make_channel()
        quarantine_channel(ch)
        assert ch.status == ChannelStatus.QUARANTINED


# ---------------------------------------------------------------------------
# quarantine_agent()
# ---------------------------------------------------------------------------

class TestQuarantineAgent:
    def test_sets_agent_quarantined(self):
        agent = make_agent_record()
        registry: ChannelRegistry = {}
        quarantine_agent(agent, registry, reason="bad behavior")
        assert agent.state == AgentState.QUARANTINED
        assert agent.quarantine_reason == "bad behavior"

    def test_quarantines_active_channels(self):
        ch1 = make_channel(status=ChannelStatus.ACTIVE)
        ch2 = make_channel(status=ChannelStatus.ACTIVE)
        registry = make_channel_registry(ch1, ch2)
        agent = make_agent_record(
            channel_ids={ch1.channel_id.value, ch2.channel_id.value},
        )
        result = quarantine_agent(agent, registry, reason="test")
        assert ch1.status == ChannelStatus.QUARANTINED
        assert ch2.status == ChannelStatus.QUARANTINED
        assert len(result) == 2

    def test_skips_non_active_channels(self):
        ch_active = make_channel(status=ChannelStatus.ACTIVE)
        ch_closed = make_channel(status=ChannelStatus.CLOSED)
        ch_quarantined = make_channel(status=ChannelStatus.QUARANTINED)
        registry = make_channel_registry(ch_active, ch_closed, ch_quarantined)
        agent = make_agent_record(
            channel_ids={
                ch_active.channel_id.value,
                ch_closed.channel_id.value,
                ch_quarantined.channel_id.value,
            },
        )
        result = quarantine_agent(agent, registry, reason="test")
        # Only the active channel should be quarantined
        assert len(result) == 1
        assert result[0] == ch_active.channel_id
        assert ch_active.status == ChannelStatus.QUARANTINED
        # Closed channel stays closed
        assert ch_closed.status == ChannelStatus.CLOSED

    def test_returns_quarantined_channel_ids(self):
        ch = make_channel(status=ChannelStatus.ACTIVE)
        registry = make_channel_registry(ch)
        agent = make_agent_record(channel_ids={ch.channel_id.value})
        result = quarantine_agent(agent, registry)
        assert len(result) == 1
        assert isinstance(result[0], ChannelId)

    def test_no_channels(self):
        agent = make_agent_record()
        registry: ChannelRegistry = {}
        result = quarantine_agent(agent, registry)
        assert result == []
        assert agent.state == AgentState.QUARANTINED

    def test_channel_not_in_registry(self):
        fake_id = random_id(16)
        agent = make_agent_record(channel_ids={fake_id})
        registry: ChannelRegistry = {}
        result = quarantine_agent(agent, registry)
        assert result == []
        assert agent.state == AgentState.QUARANTINED


# ---------------------------------------------------------------------------
# restore_channel()
# ---------------------------------------------------------------------------

class TestRestoreChannel:
    def test_restores_to_active(self):
        ch = make_channel(status=ChannelStatus.QUARANTINED)
        restore_channel(ch)
        assert ch.status == ChannelStatus.ACTIVE

    def test_resets_failure_count(self):
        ch = make_channel(status=ChannelStatus.QUARANTINED, failure_count=5)
        restore_channel(ch)
        assert ch.validation_failure_count == 0

    def test_raises_on_active(self):
        ch = make_channel(status=ChannelStatus.ACTIVE)
        with pytest.raises(ValueError, match="Cannot restore"):
            restore_channel(ch)

    def test_raises_on_closed(self):
        ch = make_channel(status=ChannelStatus.CLOSED)
        with pytest.raises(ValueError, match="Cannot restore"):
            restore_channel(ch)

    def test_preserves_state_on_restore(self):
        ch = make_channel(status=ChannelStatus.QUARANTINED)
        original_state = ch.state.local_state
        original_step = ch.state.step
        restore_channel(ch)
        assert ch.state.local_state == original_state
        assert ch.state.step == original_step


# ---------------------------------------------------------------------------
# restore_agent()
# ---------------------------------------------------------------------------

class TestRestoreAgent:
    def test_restores_agent_state(self):
        agent = make_agent_record(state=AgentState.QUARANTINED)
        agent.quarantine_reason = "some reason"
        registry: ChannelRegistry = {}
        restore_agent(agent, registry)
        assert agent.state == AgentState.ACTIVE
        assert agent.quarantine_reason == ""

    def test_restores_quarantined_channels(self):
        ch1 = make_channel(status=ChannelStatus.QUARANTINED, failure_count=3)
        ch2 = make_channel(status=ChannelStatus.QUARANTINED, failure_count=1)
        registry = make_channel_registry(ch1, ch2)
        agent = make_agent_record(
            state=AgentState.QUARANTINED,
            channel_ids={ch1.channel_id.value, ch2.channel_id.value},
        )
        result = restore_agent(agent, registry)
        assert ch1.status == ChannelStatus.ACTIVE
        assert ch2.status == ChannelStatus.ACTIVE
        assert ch1.validation_failure_count == 0
        assert ch2.validation_failure_count == 0
        assert len(result) == 2

    def test_skips_non_quarantined_channels(self):
        ch_active = make_channel(status=ChannelStatus.ACTIVE)
        ch_closed = make_channel(status=ChannelStatus.CLOSED)
        ch_quarantined = make_channel(status=ChannelStatus.QUARANTINED)
        registry = make_channel_registry(ch_active, ch_closed, ch_quarantined)
        agent = make_agent_record(
            state=AgentState.QUARANTINED,
            channel_ids={
                ch_active.channel_id.value,
                ch_closed.channel_id.value,
                ch_quarantined.channel_id.value,
            },
        )
        result = restore_agent(agent, registry)
        assert len(result) == 1
        assert result[0] == ch_quarantined.channel_id
        # Active stays active
        assert ch_active.status == ChannelStatus.ACTIVE
        # Closed stays closed
        assert ch_closed.status == ChannelStatus.CLOSED

    def test_raises_on_non_quarantined_agent(self):
        agent = make_agent_record(state=AgentState.ACTIVE)
        registry: ChannelRegistry = {}
        with pytest.raises(ValueError, match="Cannot restore"):
            restore_agent(agent, registry)

    def test_raises_on_terminated_agent(self):
        agent = make_agent_record(state=AgentState.TERMINATED)
        registry: ChannelRegistry = {}
        with pytest.raises(ValueError, match="Cannot restore"):
            restore_agent(agent, registry)

    def test_no_channels(self):
        agent = make_agent_record(state=AgentState.QUARANTINED)
        registry: ChannelRegistry = {}
        result = restore_agent(agent, registry)
        assert result == []
        assert agent.state == AgentState.ACTIVE

    def test_returns_channel_ids(self):
        ch = make_channel(status=ChannelStatus.QUARANTINED)
        registry = make_channel_registry(ch)
        agent = make_agent_record(
            state=AgentState.QUARANTINED,
            channel_ids={ch.channel_id.value},
        )
        result = restore_agent(agent, registry)
        assert len(result) == 1
        assert isinstance(result[0], ChannelId)
