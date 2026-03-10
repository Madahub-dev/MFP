"""MFP Recovery Protocol — DETECT → NEGOTIATE → RESYNC.

Handles bilateral state divergence between runtimes. Three-phase
protocol with configurable limits and operator escalation.

Maps to: impl/I-18_recovery.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mfp.core.primitives import sha256
from mfp.core.types import (
    BilateralState,
    ChannelId,
    RecoveryMessage,
    StateValue,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecoveryConfig:
    """Recovery protocol configuration."""
    max_step_gap: int = 5         # max step divergence before escalation
    max_attempts: int = 3         # max NEGOTIATE-RESYNC iterations
    timeout_seconds: float = 30   # max time in recovery mode


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

class Diagnosis(Enum):
    """Result of recovery negotiation."""
    SPURIOUS = "spurious"           # same step, same hash — no divergence
    CORRUPTION = "corruption"       # same step, different hash — escalate
    LOCAL_BEHIND = "local_behind"   # local step < peer step
    LOCAL_AHEAD = "local_ahead"     # local step > peer step
    ESCALATE = "escalate"           # gap exceeds max_step_gap


def compute_state_hash(state: BilateralState) -> StateValue:
    """Compute hash of bilateral state for recovery negotiation.

    SHA-256(ratchet_state || shared_prng_seed) — allows comparison
    without revealing actual state values.
    """
    return sha256(state.ratchet_state.data + state.shared_prng_seed.data)


def build_recovery_message(
    channel_id: ChannelId,
    state: BilateralState,
) -> RecoveryMessage:
    """Build a recovery negotiation message."""
    return RecoveryMessage(
        channel_id=channel_id,
        step=state.step,
        state_hash=compute_state_hash(state),
    )


def diagnose_divergence(
    local_step: int,
    peer_step: int,
    local_hash: StateValue,
    peer_hash: StateValue,
    config: RecoveryConfig,
) -> Diagnosis:
    """Diagnose bilateral state divergence from negotiation messages.

    Maps to: I-18 §5.3.
    """
    if local_step == peer_step:
        if local_hash.data == peer_hash.data:
            return Diagnosis.SPURIOUS
        return Diagnosis.CORRUPTION

    gap = abs(local_step - peer_step)
    if gap > config.max_step_gap:
        return Diagnosis.ESCALATE

    if local_step < peer_step:
        return Diagnosis.LOCAL_BEHIND
    return Diagnosis.LOCAL_AHEAD


# ---------------------------------------------------------------------------
# Recovery State
# ---------------------------------------------------------------------------

class RecoveryPhase(Enum):
    """Current phase of recovery protocol."""
    DETECT = "detect"
    NEGOTIATE = "negotiate"
    RESYNC = "resync"
    COMPLETE = "complete"
    ESCALATED = "escalated"


@dataclass
class RecoveryState:
    """Tracks recovery progress for a bilateral channel."""
    bilateral_id: bytes
    phase: RecoveryPhase
    attempt_count: int = 0
    local_step: int = 0
    peer_step: int = 0
    diagnosis: Diagnosis | None = None


def begin_recovery(bilateral_id: bytes, local_step: int) -> RecoveryState:
    """Enter recovery mode for a bilateral channel (Phase 1 — DETECT)."""
    return RecoveryState(
        bilateral_id=bilateral_id,
        phase=RecoveryPhase.DETECT,
        local_step=local_step,
    )


def process_negotiation(
    recovery: RecoveryState,
    peer_msg: RecoveryMessage,
    local_state: BilateralState,
    config: RecoveryConfig,
) -> RecoveryState:
    """Process a recovery negotiation message (Phase 2 — NEGOTIATE).

    Updates recovery state with diagnosis.
    """
    local_hash = compute_state_hash(local_state)
    diagnosis = diagnose_divergence(
        local_step=local_state.step,
        peer_step=peer_msg.step,
        local_hash=local_hash,
        peer_hash=peer_msg.state_hash,
        config=config,
    )

    recovery.peer_step = peer_msg.step
    recovery.diagnosis = diagnosis
    recovery.attempt_count += 1

    if diagnosis == Diagnosis.SPURIOUS:
        recovery.phase = RecoveryPhase.COMPLETE
    elif diagnosis in (Diagnosis.CORRUPTION, Diagnosis.ESCALATE):
        recovery.phase = RecoveryPhase.ESCALATED
    elif recovery.attempt_count > config.max_attempts:
        recovery.phase = RecoveryPhase.ESCALATED
    else:
        recovery.phase = RecoveryPhase.RESYNC

    return recovery


def complete_resync(recovery: RecoveryState) -> RecoveryState:
    """Mark recovery as complete after successful RESYNC (Phase 3)."""
    recovery.phase = RecoveryPhase.COMPLETE
    return recovery
