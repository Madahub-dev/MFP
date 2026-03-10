"""Tests for Merkle tree incremental Sg computation."""

import pytest

from mfp.core.merkle import IncrementalSg, MerkleNode, compose_ordered_incremental
from mfp.core.primitives import random_bytes, sha256
from mfp.core.ratchet import compose_ordered
from mfp.core.types import ChannelId, GlobalState, StateValue


class TestMerkleNode:
    """Tests for MerkleNode."""

    def test_leaf_node_is_leaf(self):
        """Leaf nodes should return True for is_leaf()."""
        leaf = MerkleNode(hash=b"test", channel_id=ChannelId(random_bytes(16)))
        assert leaf.is_leaf()

    def test_internal_node_not_leaf(self):
        """Internal nodes should return False for is_leaf()."""
        leaf1 = MerkleNode(hash=b"left")
        leaf2 = MerkleNode(hash=b"right")
        parent = MerkleNode(hash=b"parent", left=leaf1, right=leaf2)

        assert not parent.is_leaf()


class TestIncrementalSg:
    """Tests for IncrementalSg Merkle tree."""

    def test_empty_tree_raises_error(self):
        """Getting root hash from empty tree should raise error."""
        tree = IncrementalSg()

        with pytest.raises(ValueError, match="empty tree"):
            tree.get_root_hash()

    def test_single_channel_tree(self):
        """Tree with single channel should work."""
        channel_id = ChannelId(random_bytes(16))
        state = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(channel_id, state)])

        assert tree.channel_count() == 1
        assert tree.root is not None
        assert tree.root.is_leaf()

        # Root hash should be hash of state
        expected_hash = sha256(state.data)
        assert tree.root.hash == expected_hash.data

    def test_two_channel_tree(self):
        """Tree with two channels should build correctly."""
        ch1 = ChannelId(random_bytes(16))
        ch2 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))
        state2 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1), (ch2, state2)])

        assert tree.channel_count() == 2
        assert tree.root is not None
        assert not tree.root.is_leaf()
        assert tree.root.left is not None
        assert tree.root.right is not None

    def test_merkle_produces_consistent_root(self):
        """Merkle root should be consistent for same inputs."""
        # Create several channels
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(10)
        ]

        # Build tree twice with same inputs
        tree1 = IncrementalSg.from_channel_states(channels)
        tree2 = IncrementalSg.from_channel_states(channels)

        # Should produce same root
        assert tree1.get_root_hash().value.data == tree2.get_root_hash().value.data

    def test_merkle_root_changes_on_state_change(self):
        """Merkle root should change when any channel state changes."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(5)
        ]

        tree = IncrementalSg.from_channel_states(channels)
        original_root = tree.get_root_hash().value.data

        # Change one channel state
        new_channels = channels.copy()
        new_channels[2] = (channels[2][0], StateValue(data=random_bytes(32)))

        tree2 = IncrementalSg.from_channel_states(new_channels)
        new_root = tree2.get_root_hash().value.data

        # Root should be different
        assert original_root != new_root

    def test_update_channel_recomputes_root(self):
        """Updating a channel should recompute root hash."""
        ch1 = ChannelId(random_bytes(16))
        ch2 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))
        state2 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1), (ch2, state2)])
        original_root = tree.root.hash

        # Update one channel
        new_state1 = StateValue(data=random_bytes(32))
        tree.update_channel(ch1, new_state1)

        # Root should change
        assert tree.root.hash != original_root

        # Should match building new tree with updated state
        expected_tree = IncrementalSg.from_channel_states([(ch1, new_state1), (ch2, state2)])
        assert tree.get_root_hash().value.data == expected_tree.get_root_hash().value.data

    def test_update_multiple_channels(self):
        """Multiple updates should maintain correctness."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(10)
        ]

        tree = IncrementalSg.from_channel_states(channels)

        # Update several channels
        for i in [0, 3, 7]:
            new_state = StateValue(data=random_bytes(32))
            channels[i] = (channels[i][0], new_state)
            tree.update_channel(channels[i][0], new_state)

        # Should match building new tree with updated states
        expected_tree = IncrementalSg.from_channel_states(channels)
        assert tree.get_root_hash().value.data == expected_tree.get_root_hash().value.data

    def test_update_nonexistent_channel_raises_error(self):
        """Updating channel not in tree should raise error."""
        ch1 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1)])

        # Try to update channel not in tree
        ch2 = ChannelId(random_bytes(16))
        with pytest.raises(ValueError, match="not in tree"):
            tree.update_channel(ch2, StateValue(data=random_bytes(32)))

    def test_add_channel_increases_count(self):
        """Adding a channel should increase channel count."""
        ch1 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1)])
        assert tree.channel_count() == 1

        # Add another channel
        ch2 = ChannelId(random_bytes(16))
        state2 = StateValue(data=random_bytes(32))
        tree.add_channel(ch2, state2)

        assert tree.channel_count() == 2

    def test_add_channel_maintains_correctness(self):
        """Adding channels should maintain Merkle correctness."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(5)
        ]

        tree = IncrementalSg.from_channel_states(channels[:3])

        # Add remaining channels
        for ch_id, state in channels[3:]:
            tree.add_channel(ch_id, state)

        # Should match building new tree with all channels
        expected_tree = IncrementalSg.from_channel_states(channels)
        assert tree.get_root_hash().value.data == expected_tree.get_root_hash().value.data

    def test_add_duplicate_channel_raises_error(self):
        """Adding duplicate channel should raise error."""
        ch1 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1)])

        with pytest.raises(ValueError, match="already exists"):
            tree.add_channel(ch1, StateValue(data=random_bytes(32)))

    def test_remove_channel_decreases_count(self):
        """Removing a channel should decrease channel count."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(5)
        ]

        tree = IncrementalSg.from_channel_states(channels)
        assert tree.channel_count() == 5

        tree.remove_channel(channels[0][0])
        assert tree.channel_count() == 4

    def test_remove_channel_maintains_correctness(self):
        """Removing channels should maintain Merkle correctness."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(5)
        ]

        tree = IncrementalSg.from_channel_states(channels)

        # Remove some channels
        tree.remove_channel(channels[1][0])
        tree.remove_channel(channels[3][0])

        # Should match building new tree with remaining channels
        remaining = [channels[0], channels[2], channels[4]]
        expected_tree = IncrementalSg.from_channel_states(remaining)
        assert tree.get_root_hash().value.data == expected_tree.get_root_hash().value.data

    def test_remove_all_channels_leaves_empty_tree(self):
        """Removing all channels should leave empty tree."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(3)
        ]

        tree = IncrementalSg.from_channel_states(channels)

        for ch_id, _ in channels:
            tree.remove_channel(ch_id)

        assert tree.channel_count() == 0
        assert tree.root is None

    def test_remove_nonexistent_channel_raises_error(self):
        """Removing channel not in tree should raise error."""
        ch1 = ChannelId(random_bytes(16))
        state1 = StateValue(data=random_bytes(32))

        tree = IncrementalSg.from_channel_states([(ch1, state1)])

        ch2 = ChannelId(random_bytes(16))
        with pytest.raises(ValueError, match="not in tree"):
            tree.remove_channel(ch2)

    def test_deterministic_ordering(self):
        """Tree should produce same root regardless of insertion order."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(10)
        ]

        # Build tree with channels in original order
        tree1 = IncrementalSg.from_channel_states(channels)

        # Build tree with channels in reverse order
        tree2 = IncrementalSg.from_channel_states(list(reversed(channels)))

        # Should produce same root
        assert tree1.get_root_hash().value.data == tree2.get_root_hash().value.data


class TestComposeOrderedIncremental:
    """Tests for compose_ordered_incremental compatibility function."""

    def test_produces_valid_global_state(self):
        """compose_ordered_incremental should produce valid GlobalState."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(20)
        ]

        result = compose_ordered_incremental(channels)

        # Should be a valid GlobalState with 32-byte value
        assert isinstance(result, GlobalState)
        assert len(result.value.data) == 32

    def test_deterministic_output(self):
        """Same inputs should produce same output."""
        channels = [
            (ChannelId(random_bytes(16)), StateValue(data=random_bytes(32)))
            for _ in range(10)
        ]

        result1 = compose_ordered_incremental(channels)
        result2 = compose_ordered_incremental(channels)

        assert result1.value.data == result2.value.data

    def test_empty_channels_raises_error(self):
        """Empty channel list should raise error."""
        with pytest.raises(ValueError, match="zero states"):
            compose_ordered_incremental([])
