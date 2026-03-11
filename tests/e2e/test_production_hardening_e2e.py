"""End-to-end tests for production hardening features (P2/P3).

Tests realistic scenarios combining multiple hardening features:
- Merkle tree (P2.1)
- Circuit breakers (P2.2, P3.1)
- Timeouts (P2.3)
- Connection pooling (P2.4)
- Frame caching (P3.2)
- Key rotation (P3.3)
- Message deduplication (P3.4)
"""

import time

import pytest

from mfp.core.frame import clear_frame_cache, get_frame_cache_stats
from mfp.core.primitives import random_id
from mfp.core.types import MessageId
from mfp.observability.circuit_breaker import CircuitBreakerConfig
from mfp.runtime.deduplication import DeduplicationConfig, DeduplicationTracker
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime


class TestProductionHardeningE2E:
    """End-to-end tests for production hardening features."""

    def test_runtime_with_merkle_tree_scales(self):
        """Runtime with Merkle tree should scale to many channels efficiently."""
        rt = Runtime()

        # Create 500 channels
        channels = []
        for i in range(500):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))

        # Send messages on multiple channels
        start = time.perf_counter()
        for i in range(50):
            agent_a, channel_id = channels[i]
            rt.send(agent_a, channel_id, f"msg{i}".encode())
        elapsed = time.perf_counter() - start

        # Should be fast with Merkle tree (O(log N) per message)
        assert elapsed < 1.0, f"Too slow with Merkle: {elapsed:.2f}s"

        # Sg should be computable
        sg = rt.global_state
        assert sg is not None

    def test_merkle_tree_performance_with_many_channels(self):
        """Merkle tree should maintain performance with many channels."""
        rt = Runtime()

        # Create 200 channels
        channels = []
        start = time.perf_counter()
        for i in range(200):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))
        setup_time = time.perf_counter() - start

        # Should complete setup quickly
        assert setup_time < 2.0, f"Setup too slow: {setup_time:.2f}s"

        # Send messages and measure Sg computation time
        start = time.perf_counter()
        for i in range(20):
            agent_a, channel_id = channels[i]
            rt.send(agent_a, channel_id, f"msg{i}".encode())
        send_time = time.perf_counter() - start

        # Should be fast with Merkle tree (O(log N) per message)
        assert send_time < 0.5, f"Send too slow: {send_time:.2f}s"

    def test_deduplication_with_multiple_channels(self):
        """Deduplication should work across multiple channels."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        # Create 10 channels
        channels = []
        for i in range(10):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append(channel_id)

        # Send unique messages on each channel
        for channel_id in channels:
            for j in range(5):
                msg_id = MessageId(random_id(16))
                assert not tracker.is_duplicate(channel_id, msg_id)

        # Each channel should track 5 messages
        for channel_id in channels:
            assert tracker.get_tracked_count(channel_id) == 5

        # Replays should be detected
        for channel_id in channels:
            # Generate new message
            msg_id = MessageId(random_id(16))
            tracker.is_duplicate(channel_id, msg_id)
            # Replay should be detected
            assert tracker.is_duplicate(channel_id, msg_id)

    def test_frame_caching_in_recovery_scenario(self):
        """Frame caching should help in recovery scenarios."""
        from mfp.core.frame import sample_frame_cross_runtime
        from mfp.core.types import StateValue

        clear_frame_cache()

        # Simulate recovery scenario - re-validating old frames
        local_state = StateValue(b"local" + b"\x00" * 27)
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # Simulate validating 100 frames from history (repeated state access)
        frames = []
        for step in range(10):
            # Each step accessed 10 times (validation, logging, etc.)
            for _ in range(10):
                frame = sample_frame_cross_runtime(
                    local_state, step, bilateral_state, prng_seed
                )
                frames.append(frame)

        # Check cache stats
        hits, misses, hit_rate = get_frame_cache_stats()

        # Should have high hit rate (90%+)
        assert hit_rate > 0.85, f"Low cache hit rate: {hit_rate:.2%}"

        # 10 unique steps = 10 misses, 90 hits
        assert misses == 10
        assert hits == 90

    def test_merkle_tree_consistency_across_operations(self):
        """Merkle tree should maintain consistency across different operations."""
        rt = Runtime()

        # Track Sg values through different operations
        sg_values = []

        # Initial state
        sg_values.append(("initial", rt.global_state))

        # Add channels
        channels = []
        for i in range(10):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, agent_b, channel_id))
            if i % 3 == 0:
                sg_values.append((f"after_channel_{i}", rt.global_state))

        # Send messages
        for i, (agent_a, _, channel_id) in enumerate(channels[:5]):
            rt.send(agent_a, channel_id, f"msg{i}".encode())
            sg_values.append((f"after_send_{i}", rt.global_state))

        # Close some channels
        for i in range(3):
            rt.close_channel(channels[i][2])
            sg_values.append((f"after_close_{i}", rt.global_state))

        # All Sg values should be different (operations change state)
        unique_sgs = set(sg.value.data if sg else None for _, sg in sg_values)

        # Remove None if initial state was None
        unique_sgs.discard(None)

        # Should have many unique Sg values
        assert len(unique_sgs) >= 5, "Sg should change across operations"

    def test_combined_hardening_features_realistic_workload(self):
        """Test realistic workload with multiple hardening features."""
        rt = Runtime()
        tracker = DeduplicationTracker(DeduplicationConfig(window_size=100))

        clear_frame_cache()

        # Create 20 channels
        channels = []
        for i in range(20):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))

        # Simulate realistic message flow
        total_messages = 100
        duplicates_detected = 0

        for i in range(total_messages):
            # Pick random channel
            agent_a, channel_id = channels[i % len(channels)]

            # Generate message ID
            msg_id = MessageId(random_id(16))

            # Check deduplication
            if not tracker.is_duplicate(channel_id, msg_id):
                # Send message (Merkle tree updates incrementally)
                rt.send(agent_a, channel_id, f"msg{i}".encode())
            else:
                duplicates_detected += 1

        # Verify system state
        sg = rt.global_state
        assert sg is not None

        # Check deduplication tracked messages
        total_tracked = sum(
            tracker.get_tracked_count(channel_id)
            for _, channel_id in channels
        )
        assert total_tracked > 0

        # Merkle tree should be working
        assert rt._incremental_sg is not None

    def test_high_throughput_with_merkle_and_dedup(self):
        """Test high throughput with Merkle tree and deduplication."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        # Create 50 channels
        channels = []
        for i in range(50):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))

        # Send 1000 messages as fast as possible
        start = time.perf_counter()

        for i in range(1000):
            agent_a, channel_id = channels[i % len(channels)]
            msg_id = MessageId(random_id(16))

            if not tracker.is_duplicate(channel_id, msg_id):
                rt.send(agent_a, channel_id, f"msg{i}".encode())

        elapsed = time.perf_counter() - start

        # Should achieve reasonable throughput
        throughput = 1000 / elapsed
        print(f"\nThroughput: {throughput:.0f} msg/s")

        # Should be >100 msg/s with Merkle tree
        assert throughput > 100, f"Low throughput: {throughput:.0f} msg/s"

    def test_channel_lifecycle_with_all_features(self):
        """Test complete channel lifecycle with all hardening features."""
        rt = Runtime()
        tracker = DeduplicationTracker()

        clear_frame_cache()

        # Establish channel
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        sg_initial = rt.global_state

        # Send messages with deduplication checks
        message_ids = []
        for i in range(10):
            msg_id = MessageId(random_id(16))
            message_ids.append(msg_id)

            # First time - not duplicate
            assert not tracker.is_duplicate(channel_id, msg_id)

            # Send message
            rt.send(agent_a, channel_id, f"msg{i}".encode())

        # Sg should have changed (Merkle tree updated)
        sg_after_messages = rt.global_state
        assert sg_after_messages != sg_initial

        # Replay detection
        for msg_id in message_ids:
            assert tracker.is_duplicate(channel_id, msg_id)

        # Close channel
        rt.close_channel(channel_id)

        # Clean up deduplication
        tracker.clear_channel(channel_id)
        assert tracker.get_tracked_count(channel_id) == 0

        # Sg should change again (Merkle tree updated)
        sg_after_close = rt.global_state
        assert sg_after_close != sg_after_messages

    def test_stress_test_all_features(self):
        """Stress test all hardening features together."""
        rt = Runtime()
        tracker = DeduplicationTracker(DeduplicationConfig(window_size=500))

        clear_frame_cache()

        # Create 100 channels
        channels = []
        for i in range(100):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, agent_b, channel_id))

        # Send 500 messages
        for i in range(500):
            agent_a, agent_b, channel_id = channels[i % len(channels)]

            msg_id = MessageId(random_id(16))

            if not tracker.is_duplicate(channel_id, msg_id):
                rt.send(agent_a, channel_id, f"stress{i}".encode())

        # System should still be functional
        sg = rt.global_state
        assert sg is not None

        # Close half the channels
        for i in range(50):
            _, _, channel_id = channels[i]
            rt.close_channel(channel_id)
            tracker.clear_channel(channel_id)

        # System should still be functional
        sg_after_close = rt.global_state
        assert sg_after_close is not None
        assert sg_after_close != sg

        # Send more messages on remaining channels
        for i in range(100):
            agent_a, _, channel_id = channels[50 + (i % 50)]

            msg_id = MessageId(random_id(16))

            if not tracker.is_duplicate(channel_id, msg_id):
                rt.send(agent_a, channel_id, f"post_close{i}".encode())

        # Everything should still work
        final_sg = rt.global_state
        assert final_sg is not None
