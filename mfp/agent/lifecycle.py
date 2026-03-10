"""MFP Agent Lifecycle — state machine, AgentHandle, bind/unbind.

The AgentHandle is the agent's sole interface to the protocol. It wraps
Runtime Layer 1 methods with state-based access control.

Maps to: impl/I-10_lifecycle.md
"""

from __future__ import annotations

from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
    AgentState,
    AgentStatus,
    ChannelId,
    ChannelInfo,
    Receipt,
)
from mfp.runtime.pipeline import AgentCallable
from mfp.runtime.runtime import Runtime


class AgentHandle:
    """Agent's interface to the MFP runtime.

    Wraps Runtime Layer 1 methods with state-based access control.
    Agents interact with the protocol exclusively through this handle.

    Maps to: I-10 §10.
    """

    def __init__(self, runtime: Runtime, agent_id: AgentId) -> None:
        self._runtime = runtime
        self._agent_id = agent_id

    @property
    def agent_id(self) -> AgentId:
        """This agent's runtime-assigned identity."""
        return self._agent_id

    def send(self, channel_id: ChannelId, payload: bytes) -> Receipt:
        """Send a message on a channel. Requires ACTIVE state.

        The sender is implicit — this handle's agent_id is used.
        Prevents identity spoofing.

        Maps to: runtime-interface.md §3.1.
        """
        self._require_state(AgentState.ACTIVE)
        return self._runtime.send(self._agent_id, channel_id, payload)

    def channels(self) -> list[ChannelInfo]:
        """List channels visible to this agent. Requires ACTIVE state.

        Maps to: runtime-interface.md §3.2.
        """
        self._require_state(AgentState.ACTIVE)
        return self._runtime.get_channels(self._agent_id)

    def status(self) -> AgentStatus:
        """Query own lifecycle status. Requires BOUND or ACTIVE state.

        Maps to: runtime-interface.md §3.3.
        """
        self._require_state(AgentState.BOUND, AgentState.ACTIVE)
        return self._runtime.get_status(self._agent_id)

    def _require_state(self, *allowed: AgentState) -> None:
        """Check that the agent is in one of the allowed states.

        Raises AgentError with appropriate code if not.
        """
        record = self._runtime._agents.get(self._agent_id.value)
        if record is None:
            raise AgentError(AgentErrorCode.UNBOUND, "Agent not bound")
        if record.state == AgentState.QUARANTINED:
            raise AgentError(AgentErrorCode.QUARANTINED, "Agent is quarantined")
        if record.state not in allowed:
            raise AgentError(
                AgentErrorCode.UNBOUND,
                "Operation not available in current state",
            )


# ---------------------------------------------------------------------------
# Bind / Unbind
# ---------------------------------------------------------------------------

def bind(runtime: Runtime, agent_callable: AgentCallable) -> AgentHandle:
    """Bind a new agent to the runtime and return its handle.

    Atomic: either the agent is fully bound (BOUND state) and the
    handle is returned, or no state change occurs.

    Maps to: I-10 §5.
    """
    agent_id = runtime.bind_agent(agent_callable)
    return AgentHandle(runtime, agent_id)


def unbind(handle: AgentHandle) -> None:
    """Unbind an agent from the runtime.

    Closes all channels, zeros state, invalidates the handle.

    Maps to: I-10 §8.
    """
    handle._runtime.unbind_agent(handle._agent_id)
