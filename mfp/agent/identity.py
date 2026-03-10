"""MFP Agent Identity — runtime-assigned, globally unique, unforgeable.

Generates agent_id = SHA-256(runtime_identity || counter || random_suffix).
Opaque to agents. Never reused.

Maps to: impl/I-11_identity.md
"""

from __future__ import annotations

from mfp.core.primitives import encode_u64_be, random_bytes, sha256
from mfp.core.types import AgentId, StateValue


def generate_agent_id(
    runtime_identity: StateValue,
    counter: int,
) -> AgentId:
    """Generate a unique agent identity.

    Components:
        - runtime_identity (32 bytes): cross-runtime uniqueness
        - counter (8 bytes, big-endian): intra-runtime uniqueness
        - random_suffix (8 bytes, CSPRNG): unpredictability

    Output: SHA-256(concatenation) → 32-byte AgentId.

    Maps to: agent-lifecycle.md §4.3.
    """
    counter_bytes = encode_u64_be(counter)
    random_suffix = random_bytes(8)
    preimage = runtime_identity.data + counter_bytes + random_suffix
    digest = sha256(preimage)
    return AgentId(digest.data)
