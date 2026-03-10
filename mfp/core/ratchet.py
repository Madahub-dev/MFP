"""MFP Ratchet Operations — f(), seed(), compose(), bilateral operations.

All functions are pure. They take values, return values, and modify nothing.

Maps to: impl/I-03_ratchet.md
"""

from __future__ import annotations

from collections.abc import Sequence

from mfp.core.primitives import hmac_sha256, sha256
from mfp.core.types import (
    AgentId,
    BilateralState,
    ChannelId,
    Frame,
    GlobalState,
    RuntimeId,
    StateValue,
)


# ---------------------------------------------------------------------------
# Ratchet Advancement
# ---------------------------------------------------------------------------

def advance(state: StateValue, frame: Frame) -> StateValue:
    """Advance ratchet state by one step.

    Computes: Sl' = HMAC-SHA-256(key: Sl, message: frame1 || ... || framek)

    Maps to: spec.md §4.1.
    """
    return hmac_sha256(state.data, frame.to_bytes())


# ---------------------------------------------------------------------------
# Seed Derivation
# ---------------------------------------------------------------------------

def seed(
    runtime_identity: StateValue,
    agent_a: AgentId,
    agent_b: AgentId,
    channel_id: ChannelId,
) -> StateValue:
    """Derive the initial local state Sl0 for a new channel.

    Agents are lexicographically ordered so that seed(A, B, ch) == seed(B, A, ch).

    Maps to: spec.md §4.2.
    """
    id_a, id_b = agent_a.value, agent_b.value
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    message = id_a + id_b + channel_id.value
    return hmac_sha256(runtime_identity.data, message)


# ---------------------------------------------------------------------------
# Global State Composition
# ---------------------------------------------------------------------------

def compose(local_states: Sequence[StateValue]) -> GlobalState:
    """Compute global ratchet state from all contributing states.

    States must be pre-sorted in canonical order by the caller.

    Maps to: spec.md §4.3, §7.2.
    """
    if not local_states:
        raise ValueError("Cannot compose global state from zero states")

    concatenated = b"".join(s.data for s in local_states)
    return GlobalState(value=sha256(concatenated))


def compose_ordered(
    channel_states: Sequence[tuple[ChannelId, StateValue]],
    bilateral_states: Sequence[tuple[RuntimeId, StateValue]] = (),
) -> GlobalState:
    """Compose global state with automatic canonical ordering.

    Sorts channel states by channel_id, then bilateral states by runtime_id.

    Maps to: spec.md §4.3, §7.2.
    """
    if not channel_states and not bilateral_states:
        raise ValueError("Cannot compose global state from zero states")

    sorted_channels = sorted(channel_states, key=lambda pair: pair[0].value)
    sorted_bilateral = sorted(bilateral_states, key=lambda pair: pair[0].value.data)

    ordered = [s for _, s in sorted_channels] + [s for _, s in sorted_bilateral]
    return compose(ordered)


# ---------------------------------------------------------------------------
# Bilateral Seed Derivation
# ---------------------------------------------------------------------------

def bilateral_seed(
    runtime_a: RuntimeId,
    runtime_b: RuntimeId,
) -> BilateralState:
    """Derive the initial bilateral state S_AB0 between two runtimes.

    Lexicographic ordering ensures S_AB0 == S_BA0.

    Maps to: spec.md §7.4.
    """
    id_a, id_b = runtime_a.value.data, runtime_b.value.data
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    message = id_a + id_b

    ratchet_state = hmac_sha256(b"mfp-bilateral", message)
    shared_prng_seed = hmac_sha256(b"mfp-bilateral-prng", message)

    return BilateralState(
        ratchet_state=ratchet_state,
        shared_prng_seed=shared_prng_seed,
        step=0,
    )


# ---------------------------------------------------------------------------
# Bilateral State Advancement
# ---------------------------------------------------------------------------

def bilateral_advance(
    state: BilateralState,
    frame: Frame,
) -> BilateralState:
    """Advance bilateral state after a successful cross-runtime exchange.

    Advances both ratchet_state and shared_prng_seed using the same
    one-way function f().

    Maps to: spec.md §7.5.
    """
    frame_bytes = frame.to_bytes()

    new_ratchet = hmac_sha256(state.ratchet_state.data, frame_bytes)
    new_prng_seed = hmac_sha256(state.shared_prng_seed.data, frame_bytes)

    return BilateralState(
        ratchet_state=new_ratchet,
        shared_prng_seed=new_prng_seed,
        step=state.step + 1,
    )
