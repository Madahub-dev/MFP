"""MFP Quarantine Engine — isolation without state destruction.

Functions that apply quarantine/restoration effects. No class, no owned state.
The Runtime passes state as arguments.

Maps to: impl/I-09_quarantine.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mfp.core.types import AgentState, Channel, ChannelId, ChannelStatus

if TYPE_CHECKING:
    from mfp.runtime.runtime import AgentRecord, ChannelRegistry


# ---------------------------------------------------------------------------
# Failure Tracking
# ---------------------------------------------------------------------------

def increment_failure_count(channel: Channel) -> None:
    """Increment a channel's consecutive validation failure counter."""
    channel.validation_failure_count += 1


def reset_failure_count(channel: Channel) -> None:
    """Reset after a successful exchange. Tracks consecutive failures only."""
    channel.validation_failure_count = 0


def check_validation_failure(channel: Channel, threshold: int) -> bool:
    """True if channel should be quarantined due to consecutive failures."""
    return channel.validation_failure_count >= threshold


def check_rate_limit(message_count: int, max_rate: int) -> bool:
    """True if agent has exceeded its message rate limit. 0 = unlimited."""
    if max_rate == 0:
        return False
    return message_count > max_rate


# ---------------------------------------------------------------------------
# Quarantine Effects
# ---------------------------------------------------------------------------

def quarantine_channel(channel: Channel, reason: str = "") -> None:
    """Quarantine a channel. Sl and t are frozen (not zeroed). Sg unaffected.

    Maps to: runtime-interface.md §8.3.
    """
    channel.status = ChannelStatus.QUARANTINED


def quarantine_agent(
    agent: AgentRecord,
    channels: ChannelRegistry,
    reason: str = "",
) -> list[ChannelId]:
    """Quarantine an agent and all its active channels.

    Returns list of channels that were quarantined.

    Maps to: runtime-interface.md §8.3.
    """
    agent.state = AgentState.QUARANTINED
    agent.quarantine_reason = reason

    quarantined: list[ChannelId] = []
    for ch_id_bytes in agent.channels:
        channel = channels.get(ch_id_bytes)
        if channel and channel.status == ChannelStatus.ACTIVE:
            quarantine_channel(channel, reason)
            quarantined.append(channel.channel_id)

    return quarantined


# ---------------------------------------------------------------------------
# Restoration
# ---------------------------------------------------------------------------

def restore_channel(channel: Channel) -> None:
    """Restore a quarantined channel. Resumes from current Sl and t.

    Maps to: runtime-interface.md §8.4.
    """
    if channel.status != ChannelStatus.QUARANTINED:
        raise ValueError(f"Cannot restore channel in status {channel.status.value}")
    channel.status = ChannelStatus.ACTIVE
    channel.validation_failure_count = 0


def restore_agent(
    agent: AgentRecord,
    channels: ChannelRegistry,
) -> list[ChannelId]:
    """Restore a quarantined agent and its channels.

    Channels are restored unconditionally here. The Runtime applies
    peer checks (if the other agent is quarantined, the channel stays quarantined).

    Maps to: runtime-interface.md §8.4.
    """
    if agent.state != AgentState.QUARANTINED:
        raise ValueError(f"Cannot restore agent in state {agent.state.value}")

    agent.state = AgentState.ACTIVE
    agent.quarantine_reason = ""

    restored: list[ChannelId] = []
    for ch_id_bytes in agent.channels:
        channel = channels.get(ch_id_bytes)
        if channel and channel.status == ChannelStatus.QUARANTINED:
            restore_channel(channel)
            restored.append(channel.channel_id)

    return restored
