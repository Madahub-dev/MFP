"""Unit tests for mfp/runtime/channels.py (I-08)."""

import pytest

from mfp.core.primitives import random_id, random_state_value
from mfp.core.ratchet import seed as ratchet_seed
from mfp.core.types import (
    AgentId,
    Channel,
    ChannelId,
    ChannelInfo,
    ChannelState,
    ChannelStatus,
    StateValue,
)
from mfp.runtime.channels import (
    ChannelRegistry,
    advance_channel,
    close_channel,
    establish_channel,
    get_channel,
    get_channels_for_agent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent_id(label: bytes = b"agent-a") -> AgentId:
    return AgentId(label)


def make_state_value(fill: int = 0x42) -> StateValue:
    return StateValue(bytes([fill]) * 32)


def make_registry() -> ChannelRegistry:
    return {}


def make_channel_id() -> ChannelId:
    return ChannelId(random_id(16))


def make_channel(
    agent_a: AgentId | None = None,
    agent_b: AgentId | None = None,
    status: ChannelStatus = ChannelStatus.ACTIVE,
    depth: int = 4,
) -> Channel:
    if agent_a is None:
        agent_a = make_agent_id(b"agent-a")
    if agent_b is None:
        agent_b = make_agent_id(b"agent-b")
    return Channel(
        channel_id=make_channel_id(),
        agent_a=agent_a,
        agent_b=agent_b,
        state=ChannelState(local_state=random_state_value(), step=0),
        depth=depth,
        status=status,
    )


# ---------------------------------------------------------------------------
# establish_channel()
# ---------------------------------------------------------------------------

class TestEstablishChannel:
    def test_creates_channel_with_active_status(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        ch = establish_channel(registry, identity, alice, bob)
        assert ch.status == ChannelStatus.ACTIVE

    def test_canonical_agent_ordering_already_sorted(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        # alice < bob lexicographically
        ch = establish_channel(registry, identity, alice, bob)
        assert ch.agent_a == alice
        assert ch.agent_b == bob

    def test_canonical_agent_ordering_reversed(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        # Pass in reverse order — should be swapped
        ch = establish_channel(registry, identity, bob, alice)
        assert ch.agent_a == alice
        assert ch.agent_b == bob

    def test_seed_derivation(self):
        registry = make_registry()
        identity = make_state_value(0x11)
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        ch = establish_channel(registry, identity, alice, bob)

        # Compute expected seed using the same function
        expected_sl0 = ratchet_seed(identity, alice, bob, ch.channel_id)
        assert ch.state.local_state == expected_sl0
        assert ch.state.step == 0

    def test_registration_in_registry(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        ch = establish_channel(registry, identity, alice, bob)
        assert ch.channel_id.value in registry
        assert registry[ch.channel_id.value] is ch

    def test_multiple_channels(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        ch1 = establish_channel(registry, identity, alice, bob)
        ch2 = establish_channel(registry, identity, alice, bob)
        assert len(registry) == 2
        assert ch1.channel_id != ch2.channel_id

    def test_channel_id_is_16_bytes(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        assert len(ch.channel_id.value) == 16

    def test_custom_depth(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
            depth=8,
        )
        assert ch.depth == 8


# ---------------------------------------------------------------------------
# advance_channel()
# ---------------------------------------------------------------------------

class TestAdvanceChannel:
    def test_updates_local_state(self):
        ch = make_channel()
        old_state = ch.state.local_state
        new_state = random_state_value()
        advance_channel(ch, new_state)
        assert ch.state.local_state == new_state
        assert ch.state.local_state != old_state

    def test_increments_step(self):
        ch = make_channel()
        assert ch.state.step == 0
        advance_channel(ch, random_state_value())
        assert ch.state.step == 1
        advance_channel(ch, random_state_value())
        assert ch.state.step == 2

    def test_step_and_state_updated_together(self):
        ch = make_channel()
        new_state = make_state_value(0xFF)
        advance_channel(ch, new_state)
        assert ch.state.local_state == new_state
        assert ch.state.step == 1


# ---------------------------------------------------------------------------
# close_channel()
# ---------------------------------------------------------------------------

class TestCloseChannel:
    def test_zeros_local_state(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        cid = ch.channel_id
        closed = close_channel(registry, cid)
        assert closed.state.local_state == StateValue(b"\x00" * 32)

    def test_removes_from_registry(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        cid = ch.channel_id
        assert cid.value in registry
        close_channel(registry, cid)
        assert cid.value not in registry

    def test_sets_closed_status(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        closed = close_channel(registry, ch.channel_id)
        assert closed.status == ChannelStatus.CLOSED

    def test_preserves_step(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        advance_channel(ch, random_state_value())
        advance_channel(ch, random_state_value())
        step_before = ch.state.step
        closed = close_channel(registry, ch.channel_id)
        assert closed.state.step == step_before

    def test_not_found_raises(self):
        registry = make_registry()
        fake_cid = make_channel_id()
        with pytest.raises(KeyError):
            close_channel(registry, fake_cid)


# ---------------------------------------------------------------------------
# get_channels_for_agent()
# ---------------------------------------------------------------------------

class TestGetChannelsForAgent:
    def test_returns_correct_peers(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        ch = establish_channel(registry, identity, alice, bob)

        alice_channels = get_channels_for_agent(registry, alice)
        assert len(alice_channels) == 1
        assert alice_channels[0].peer == bob
        assert alice_channels[0].channel_id == ch.channel_id
        assert alice_channels[0].status == ChannelStatus.ACTIVE

    def test_agent_b_sees_agent_a_as_peer(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        establish_channel(registry, identity, alice, bob)

        bob_channels = get_channels_for_agent(registry, bob)
        assert len(bob_channels) == 1
        assert bob_channels[0].peer == alice

    def test_filters_by_agent(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        carol = make_agent_id(b"carol")
        establish_channel(registry, identity, alice, bob)
        establish_channel(registry, identity, alice, carol)

        alice_channels = get_channels_for_agent(registry, alice)
        assert len(alice_channels) == 2

        bob_channels = get_channels_for_agent(registry, bob)
        assert len(bob_channels) == 1

        carol_channels = get_channels_for_agent(registry, carol)
        assert len(carol_channels) == 1

    def test_no_channels(self):
        registry = make_registry()
        outsider = make_agent_id(b"outsider")
        result = get_channels_for_agent(registry, outsider)
        assert result == []

    def test_returns_channel_info_type(self):
        registry = make_registry()
        identity = make_state_value()
        alice = make_agent_id(b"alice")
        bob = make_agent_id(b"bob")
        establish_channel(registry, identity, alice, bob)

        channels = get_channels_for_agent(registry, alice)
        assert all(isinstance(ci, ChannelInfo) for ci in channels)


# ---------------------------------------------------------------------------
# get_channel()
# ---------------------------------------------------------------------------

class TestGetChannel:
    def test_found(self):
        registry = make_registry()
        identity = make_state_value()
        ch = establish_channel(
            registry, identity,
            make_agent_id(b"a"), make_agent_id(b"b"),
        )
        result = get_channel(registry, ch.channel_id)
        assert result is ch

    def test_not_found(self):
        registry = make_registry()
        fake_cid = make_channel_id()
        result = get_channel(registry, fake_cid)
        assert result is None
