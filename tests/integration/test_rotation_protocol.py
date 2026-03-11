"""Integration tests for bilateral key rotation protocol (P3.3)."""

import pytest

from mfp.core.ratchet import bilateral_seed
from mfp.core.types import BilateralState, RuntimeId, StateValue
from mfp.federation.bilateral import BilateralChannel, derive_bilateral_id
from mfp.federation.rotation import (
    RekeyAccept,
    RekeyRequest,
    RotationConfig,
    RotationReason,
    RotationState,
    derive_rotated_bilateral_state,
)


class TestRotationProtocol:
    """Integration tests for full rotation protocol."""

    def test_full_rotation_handshake(self):
        """Complete rotation handshake between two channels."""
        # Setup two runtimes with bilateral channel
        runtime_a = RuntimeId(value=StateValue(b"runtime_a" + b"\x00" * 23))
        runtime_b = RuntimeId(value=StateValue(b"runtime_b" + b"\x00" * 23))

        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        initial_state = bilateral_seed(runtime_a, runtime_b)

        # Create bilateral channels for both runtimes
        channel_a = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=initial_state,
        )

        channel_b = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_b,
            peer_runtime=runtime_a,
            state=initial_state,
        )

        # Configure rotation with low threshold for testing
        config = RotationConfig(rotation_message_threshold=100)
        channel_a.configure_rotation(config)
        channel_b.configure_rotation(config)

        # Simulate 100 bilateral exchanges
        for _ in range(100):
            channel_a.increment_message_count()
            channel_b.increment_message_count()

        # Both should need rotation
        assert channel_a.should_rotate()
        assert channel_b.should_rotate()

        # Step 1: Runtime A initiates rotation
        session_a = channel_a.get_rotation_session()
        session_a.state = RotationState.INITIATING

        # A generates ephemeral keypair
        private_a, public_a = session_a.generate_keypair()
        assert len(private_a) == 32
        assert len(public_a) == 32

        # A sends REKEY_REQUEST to B
        proposed_step = channel_a.state.step + 1
        session_a.rotation_step = proposed_step

        rekey_request = RekeyRequest(
            sender_runtime=runtime_a,
            receiver_runtime=runtime_b,
            ephemeral_public_key=public_a,
            proposed_step=proposed_step,
        )

        # Step 2: Runtime B receives REKEY_REQUEST
        session_b = channel_b.get_rotation_session()
        session_b.state = RotationState.RESPONDING

        # B generates ephemeral keypair
        private_b, public_b = session_b.generate_keypair()

        # B computes shared secret
        shared_secret_b = session_b.compute_shared_secret(rekey_request.ephemeral_public_key)

        # B derives new bilateral state
        new_state_b = derive_rotated_bilateral_state(
            channel_b.state,
            shared_secret_b,
            runtime_a,
            runtime_b,
        )
        session_b.new_bilateral_state = new_state_b
        session_b.rotation_step = rekey_request.proposed_step

        # B sends REKEY_ACCEPT to A
        rekey_accept = RekeyAccept(
            sender_runtime=runtime_b,
            receiver_runtime=runtime_a,
            ephemeral_public_key=public_b,
            accepted_step=rekey_request.proposed_step,
        )

        # Step 3: Runtime A receives REKEY_ACCEPT
        # A computes shared secret
        shared_secret_a = session_a.compute_shared_secret(rekey_accept.ephemeral_public_key)

        # Shared secrets should match (DH property)
        assert shared_secret_a == shared_secret_b

        # A derives new bilateral state
        new_state_a = derive_rotated_bilateral_state(
            channel_a.state,
            shared_secret_a,
            runtime_a,
            runtime_b,
        )
        session_a.new_bilateral_state = new_state_a

        # Both sides now have new state
        assert new_state_a.ratchet_state == new_state_b.ratchet_state
        assert new_state_a.shared_prng_seed == new_state_b.shared_prng_seed

        # New state differs from old state
        assert new_state_a.ratchet_state != initial_state.ratchet_state
        assert new_state_a.shared_prng_seed != initial_state.shared_prng_seed

        # Step counter preserved
        assert new_state_a.step == initial_state.step

        # Step 4: Both sides switch to new state at agreed step
        channel_a.state = new_state_a
        channel_b.state = new_state_b

        # Reset rotation state
        channel_a.get_rotation_trigger().reset()
        channel_b.get_rotation_trigger().reset()

        session_a.reset()
        session_b.reset()

        # Verify rotation completed
        assert session_a.state == RotationState.IDLE
        assert session_b.state == RotationState.IDLE

        assert channel_a.get_rotation_trigger().messages_since_rotation == 0
        assert channel_b.get_rotation_trigger().messages_since_rotation == 0

    def test_rotation_handshake_message_serialization(self):
        """Rotation messages serialize correctly over wire."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        # Generate keypair
        channel_a = BilateralChannel(
            bilateral_id=derive_bilateral_id(runtime_a, runtime_b),
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=bilateral_seed(runtime_a, runtime_b),
        )

        session = channel_a.get_rotation_session()
        _, public_key = session.generate_keypair()

        # Create request
        request = RekeyRequest(
            sender_runtime=runtime_a,
            receiver_runtime=runtime_b,
            ephemeral_public_key=public_key,
            proposed_step=42,
        )

        # Serialize (would be sent over network)
        wire_data = request.to_bytes()

        # Deserialize on receiver side
        received_request = RekeyRequest.from_bytes(wire_data, runtime_a, runtime_b)

        assert received_request.ephemeral_public_key == public_key
        assert received_request.proposed_step == 42

    def test_rotation_preserves_bilateral_channel_functionality(self):
        """After rotation, bilateral channel should work normally."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        initial_state = bilateral_seed(runtime_a, runtime_b)

        channel_a = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=initial_state,
        )

        channel_b = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_b,
            peer_runtime=runtime_a,
            state=initial_state,
        )

        # Perform rotation
        session_a = channel_a.get_rotation_session()
        session_b = channel_b.get_rotation_session()

        _, public_a = session_a.generate_keypair()
        _, public_b = session_b.generate_keypair()

        secret_a = session_a.compute_shared_secret(public_b)
        secret_b = session_b.compute_shared_secret(public_a)

        new_state = derive_rotated_bilateral_state(
            initial_state,
            secret_a,
            runtime_a,
            runtime_b,
        )

        channel_a.state = new_state
        channel_b.state = new_state

        # Verify states match
        assert channel_a.state.ratchet_state == channel_b.state.ratchet_state
        assert channel_a.state.shared_prng_seed == channel_b.state.shared_prng_seed

        # Can continue incrementing message counts
        channel_a.increment_message_count()
        channel_b.increment_message_count()

        assert channel_a.get_rotation_trigger().messages_since_rotation == 1
        assert channel_b.get_rotation_trigger().messages_since_rotation == 1

    def test_multiple_rotations_in_sequence(self):
        """Multiple rotations can be performed sequentially."""
        from mfp.federation.rotation import RotationSession

        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )

        config = RotationConfig(rotation_message_threshold=50)
        channel.configure_rotation(config)

        states = [state]

        # Perform 3 rotations
        for rotation_num in range(3):
            # Trigger rotation
            for _ in range(50):
                channel.increment_message_count()

            assert channel.should_rotate()

            # Perform rotation - simulate both sides
            session_a = channel.get_rotation_session()
            _, public_a = session_a.generate_keypair()

            # Create peer session
            session_peer = RotationSession()
            _, public_peer = session_peer.generate_keypair()

            # Both compute shared secret
            shared_secret = session_a.compute_shared_secret(public_peer)

            new_state = derive_rotated_bilateral_state(
                channel.state,
                shared_secret,
                runtime_a,
                runtime_b,
            )

            # Verify new state differs from previous
            assert new_state.ratchet_state != states[-1].ratchet_state
            assert new_state.shared_prng_seed != states[-1].shared_prng_seed

            # Update channel
            channel.state = new_state
            states.append(new_state)

            # Reset trigger
            channel.get_rotation_trigger().reset()
            session_a.reset()

        # All states should be different
        assert len(states) == 4  # initial + 3 rotations
        ratchet_states = [s.ratchet_state for s in states]
        assert len(set(ratchet_states)) == 4  # All unique

    def test_rotation_failure_recovery(self):
        """Failed rotation can be retried with new session."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))

        channel = BilateralChannel(
            bilateral_id=derive_bilateral_id(runtime_a, runtime_b),
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=bilateral_seed(runtime_a, runtime_b),
        )

        # Start rotation attempt 1
        session1 = channel.get_rotation_session()
        session1.state = RotationState.INITIATING
        _, public1 = session1.generate_keypair()

        # Simulate failure (timeout, network error, etc.)
        # Reset session
        session1.reset()

        assert session1.state == RotationState.IDLE
        assert session1.local_private_key is None

        # Retry with new session
        session2 = channel.get_rotation_session()
        session2.state = RotationState.INITIATING
        _, public2 = session2.generate_keypair()

        # New keypair should be different
        assert public2 != public1

        # Can complete rotation successfully
        peer_key = b"p" * 32
        secret = session2.compute_shared_secret(peer_key)
        assert len(secret) == 32
