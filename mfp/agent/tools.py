"""MFP Protocol Tools — the agent's only interface to the protocol.

Three tools: mfp_send, mfp_channels, mfp_status.
These are named functions that delegate to AgentHandle methods.

Maps to: impl/I-12_tools.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mfp.core.types import AgentStatus, ChannelId, ChannelInfo, Receipt

if TYPE_CHECKING:
    from mfp.agent.lifecycle import AgentHandle


def mfp_send(
    handle: AgentHandle,
    channel_id: ChannelId,
    payload: bytes,
) -> Receipt:
    """Send a message on a channel.

    Requires ACTIVE state. Returns receipt on success.

    Maps to: runtime-interface.md §3.1.
    """
    return handle.send(channel_id, payload)


def mfp_channels(handle: AgentHandle) -> list[ChannelInfo]:
    """List channels visible to this agent.

    Requires ACTIVE state.

    Maps to: runtime-interface.md §3.2.
    """
    return handle.channels()


def mfp_status(handle: AgentHandle) -> AgentStatus:
    """Query own lifecycle status.

    Requires BOUND or ACTIVE state.

    Maps to: runtime-interface.md §3.3.
    """
    return handle.status()
