"""Integration tests for message deduplication (P3.4)."""

import pytest

from mfp.core.primitives import random_id
from mfp.core.types import ChannelId, MessageId
from mfp.runtime.deduplication import DeduplicationConfig, DeduplicationTracker
from mfp.runtime.runtime import Runtime


class TestDeduplicationIntegration:
    """Integration tests for message deduplication with runtime."""

    def test_deduplication_prevents_duplicate_delivery(self):
        """Deduplication tracker should detect duplicate message IDs."""
        tracker = DeduplicationTracker()

        channel_id = ChannelId(random_id(16))
        message_id = MessageId(random_id(16))

        # First message - not duplicate
        assert not tracker.is_duplicate(channel_id, message_id)

        # Same message ID - is duplicate
        assert tracker.is_duplicate(channel_id, message_id)

    def test_deduplication_with_runtime_channels(self):
        """Deduplication should work with real runtime channels."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        # Create channel
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Generate message IDs
        msg_id1 = MessageId(random_id(16))
        msg_id2 = MessageId(random_id(16))

        # Check deduplication
        assert not tracker.is_duplicate(channel_id, msg_id1)
        assert not tracker.is_duplicate(channel_id, msg_id2)

        # Duplicates
        assert tracker.is_duplicate(channel_id, msg_id1)
        assert tracker.is_duplicate(channel_id, msg_id2)

    def test_deduplication_independent_across_channels(self):
        """Deduplication should be independent per channel."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        # Create two channels
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        agent_c = rt.bind_agent(lambda msg: None)

        channel_ab = rt.establish_channel(agent_a, agent_b, depth=4)
        channel_ac = rt.establish_channel(agent_a, agent_c, depth=4)

        # Same message ID on both channels
        message_id = MessageId(random_id(16))

        # Not duplicate on channel AB
        assert not tracker.is_duplicate(channel_ab, message_id)

        # Not duplicate on channel AC (different channel)
        assert not tracker.is_duplicate(channel_ac, message_id)

        # But is duplicate on second check for each
        assert tracker.is_duplicate(channel_ab, message_id)
        assert tracker.is_duplicate(channel_ac, message_id)

    def test_deduplication_window_size_eviction(self):
        """Deduplication should evict old messages when window is full."""
        config = DeduplicationConfig(window_size=10)
        tracker = DeduplicationTracker(config)

        rt = Runtime()
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Add 10 messages
        message_ids = [MessageId(random_id(16)) for _ in range(10)]
        for msg_id in message_ids:
            tracker.is_duplicate(channel_id, msg_id)

        # Window is full
        assert tracker.get_tracked_count(channel_id) == 10

        # All should be duplicates
        for msg_id in message_ids:
            assert tracker.is_duplicate(channel_id, msg_id)

        # Add 5 more (should evict first 5)
        new_messages = [MessageId(random_id(16)) for _ in range(5)]
        for msg_id in new_messages:
            tracker.is_duplicate(channel_id, msg_id)

        # Window still at max
        assert tracker.get_tracked_count(channel_id) == 10

        # Last 5 of original + new 5 should still be tracked (before checking evicted ones)
        for msg_id in message_ids[5:]:
            assert tracker.is_duplicate(channel_id, msg_id)

        # First 5 should be evicted (not duplicates anymore)
        # Note: Checking these will RE-ADD them to the tracker
        for msg_id in message_ids[:5]:
            assert not tracker.is_duplicate(channel_id, msg_id)

    def test_deduplication_ttl_eviction(self):
        """Old messages should be evicted after TTL expires."""
        import time

        config = DeduplicationConfig(ttl_seconds=0.1)
        tracker = DeduplicationTracker(config)

        rt = Runtime()
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        message_id = MessageId(random_id(16))

        # Add message
        assert not tracker.is_duplicate(channel_id, message_id)

        # Immediately is duplicate
        assert tracker.is_duplicate(channel_id, message_id)

        # Wait for TTL to expire
        time.sleep(0.15)

        # Trigger eviction by checking another message
        new_msg = MessageId(random_id(16))
        tracker.is_duplicate(channel_id, new_msg)

        # Original should be evicted
        assert not tracker.is_duplicate(channel_id, message_id)

    def test_deduplication_clear_channel_on_close(self):
        """Deduplication should clear when channel closes."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Add messages
        message_ids = [MessageId(random_id(16)) for _ in range(5)]
        for msg_id in message_ids:
            tracker.is_duplicate(channel_id, msg_id)

        assert tracker.get_tracked_count(channel_id) == 5

        # Close channel
        rt.close_channel(channel_id)

        # Clear deduplication
        tracker.clear_channel(channel_id)

        # Should be empty
        assert tracker.get_tracked_count(channel_id) == 0

        # Messages should not be duplicates anymore
        for msg_id in message_ids:
            assert not tracker.is_duplicate(channel_id, msg_id)

    def test_deduplication_with_many_channels(self):
        """Deduplication should scale to many channels."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        # Create 100 channels
        channels = []
        for i in range(100):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append(channel_id)

        # Add 10 messages per channel
        for channel_id in channels:
            for j in range(10):
                msg_id = MessageId(random_id(16))
                tracker.is_duplicate(channel_id, msg_id)

        # Each channel should track 10 messages
        for channel_id in channels:
            assert tracker.get_tracked_count(channel_id) == 10

    def test_deduplication_replay_attack_prevention(self):
        """Deduplication should prevent replay attacks."""
        tracker = DeduplicationTracker()

        rt = Runtime()
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Simulate message sequence
        message_ids = [MessageId(random_id(16)) for _ in range(10)]

        # Process messages in order
        for msg_id in message_ids:
            is_dup = tracker.is_duplicate(channel_id, msg_id)
            assert not is_dup, "First time should not be duplicate"

        # Attempt replay attack - resend old messages
        replay_attempts = 0
        for msg_id in message_ids:
            if tracker.is_duplicate(channel_id, msg_id):
                replay_attempts += 1

        # All replays should be detected
        assert replay_attempts == 10

    def test_deduplication_performance_with_large_window(self):
        """Deduplication should perform well with large windows."""
        import time

        config = DeduplicationConfig(window_size=10000)
        tracker = DeduplicationTracker(config)

        rt = Runtime()
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Add 1000 unique messages
        message_ids = [MessageId(random_id(16)) for _ in range(1000)]

        start = time.perf_counter()
        for msg_id in message_ids:
            tracker.is_duplicate(channel_id, msg_id)
        elapsed = time.perf_counter() - start

        # Should be fast (O(1) per operation)
        assert elapsed < 0.1, f"Too slow: {elapsed*1000:.2f}ms for 1000 messages"

        # Check duplicates (should also be fast)
        start = time.perf_counter()
        for msg_id in message_ids:
            assert tracker.is_duplicate(channel_id, msg_id)
        check_elapsed = time.perf_counter() - start

        assert check_elapsed < 0.1, f"Duplicate check too slow: {check_elapsed*1000:.2f}ms"

    def test_deduplication_mixed_operations(self):
        """Deduplication should handle mixed add/check operations."""
        tracker = DeduplicationTracker()

        rt = Runtime()
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        message_ids = []

        # Mixed pattern: add 3, check 2, add 2, check all
        for i in range(3):
            msg_id = MessageId(random_id(16))
            message_ids.append(msg_id)
            assert not tracker.is_duplicate(channel_id, msg_id)

        # Check first 2
        assert tracker.is_duplicate(channel_id, message_ids[0])
        assert tracker.is_duplicate(channel_id, message_ids[1])

        # Add 2 more
        for i in range(2):
            msg_id = MessageId(random_id(16))
            message_ids.append(msg_id)
            assert not tracker.is_duplicate(channel_id, msg_id)

        # Check all 5
        for msg_id in message_ids:
            assert tracker.is_duplicate(channel_id, msg_id)

        assert tracker.get_tracked_count(channel_id) == 5
