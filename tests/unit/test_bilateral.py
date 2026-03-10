"""Unit tests for mfp/federation/bilateral.py (I-15)."""

import pytest

from mfp.core.primitives import hmac_sha256
from mfp.core.ratchet import bilateral_seed
from mfp.core.types import (
    BilateralState,
    Block,
    ChannelId,
    ChannelState,
    Frame,
    RuntimeId,
    StateValue,
)
from mfp.federation.bilateral import (
    BilateralChannel,
    CrossRuntimeChannel,
    PendingAdvance,
    advance_bilateral_state,
    bootstrap_ceremonial,
    bootstrap_deterministic,
    compute_shared_secret,
    derive_bilateral_id,
    generate_dh_keypair,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runtime_id(n: int) -> RuntimeId:
    return RuntimeId(value=StateValue(bytes([n]) * 32))


def _frame(depth: int = 2) -> Frame:
    return Frame(tuple(Block(bytes([i]) * 16) for i in range(depth)))


# ---------------------------------------------------------------------------
# bootstrap_deterministic
# ---------------------------------------------------------------------------

class TestBootstrapDeterministic:
    def test_returns_bilateral_state(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        assert isinstance(state, BilateralState)
        assert state.step == 0

    def test_symmetric(self):
        """S_AB₀ == S_BA₀ regardless of argument order."""
        s_ab = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        s_ba = bootstrap_deterministic(_runtime_id(2), _runtime_id(1))
        assert s_ab == s_ba

    def test_different_pairs_differ(self):
        s1 = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        s2 = bootstrap_deterministic(_runtime_id(1), _runtime_id(3))
        assert s1.ratchet_state != s2.ratchet_state

    def test_delegates_to_bilateral_seed(self):
        """Should produce same result as core ratchet bilateral_seed."""
        r_a, r_b = _runtime_id(1), _runtime_id(2)
        assert bootstrap_deterministic(r_a, r_b) == bilateral_seed(r_a, r_b)


# ---------------------------------------------------------------------------
# DH key exchange
# ---------------------------------------------------------------------------

class TestDHKeyExchange:
    def test_generate_keypair_sizes(self):
        priv, pub = generate_dh_keypair()
        assert len(priv) == 32
        assert len(pub) == 32

    def test_shared_secret_symmetric(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret_ab = compute_shared_secret(priv_a, pub_b)
        secret_ba = compute_shared_secret(priv_b, pub_a)
        assert secret_ab == secret_ba

    def test_shared_secret_length(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret = compute_shared_secret(priv_a, pub_b)
        assert len(secret) == 32

    def test_different_pairs_different_secrets(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        priv_c, pub_c = generate_dh_keypair()
        s1 = compute_shared_secret(priv_a, pub_b)
        s2 = compute_shared_secret(priv_a, pub_c)
        assert s1 != s2


# ---------------------------------------------------------------------------
# bootstrap_ceremonial
# ---------------------------------------------------------------------------

class TestBootstrapCeremonial:
    def test_returns_bilateral_state(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret = compute_shared_secret(priv_a, pub_b)
        state = bootstrap_ceremonial(_runtime_id(1), _runtime_id(2), secret)
        assert isinstance(state, BilateralState)
        assert state.step == 0

    def test_symmetric_with_same_secret(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret = compute_shared_secret(priv_a, pub_b)
        s_ab = bootstrap_ceremonial(_runtime_id(1), _runtime_id(2), secret)
        s_ba = bootstrap_ceremonial(_runtime_id(2), _runtime_id(1), secret)
        assert s_ab == s_ba

    def test_different_from_deterministic(self):
        priv_a, pub_a = generate_dh_keypair()
        priv_b, pub_b = generate_dh_keypair()
        secret = compute_shared_secret(priv_a, pub_b)
        s_det = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        s_cer = bootstrap_ceremonial(_runtime_id(1), _runtime_id(2), secret)
        assert s_det != s_cer

    def test_different_secrets_different_states(self):
        s1 = bootstrap_ceremonial(_runtime_id(1), _runtime_id(2), b"\x01" * 32)
        s2 = bootstrap_ceremonial(_runtime_id(1), _runtime_id(2), b"\x02" * 32)
        assert s1 != s2


# ---------------------------------------------------------------------------
# advance_bilateral_state
# ---------------------------------------------------------------------------

class TestAdvanceBilateralState:
    def test_step_increments(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        advanced = advance_bilateral_state(state, _frame())
        assert advanced.step == 1

    def test_ratchet_state_changes(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        advanced = advance_bilateral_state(state, _frame())
        assert advanced.ratchet_state != state.ratchet_state

    def test_prng_seed_changes(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        advanced = advance_bilateral_state(state, _frame())
        assert advanced.shared_prng_seed != state.shared_prng_seed

    def test_deterministic(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        a1 = advance_bilateral_state(state, _frame())
        a2 = advance_bilateral_state(state, _frame())
        assert a1 == a2

    def test_different_frames_different_states(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        a1 = advance_bilateral_state(state, _frame(2))
        a2 = advance_bilateral_state(state, _frame(3))
        assert a1 != a2

    def test_chain_multiple_advances(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        for i in range(5):
            state = advance_bilateral_state(state, _frame())
        assert state.step == 5


# ---------------------------------------------------------------------------
# derive_bilateral_id
# ---------------------------------------------------------------------------

class TestDeriveBilateralId:
    def test_returns_32_bytes(self):
        bid = derive_bilateral_id(_runtime_id(1), _runtime_id(2))
        assert len(bid) == 32

    def test_symmetric(self):
        bid_ab = derive_bilateral_id(_runtime_id(1), _runtime_id(2))
        bid_ba = derive_bilateral_id(_runtime_id(2), _runtime_id(1))
        assert bid_ab == bid_ba

    def test_different_pairs_differ(self):
        bid1 = derive_bilateral_id(_runtime_id(1), _runtime_id(2))
        bid2 = derive_bilateral_id(_runtime_id(1), _runtime_id(3))
        assert bid1 != bid2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class TestDataClasses:
    def test_pending_advance(self):
        state = bootstrap_deterministic(_runtime_id(1), _runtime_id(2))
        pa = PendingAdvance(
            bilateral_id=b"\x00" * 32,
            pre_advance_state=state,
            expected_post_state=advance_bilateral_state(state, _frame()),
            frame=_frame(),
            step=0,
        )
        assert pa.step == 0

    def test_cross_runtime_channel(self):
        crc = CrossRuntimeChannel(
            channel_id=ChannelId(b"\x01" * 16),
            local_agent_id=b"\x02" * 32,
            remote_agent_id=b"\x03" * 32,
            bilateral_id=b"\x04" * 32,
            state=ChannelState(local_state=StateValue(b"\x05" * 32), step=0),
            depth=4,
        )
        assert crc.depth == 4

    def test_bilateral_channel(self):
        bc = BilateralChannel(
            bilateral_id=b"\x00" * 32,
            local_runtime=_runtime_id(1),
            peer_runtime=_runtime_id(2),
            state=bootstrap_deterministic(_runtime_id(1), _runtime_id(2)),
        )
        assert bc.status == "active"
        assert bc.pending is None
        assert bc.cross_runtime_channels == []
