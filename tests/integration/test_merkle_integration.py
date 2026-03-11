"""Integration tests for Merkle tree incremental Sg computation (P2.1)."""

import pytest

from mfp.core.merkle import IncrementalSg
from mfp.core.primitives import random_id
from mfp.core.types import ChannelId, StateValue
from mfp.runtime.pipeline import RuntimeConfig
from mfp.runtime.runtime import Runtime


class TestMerkleTreeIntegration:
    """Integration tests for Merkle tree with runtime."""

    def test_runtime_uses_merkle_for_sg(self):
        """Runtime should use Merkle tree for Sg computation."""
        rt = Runtime()

        # Bind agents
        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)

        # Establish channel
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Runtime should have incremental_sg after first channel
        assert rt._incremental_sg is not None

        # Sg should be computed from Merkle root
        sg = rt.global_state
        assert sg is not None
        assert sg.value is not None

    def test_merkle_updates_on_message_send(self):
        """Merkle tree should update incrementally on each message."""
        rt = Runtime()

        agent_a = rt.bind_agent(lambda msg: None)
        agent_b = rt.bind_agent(lambda msg: None)
        channel_id = rt.establish_channel(agent_a, agent_b, depth=4)

        # Track Sg values
        sg_values = [rt.global_state]

        # Send several messages
        for i in range(10):
            rt.send(agent_a, channel_id, f"msg{i}".encode())
            sg_values.append(rt.global_state)

        # Each Sg should be different (states advance)
        unique_sgs = set(sg.value.data for sg in sg_values)
        assert len(unique_sgs) == len(sg_values)

    def test_merkle_with_multiple_channels(self):
        """Merkle tree should handle multiple channels efficiently."""
        rt = Runtime()

        # Create 100 channels
        channels = []
        for i in range(100):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))

        # Merkle tree should be initialized
        assert rt._incremental_sg is not None

        # Send message on one channel
        sg_before = rt.global_state
        rt.send(channels[0][0], channels[0][1], b"test")
        sg_after = rt.global_state

        # Sg should change (O(log N) update)
        assert sg_before != sg_after

    def test_merkle_channel_addition_updates_tree(self):
        """Adding channels should update Merkle tree incrementally."""
        rt = Runtime()

        sg_values = []

        # Add 50 channels one by one
        for i in range(50):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            rt.establish_channel(agent_a, agent_b, depth=4)
            sg_values.append(rt.global_state)

        # Each Sg should be different
        unique_sgs = set(sg.value.data for sg in sg_values)
        assert len(unique_sgs) == len(sg_values)

    def test_merkle_channel_closure_updates_tree(self):
        """Closing channels should update Merkle tree."""
        rt = Runtime()

        # Create 10 channels
        channels = []
        for i in range(10):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append(channel_id)

        sg_with_all = rt.global_state

        # Close half the channels
        for channel_id in channels[:5]:
            rt.close_channel(channel_id)

        sg_after_close = rt.global_state

        # Sg should change
        assert sg_with_all != sg_after_close

    def test_merkle_consistency_across_operations(self):
        """Merkle tree should maintain consistency across mixed operations."""
        rt = Runtime()

        # Mixed operations: add channels, send messages, close channels
        agent_pairs = []

        # Add 5 channels
        for i in range(5):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            agent_pairs.append((agent_a, agent_b, channel_id))

        # Send messages on all channels
        for agent_a, _, channel_id in agent_pairs:
            rt.send(agent_a, channel_id, b"test")

        # Add more channels
        for i in range(5):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            agent_pairs.append((agent_a, agent_b, channel_id))

        # Close some channels
        for _, _, channel_id in agent_pairs[:3]:
            rt.close_channel(channel_id)

        # Sg should still be computable
        final_sg = rt.global_state
        assert final_sg is not None

        # Should have correct number of active channels
        active_channels = len([ch for ch in rt._channels.values() if ch.status.value == "active"])
        assert active_channels == 7  # 10 total - 3 closed


class TestMerklePerformance:
    """Performance tests for Merkle tree vs naive computation."""

    def test_merkle_scales_with_many_channels(self):
        """Merkle tree should handle many channels efficiently."""
        import time

        rt = Runtime()

        # Create 1000 channels
        channels = []
        for i in range(1000):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            channel_id = rt.establish_channel(agent_a, agent_b, depth=4)
            channels.append((agent_a, channel_id))

        # Measure time to send message (includes Sg recomputation)
        start = time.perf_counter()
        rt.send(channels[0][0], channels[0][1], b"test")
        elapsed = time.perf_counter() - start

        # Should complete quickly (O(log N) update)
        # With 1000 channels, log2(1000) ≈ 10 hash operations
        assert elapsed < 0.1, f"Sg update too slow: {elapsed*1000:.2f}ms"

    def test_merkle_sg_computation_is_fast(self):
        """Getting Sg should be O(1) with Merkle tree."""
        import time

        rt = Runtime()

        # Create 500 channels
        for i in range(500):
            agent_a = rt.bind_agent(lambda msg: None)
            agent_b = rt.bind_agent(lambda msg: None)
            rt.establish_channel(agent_a, agent_b, depth=4)

        # Measure Sg retrieval time
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            rt.global_state
        elapsed = time.perf_counter() - start

        avg_time = elapsed / iterations
        # Should be very fast (just returns root hash)
        assert avg_time < 0.0001, f"Sg retrieval too slow: {avg_time*1000:.3f}ms"
