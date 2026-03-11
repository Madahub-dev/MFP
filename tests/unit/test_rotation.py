"""Unit tests for bilateral key rotation (P3.3)."""

import time

import pytest

from mfp.core.primitives import random_id
from mfp.core.types import BilateralState, RuntimeId, StateValue
from mfp.federation.rotation import (
    RekeyAccept,
    RekeyRequest,
    RotationConfig,
    RotationReason,
    RotationSession,
    RotationState,
    RotationTrigger,
    derive_rotated_bilateral_state,
)


class TestRotationTrigger:
    """Tests for rotation trigger logic."""

    def test_no_rotation_initially(self):
        """Newly created trigger should not require rotation."""
        trigger = RotationTrigger()
        config = RotationConfig()

        assert trigger.should_rotate(config) is None

    def test_message_count_threshold_triggers_rotation(self):
        """Rotation triggered when message count exceeds threshold."""
        config = RotationConfig(rotation_message_threshold=1000)
        trigger = RotationTrigger()

        # Increment to threshold
        for _ in range(999):
            trigger.increment_message_count()

        assert trigger.should_rotate(config) is None

        # One more message hits threshold
        trigger.increment_message_count()
        assert trigger.should_rotate(config) == RotationReason.MESSAGE_COUNT

    def test_time_threshold_triggers_rotation(self):
        """Rotation triggered when time exceeds threshold."""
        config = RotationConfig(rotation_time_threshold_seconds=0.1)
        trigger = RotationTrigger()

        # Initially no rotation
        assert trigger.should_rotate(config) is None

        # Wait for threshold
        time.sleep(0.15)

        assert trigger.should_rotate(config) == RotationReason.TIME_ELAPSED

    def test_disabled_auto_rotation(self):
        """Rotation not triggered when auto-rotation disabled."""
        config = RotationConfig(
            enable_auto_rotation=False,
            rotation_message_threshold=10,
        )
        trigger = RotationTrigger()

        # Exceed message threshold
        for _ in range(100):
            trigger.increment_message_count()

        assert trigger.should_rotate(config) is None

    def test_reset_clears_counters(self):
        """Reset should clear message count and update time."""
        trigger = RotationTrigger()

        # Increment messages
        for _ in range(100):
            trigger.increment_message_count()

        assert trigger.messages_since_rotation == 100

        # Reset
        trigger.reset()

        assert trigger.messages_since_rotation == 0
        assert trigger.last_rotation_time > 0


class TestRekeyMessages:
    """Tests for rekey protocol message serialization."""

    def test_rekey_request_serialization(self):
        """RekeyRequest should serialize/deserialize correctly."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        ephemeral_key = b"x" * 32
        step = 42

        request = RekeyRequest(
            sender_runtime=runtime_a,
            receiver_runtime=runtime_b,
            ephemeral_public_key=ephemeral_key,
            proposed_step=step,
        )

        # Serialize
        data = request.to_bytes()
        assert len(data) == 104

        # Deserialize
        reconstructed = RekeyRequest.from_bytes(data, runtime_a, runtime_b)
        assert reconstructed.ephemeral_public_key == ephemeral_key
        assert reconstructed.proposed_step == step

    def test_rekey_accept_serialization(self):
        """RekeyAccept should serialize/deserialize correctly."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        ephemeral_key = b"y" * 32
        step = 43

        accept = RekeyAccept(
            sender_runtime=runtime_a,
            receiver_runtime=runtime_b,
            ephemeral_public_key=ephemeral_key,
            accepted_step=step,
        )

        # Serialize
        data = accept.to_bytes()
        assert len(data) == 104

        # Deserialize
        reconstructed = RekeyAccept.from_bytes(data, runtime_a, runtime_b)
        assert reconstructed.ephemeral_public_key == ephemeral_key
        assert reconstructed.accepted_step == step

    def test_rekey_request_wrong_size(self):
        """RekeyRequest should reject wrong-sized data."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        with pytest.raises(ValueError, match="104 bytes"):
            RekeyRequest.from_bytes(b"too short", runtime_a, runtime_b)

    def test_rekey_accept_wrong_size(self):
        """RekeyAccept should reject wrong-sized data."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        with pytest.raises(ValueError, match="104 bytes"):
            RekeyAccept.from_bytes(b"too short", runtime_a, runtime_b)


class TestDeriveRotatedState:
    """Tests for key derivation during rotation."""

    def test_derive_rotated_state_produces_new_keys(self):
        """Rotated state should have different keys than original."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        current_state = BilateralState(
            ratchet_state=StateValue(b"old_ratchet" + b"\x00" * 21),
            shared_prng_seed=StateValue(b"old_prng" + b"\x00" * 24),
            step=100,
        )

        shared_secret = b"shared_secret" + b"\x00" * 19

        rotated = derive_rotated_bilateral_state(
            current_state,
            shared_secret,
            runtime_a,
            runtime_b,
        )

        # New state has different keys
        assert rotated.ratchet_state != current_state.ratchet_state
        assert rotated.shared_prng_seed != current_state.shared_prng_seed

        # Step is preserved
        assert rotated.step == current_state.step

    def test_derive_rotated_state_is_deterministic(self):
        """Same inputs produce same rotated state."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        current_state = BilateralState(
            ratchet_state=StateValue(b"ratchet" + b"\x00" * 25),
            shared_prng_seed=StateValue(b"prng" + b"\x00" * 28),
            step=50,
        )

        shared_secret = b"secret" + b"\x00" * 26

        rotated1 = derive_rotated_bilateral_state(
            current_state, shared_secret, runtime_a, runtime_b
        )
        rotated2 = derive_rotated_bilateral_state(
            current_state, shared_secret, runtime_a, runtime_b
        )

        assert rotated1.ratchet_state == rotated2.ratchet_state
        assert rotated1.shared_prng_seed == rotated2.shared_prng_seed
        assert rotated1.step == rotated2.step

    def test_derive_rotated_state_is_symmetric(self):
        """Runtime order should not affect result (commutative)."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        current_state = BilateralState(
            ratchet_state=StateValue(b"ratchet" + b"\x00" * 25),
            shared_prng_seed=StateValue(b"prng" + b"\x00" * 28),
            step=50,
        )

        shared_secret = b"secret" + b"\x00" * 26

        # A's perspective
        rotated_a = derive_rotated_bilateral_state(
            current_state, shared_secret, runtime_a, runtime_b
        )

        # B's perspective (swapped order)
        rotated_b = derive_rotated_bilateral_state(
            current_state, shared_secret, runtime_b, runtime_a
        )

        assert rotated_a.ratchet_state == rotated_b.ratchet_state
        assert rotated_a.shared_prng_seed == rotated_b.shared_prng_seed


class TestRotationSession:
    """Tests for rotation session state machine."""

    def test_session_starts_idle(self):
        """New session should be in IDLE state."""
        session = RotationSession()
        assert session.state == RotationState.IDLE
        assert session.local_private_key is None
        assert session.peer_public_key is None

    def test_generate_keypair(self):
        """Session should generate valid X25519 keypair."""
        session = RotationSession()

        private_key, public_key = session.generate_keypair()

        assert len(private_key) == 32
        assert len(public_key) == 32
        assert session.local_private_key == private_key
        assert session.local_public_key == public_key

    def test_compute_shared_secret(self):
        """Session should compute valid DH shared secret."""
        # Create two sessions (Alice and Bob)
        alice = RotationSession()
        bob = RotationSession()

        alice_private, alice_public = alice.generate_keypair()
        bob_private, bob_public = bob.generate_keypair()

        # Compute shared secrets
        alice_secret = alice.compute_shared_secret(bob_public)
        bob_secret = bob.compute_shared_secret(alice_public)

        # Shared secrets should match (DH property)
        assert alice_secret == bob_secret
        assert len(alice_secret) == 32

        # Session state updated
        assert alice.shared_secret == alice_secret
        assert alice.peer_public_key == bob_public

    def test_compute_shared_secret_without_keypair_fails(self):
        """Computing shared secret without local keypair should fail."""
        session = RotationSession()
        peer_public = b"x" * 32

        with pytest.raises(ValueError, match="Local private key not generated"):
            session.compute_shared_secret(peer_public)

    def test_reset_clears_session(self):
        """Reset should clear all session state."""
        session = RotationSession()

        # Populate session
        session.generate_keypair()
        session.state = RotationState.INITIATING
        session.rotation_step = 100

        # Reset
        session.reset()

        assert session.state == RotationState.IDLE
        assert session.local_private_key is None
        assert session.local_public_key is None
        assert session.rotation_step is None
        assert session.shared_secret is None
