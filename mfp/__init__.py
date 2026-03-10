"""Mirror Frame Protocol — symmetric frame envelope for LLM agent communication.

Library API:
    from mfp import Runtime, RuntimeConfig, bind, unbind
    from mfp import mfp_send, mfp_channels, mfp_status

Maps to: impl/I-19_api.md
"""

from mfp.agent.lifecycle import AgentHandle, bind, unbind
from mfp.agent.tools import mfp_channels, mfp_send, mfp_status
from mfp.core.types import (
    AgentError,
    AgentErrorCode,
    AgentId,
    AgentStatus,
    ChannelId,
    ChannelInfo,
    Receipt,
)
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime

__version__ = "0.1.0"

__all__ = [
    # Core
    "Runtime",
    "RuntimeConfig",
    # Agent lifecycle
    "AgentHandle",
    "bind",
    "unbind",
    # Protocol tools
    "mfp_send",
    "mfp_channels",
    "mfp_status",
    # Types
    "AgentId",
    "AgentStatus",
    "AgentError",
    "AgentErrorCode",
    "ChannelId",
    "ChannelInfo",
    "Receipt",
]
