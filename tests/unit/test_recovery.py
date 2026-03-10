"""Unit tests for mfp/federation/recovery.py (I-18)."""

import pytest

from mfp.core.types import (
    BilateralState,
    ChannelId,
    RecoveryMessage,
    StateValue,
)
from mfp.federation.bilateral import bootstrap_deterministic
from mfp.federation.recovery import (
    Diagnosis,
    RecoveryConfig,
    RecoveryPhase,
    RecoveryState,
    begin_recovery,
    build_recovery_message,
    complete_resync,
    compute_state_hash,
    diagnose_divergence,
    process_negotiation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runtime_id(n: int):
    from mfp.core.types import RuntimeId
    return RuntimeId(value=StateValue(bytes([n]) * 32))


def _state() -> BilateralState:
    return bootstrap_deterministic(_runtime_id(1), _runtime_id(2))


def _channel_id() -> ChannelId:
    return ChannelId(b"\x01" * 16)


def _config(**kwargs) -> RecoveryConfig:
    return RecoveryConfig(**kwargs)


# ---------------------------------------------------------------------------
# compute_state_hash
# ---------------------------------------------------------------------------

class TestComputeStateHash:
    def test_returns_state_value(self):
        h = compute_state_hash(_state())
        assert isinstance(h, StateValue)
        assert len(h.data) == 32

    def test_deterministic(self):
        s = _state()
        assert compute_state_hash(s) == compute_state_hash(s)

    def test_different_states_different_hashes(self):
        s1 = _state()
        s2 = bootstrap_deterministic(_runtime_id(1), _runtime_id(3))
        assert compute_state_hash(s1) != compute_state_hash(s2)


# ---------------------------------------------------------------------------
# build_recovery_message
# ---------------------------------------------------------------------------

class TestBuildRecoveryMessage:
    def test_structure(self):
        msg = build_recovery_message(_channel_id(), _state())
        assert isinstance(msg, RecoveryMessage)
        assert msg.channel_id == _channel_id()
        assert msg.step == 0
        assert isinstance(msg.state_hash, StateValue)


# ---------------------------------------------------------------------------
# diagnose_divergence
# ---------------------------------------------------------------------------

class TestDiagnoseDivergence:
    def test_spurious(self):
        h = compute_state_hash(_state())
        d = diagnose_divergence(5, 5, h, h, _config())
        assert d == Diagnosis.SPURIOUS

    def test_corruption(self):
        h1 = StateValue(b"\x01" * 32)
        h2 = StateValue(b"\x02" * 32)
        d = diagnose_divergence(5, 5, h1, h2, _config())
        assert d == Diagnosis.CORRUPTION

    def test_local_behind(self):
        h1, h2 = StateValue(b"\x01" * 32), StateValue(b"\x02" * 32)
        d = diagnose_divergence(3, 5, h1, h2, _config(max_step_gap=10))
        assert d == Diagnosis.LOCAL_BEHIND

    def test_local_ahead(self):
        h1, h2 = StateValue(b"\x01" * 32), StateValue(b"\x02" * 32)
        d = diagnose_divergence(7, 5, h1, h2, _config(max_step_gap=10))
        assert d == Diagnosis.LOCAL_AHEAD

    def test_escalate_when_gap_exceeds_max(self):
        h1, h2 = StateValue(b"\x01" * 32), StateValue(b"\x02" * 32)
        d = diagnose_divergence(0, 10, h1, h2, _config(max_step_gap=5))
        assert d == Diagnosis.ESCALATE

    def test_gap_at_boundary_not_escalated(self):
        """Gap == max_step_gap should NOT escalate (only > does)."""
        h1, h2 = StateValue(b"\x01" * 32), StateValue(b"\x02" * 32)
        d = diagnose_divergence(0, 5, h1, h2, _config(max_step_gap=5))
        assert d == Diagnosis.LOCAL_BEHIND

    def test_gap_above_boundary_escalated(self):
        h1, h2 = StateValue(b"\x01" * 32), StateValue(b"\x02" * 32)
        d = diagnose_divergence(0, 6, h1, h2, _config(max_step_gap=5))
        assert d == Diagnosis.ESCALATE


# ---------------------------------------------------------------------------
# begin_recovery
# ---------------------------------------------------------------------------

class TestBeginRecovery:
    def test_initial_phase_is_detect(self):
        rs = begin_recovery(b"\x00" * 32, local_step=10)
        assert rs.phase == RecoveryPhase.DETECT
        assert rs.local_step == 10
        assert rs.attempt_count == 0


# ---------------------------------------------------------------------------
# process_negotiation
# ---------------------------------------------------------------------------

class TestProcessNegotiation:
    def test_spurious_completes(self):
        """Same step + same hash → COMPLETE."""
        state = _state()
        rs = begin_recovery(b"\x00" * 32, local_step=state.step)
        msg = build_recovery_message(_channel_id(), state)
        result = process_negotiation(rs, msg, state, _config())
        assert result.phase == RecoveryPhase.COMPLETE
        assert result.diagnosis == Diagnosis.SPURIOUS

    def test_corruption_escalates(self):
        """Same step + different hash → ESCALATED."""
        state = _state()
        rs = begin_recovery(b"\x00" * 32, local_step=state.step)
        # Build a message with a different hash
        fake_hash = StateValue(b"\xff" * 32)
        msg = RecoveryMessage(channel_id=_channel_id(), step=0, state_hash=fake_hash)
        result = process_negotiation(rs, msg, state, _config())
        assert result.phase == RecoveryPhase.ESCALATED
        assert result.diagnosis == Diagnosis.CORRUPTION

    def test_local_behind_enters_resync(self):
        state = _state()
        rs = begin_recovery(b"\x00" * 32, local_step=0)
        msg = RecoveryMessage(
            channel_id=_channel_id(), step=3,
            state_hash=StateValue(b"\xaa" * 32),
        )
        result = process_negotiation(rs, msg, state, _config(max_step_gap=10))
        assert result.phase == RecoveryPhase.RESYNC
        assert result.diagnosis == Diagnosis.LOCAL_BEHIND

    def test_max_attempts_exceeded_escalates(self):
        state = _state()
        rs = begin_recovery(b"\x00" * 32, local_step=0)
        config = _config(max_attempts=1, max_step_gap=100)
        msg = RecoveryMessage(
            channel_id=_channel_id(), step=2,
            state_hash=StateValue(b"\xbb" * 32),
        )
        # First attempt → RESYNC
        rs = process_negotiation(rs, msg, state, config)
        assert rs.phase == RecoveryPhase.RESYNC
        # Second attempt → over max_attempts → ESCALATED
        rs = process_negotiation(rs, msg, state, config)
        assert rs.phase == RecoveryPhase.ESCALATED

    def test_attempt_count_increments(self):
        state = _state()
        rs = begin_recovery(b"\x00" * 32, local_step=0)
        msg = RecoveryMessage(
            channel_id=_channel_id(), step=2,
            state_hash=StateValue(b"\xcc" * 32),
        )
        config = _config(max_step_gap=100, max_attempts=10)
        for i in range(3):
            rs = process_negotiation(rs, msg, state, config)
        assert rs.attempt_count == 3


# ---------------------------------------------------------------------------
# complete_resync
# ---------------------------------------------------------------------------

class TestCompleteResync:
    def test_marks_complete(self):
        rs = RecoveryState(
            bilateral_id=b"\x00" * 32,
            phase=RecoveryPhase.RESYNC,
            attempt_count=1,
        )
        result = complete_resync(rs)
        assert result.phase == RecoveryPhase.COMPLETE


# ---------------------------------------------------------------------------
# RecoveryConfig defaults
# ---------------------------------------------------------------------------

class TestRecoveryConfig:
    def test_defaults(self):
        c = RecoveryConfig()
        assert c.max_step_gap == 5
        assert c.max_attempts == 3
        assert c.timeout_seconds == 30
