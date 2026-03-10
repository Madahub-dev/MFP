"""MFP Channel Management — establishment, advancement, teardown, queries.

Functions that operate on the channel registry. No class, no owned state.
The Runtime passes its _channels dict as the registry argument.

Maps to: impl/I-08_channels.md
"""

from __future__ import annotations

from mfp.core.primitives import random_id
from mfp.core.ratchet import seed
from mfp.core.types import (
    AgentId,
    Channel,
    ChannelId,
    ChannelInfo,
    ChannelState,
    ChannelStatus,
    StateValue,
    DEFAULT_FRAME_DEPTH,
)

ChannelRegistry = dict[bytes, Channel]


# ---------------------------------------------------------------------------
# Establishment
# ---------------------------------------------------------------------------

def establish_channel(
    registry: ChannelRegistry,
    runtime_identity: StateValue,
    agent_a: AgentId,
    agent_b: AgentId,
    depth: int = DEFAULT_FRAME_DEPTH,
) -> Channel:
    """Create and register a new channel between two agents.

    Agents are stored in canonical (lexicographic) order.

    Maps to: runtime-interface.md §6.2.
    """
    channel_id = ChannelId(random_id(16))

    # Canonical ordering
    if agent_a.value > agent_b.value:
        agent_a, agent_b = agent_b, agent_a

    sl0 = seed(runtime_identity, agent_a, agent_b, channel_id)

    channel = Channel(
        channel_id=channel_id,
        agent_a=agent_a,
        agent_b=agent_b,
        state=ChannelState(local_state=sl0, step=0),
        depth=depth,
        status=ChannelStatus.ACTIVE,
    )

    registry[channel_id.value] = channel
    return channel


# ---------------------------------------------------------------------------
# State Advancement
# ---------------------------------------------------------------------------

def advance_channel(channel: Channel, new_local_state: StateValue) -> None:
    """Advance a channel's ratchet state after successful exchange.

    Replaces Sl and increments step.

    Maps to: spec.md §4.4, runtime-interface.md §9.2.
    """
    channel.state = ChannelState(
        local_state=new_local_state,
        step=channel.state.step + 1,
    )


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def close_channel(registry: ChannelRegistry, channel_id: ChannelId) -> Channel:
    """Close a channel and zero its ratchet state.

    Removes from registry after zeroing Sl.

    Maps to: runtime-interface.md §6.4.
    """
    channel = registry.pop(channel_id.value)
    channel.status = ChannelStatus.CLOSED
    # Zero Sl — security requirement
    channel.state = ChannelState(
        local_state=StateValue(b"\x00" * 32),
        step=channel.state.step,
    )
    return channel


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_channels_for_agent(
    registry: ChannelRegistry,
    agent_id: AgentId,
) -> list[ChannelInfo]:
    """Return agent-visible channel information.

    Maps to: runtime-interface.md §3.2 (mfp_channels).
    """
    results: list[ChannelInfo] = []
    for channel in registry.values():
        if agent_id == channel.agent_a:
            results.append(ChannelInfo(
                channel_id=channel.channel_id,
                peer=channel.agent_b,
                status=channel.status,
            ))
        elif agent_id == channel.agent_b:
            results.append(ChannelInfo(
                channel_id=channel.channel_id,
                peer=channel.agent_a,
                status=channel.status,
            ))
    return results


def get_channel(registry: ChannelRegistry, channel_id: ChannelId) -> Channel | None:
    """Look up a channel by ID. Returns None if not found."""
    return registry.get(channel_id.value)
