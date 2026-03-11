"""Unit tests for message deduplication (P3.4)."""

import time

import pytest

from mfp.core.primitives import random_id
from mfp.core.types import ChannelId, MessageId
from mfp.runtime.deduplication import DeduplicationConfig, DeduplicationTracker


class TestDeduplicationTracker:
    """Tests for DeduplicationTracker."""

    def test_first_message_not_duplicate(self):
        """First message on a channel should not be detected as duplicate."""
        tracker = DeduplicationTracker()
        channel_id = ChannelId(random_id(16))
        message_id = MessageId(random_id(16))

        is_dup = tracker.is_duplicate(channel_id, message_id)
        assert not is_dup

    def test_same_message_id_is_duplicate(self):
        """Same message ID on same channel should be detected as duplicate."""
        tracker = DeduplicationTracker()
        channel_id = ChannelId(random_id(16))
        message_id = MessageId(random_id(16))

        # First time - not duplicate
        assert not tracker.is_duplicate(channel_id, message_id)

        # Second time - is duplicate
        assert tracker.is_duplicate(channel_id, message_id)

    def test_different_message_ids_not_duplicates(self):
        """Different message IDs should not be detected as duplicates."""
        tracker = DeduplicationTracker()
        channel_id = ChannelId(random_id(16))

        for _ in range(10):
            message_id = MessageId(random_id(16))
            assert not tracker.is_duplicate(channel_id, message_id)

    def test_same_message_different_channels_not_duplicate(self):
        """Same message ID on different channels should not be duplicate."""
        tracker = DeduplicationTracker()
        channel_a = ChannelId(random_id(16))
        channel_b = ChannelId(random_id(16))
        message_id = MessageId(random_id(16))

        # Send on channel A
        assert not tracker.is_duplicate(channel_a, message_id)

        # Same message ID on channel B should not be duplicate
        assert not tracker.is_duplicate(channel_b, message_id)

    def test_window_size_eviction(self):
        """Old messages should be evicted when window size is exceeded."""
        config = DeduplicationConfig(window_size=5)
        tracker = DeduplicationTracker(config)
        channel_id = ChannelId(random_id(16))

        message_ids = [MessageId(random_id(16)) for _ in range(10)]

        # Add first 5 messages
        for i in range(5):
            assert not tracker.is_duplicate(channel_id, message_ids[i])

        # All 5 should be tracked
        assert tracker.get_tracked_count(channel_id) == 5

        # Verify first 5 are duplicates
        for i in range(5):
            assert tracker.is_duplicate(channel_id, message_ids[i])

        # Add 5 more (should evict first 5)
        for i in range(5, 10):
            assert not tracker.is_duplicate(channel_id, message_ids[i])

        # Still only 5 tracked (window size)
        assert tracker.get_tracked_count(channel_id) == 5

        # Last 5 should be duplicates
        for i in range(5, 10):
            assert tracker.is_duplicate(channel_id, message_ids[i])

        # First 5 should no longer be tracked (evicted by adding 5-9)
        # So if we try to add them again, they should NOT be duplicates
        for i in range(5):
            assert not tracker.is_duplicate(channel_id, message_ids[i])

    def test_ttl_eviction(self):
        """Messages older than TTL should be evicted."""
        config = DeduplicationConfig(ttl_seconds=0.2)
        tracker = DeduplicationTracker(config)
        channel_id = ChannelId(random_id(16))
        message_id = MessageId(random_id(16))

        # Add message
        assert not tracker.is_duplicate(channel_id, message_id)

        # Immediately after - still duplicate
        assert tracker.is_duplicate(channel_id, message_id)

        # Wait for TTL to expire
        time.sleep(0.25)

        # Should be evicted and no longer duplicate
        # (is_duplicate triggers eviction check)
        new_message = MessageId(random_id(16))
        tracker.is_duplicate(channel_id, new_message)

        # Original message should be evicted
        assert not tracker.is_duplicate(channel_id, message_id)

    def test_clear_channel(self):
        """clear_channel should remove all tracked messages for that channel."""
        tracker = DeduplicationTracker()
        channel_id = ChannelId(random_id(16))

        # Add several messages
        message_ids = [MessageId(random_id(16)) for _ in range(5)]
        for msg_id in message_ids:
            tracker.is_duplicate(channel_id, msg_id)

        assert tracker.get_tracked_count(channel_id) == 5

        # Clear channel
        tracker.clear_channel(channel_id)

        assert tracker.get_tracked_count(channel_id) == 0

        # Messages should no longer be duplicates
        for msg_id in message_ids:
            assert not tracker.is_duplicate(channel_id, msg_id)

    def test_multiple_channels_independent(self):
        """Deduplication tracking should be independent per channel."""
        tracker = DeduplicationTracker()
        channel_a = ChannelId(random_id(16))
        channel_b = ChannelId(random_id(16))

        # Add messages to channel A
        for _ in range(10):
            tracker.is_duplicate(channel_a, MessageId(random_id(16)))

        # Add messages to channel B
        for _ in range(5):
            tracker.is_duplicate(channel_b, MessageId(random_id(16)))

        assert tracker.get_tracked_count(channel_a) == 10
        assert tracker.get_tracked_count(channel_b) == 5

        # Clear channel A
        tracker.clear_channel(channel_a)

        assert tracker.get_tracked_count(channel_a) == 0
        assert tracker.get_tracked_count(channel_b) == 5

    def test_get_tracked_count_nonexistent_channel(self):
        """get_tracked_count for unknown channel should return 0."""
        tracker = DeduplicationTracker()
        channel_id = ChannelId(random_id(16))

        assert tracker.get_tracked_count(channel_id) == 0

    def test_duplicate_detection_preserves_order(self):
        """Duplicate detection should maintain FIFO eviction order."""
        config = DeduplicationConfig(window_size=3)
        tracker = DeduplicationTracker(config)
        channel_id = ChannelId(random_id(16))

        msg1 = MessageId(random_id(16))
        msg2 = MessageId(random_id(16))
        msg3 = MessageId(random_id(16))
        msg4 = MessageId(random_id(16))

        # Add 3 messages (first time - not duplicates)
        assert not tracker.is_duplicate(channel_id, msg1)
        assert not tracker.is_duplicate(channel_id, msg2)
        assert not tracker.is_duplicate(channel_id, msg3)

        # Now all should be duplicates
        assert tracker.is_duplicate(channel_id, msg1)
        assert tracker.is_duplicate(channel_id, msg2)
        assert tracker.is_duplicate(channel_id, msg3)

        # Add 4th message (evicts msg1 which is oldest)
        assert not tracker.is_duplicate(channel_id, msg4)

        # msg2, msg3, msg4 should be duplicates
        assert tracker.is_duplicate(channel_id, msg2)
        assert tracker.is_duplicate(channel_id, msg3)
        assert tracker.is_duplicate(channel_id, msg4)

        # msg1 should be evicted (oldest), so not a duplicate anymore
        assert not tracker.is_duplicate(channel_id, msg1)
