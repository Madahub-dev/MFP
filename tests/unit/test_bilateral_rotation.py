"""Unit tests for bilateral channel rotation integration (P3.3)."""

import time

import pytest

from mfp.core.ratchet import bilateral_seed
from mfp.core.types import RuntimeId, StateValue
from mfp.federation.bilateral import BilateralChannel, derive_bilateral_id
from mfp.federation.rotation import RotationConfig, RotationReason, RotationState


class TestBilateralChannelRotation:
    """Tests for rotation integration with BilateralChannel."""

    def test_bilateral_channel_has_rotation_trigger(self):
        """BilateralChannel should have rotation trigger."""
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

        trigger = channel.get_rotation_trigger()
        assert trigger is not None
        assert trigger.messages_since_rotation == 0

    def test_bilateral_channel_has_rotation_session(self):
        """BilateralChannel should have rotation session."""
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

        session = channel.get_rotation_session()
        assert session is not None
        assert session.state == RotationState.IDLE

    def test_increment_message_count_updates_trigger(self):
        """Incrementing message count should update trigger."""
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

        # Increment messages
        for i in range(100):
            channel.increment_message_count()

        trigger = channel.get_rotation_trigger()
        assert trigger.messages_since_rotation == 100

    def test_should_rotate_message_count_threshold(self):
        """should_rotate returns True when message threshold exceeded."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = RotationConfig(rotation_message_threshold=1000)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )
        channel.configure_rotation(config)

        # Below threshold
        for _ in range(999):
            channel.increment_message_count()

        assert not channel.should_rotate()

        # Hit threshold
        channel.increment_message_count()
        assert channel.should_rotate()

        # Check reason was recorded
        session = channel.get_rotation_session()
        assert session.reason == RotationReason.MESSAGE_COUNT

    def test_should_rotate_time_threshold(self):
        """should_rotate returns True when time threshold exceeded."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = RotationConfig(rotation_time_threshold_seconds=0.1)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )
        channel.configure_rotation(config)

        # Initially no rotation
        assert not channel.should_rotate()

        # Wait for threshold
        time.sleep(0.15)

        assert channel.should_rotate()

        # Check reason
        session = channel.get_rotation_session()
        assert session.reason == RotationReason.TIME_ELAPSED

    def test_configure_rotation_updates_config(self):
        """configure_rotation should update channel config."""
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

        # Default config allows rotation
        assert channel._rotation_config.enable_auto_rotation is True

        # Disable rotation
        new_config = RotationConfig(enable_auto_rotation=False)
        channel.configure_rotation(new_config)

        # Verify config updated
        for _ in range(10000):
            channel.increment_message_count()

        assert not channel.should_rotate()

    def test_multiple_channels_independent_rotation_tracking(self):
        """Each bilateral channel tracks rotation independently."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        runtime_c = RuntimeId(value=StateValue(b"c" * 32))

        bilateral_ab_id = derive_bilateral_id(runtime_a, runtime_b)
        bilateral_ac_id = derive_bilateral_id(runtime_a, runtime_c)

        state_ab = bilateral_seed(runtime_a, runtime_b)
        state_ac = bilateral_seed(runtime_a, runtime_c)

        config = RotationConfig(rotation_message_threshold=100)

        channel_ab = BilateralChannel(
            bilateral_id=bilateral_ab_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state_ab,
        )
        channel_ab.configure_rotation(config)

        channel_ac = BilateralChannel(
            bilateral_id=bilateral_ac_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_c,
            state=state_ac,
        )
        channel_ac.configure_rotation(config)

        # Send 50 messages on AB, 150 on AC
        for _ in range(50):
            channel_ab.increment_message_count()

        for _ in range(150):
            channel_ac.increment_message_count()

        # AB should not need rotation
        assert not channel_ab.should_rotate()

        # AC should need rotation
        assert channel_ac.should_rotate()

        # Check counts
        assert channel_ab.get_rotation_trigger().messages_since_rotation == 50
        assert channel_ac.get_rotation_trigger().messages_since_rotation == 150

    def test_rotation_trigger_reset_after_rotation(self):
        """Trigger should reset after successful rotation."""
        runtime_a = RuntimeId(value=StateValue(b"a" * 32))
        runtime_b = RuntimeId(value=StateValue(b"b" * 32))
        bilateral_id = derive_bilateral_id(runtime_a, runtime_b)
        state = bilateral_seed(runtime_a, runtime_b)

        config = RotationConfig(rotation_message_threshold=100)

        channel = BilateralChannel(
            bilateral_id=bilateral_id,
            local_runtime=runtime_a,
            peer_runtime=runtime_b,
            state=state,
        )
        channel.configure_rotation(config)

        # Trigger rotation
        for _ in range(100):
            channel.increment_message_count()

        assert channel.should_rotate()

        # Simulate successful rotation by resetting trigger
        trigger = channel.get_rotation_trigger()
        trigger.reset()

        # After reset, should not need rotation
        assert not channel.should_rotate()
        assert trigger.messages_since_rotation == 0

        # Can trigger again after threshold
        for _ in range(100):
            channel.increment_message_count()

        assert channel.should_rotate()
