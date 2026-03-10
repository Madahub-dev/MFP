"""Merkle tree for incremental global state (Sg) computation.

Replaces O(N) compose_ordered() with O(log N) incremental updates.
Maintains a balanced binary Merkle tree where leaves are channel states.

Maps to: spec.md §4.3 (optimized implementation)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from mfp.core.primitives import sha256
from mfp.core.types import ChannelId, GlobalState, StateValue


@dataclass
class MerkleNode:
    """Node in the Merkle tree.

    Internal nodes store hash of children.
    Leaf nodes store channel state hash and original state value.
    """
    hash: bytes
    left: MerkleNode | None = None
    right: MerkleNode | None = None
    parent: MerkleNode | None = None
    channel_id: ChannelId | None = None  # Only set for leaves
    state_value: StateValue | None = None  # Original state for leaves (for rebuild)

    def is_leaf(self) -> bool:
        """Check if this is a leaf node."""
        return self.left is None and self.right is None


class IncrementalSg:
    """Merkle tree for O(log N) global state computation.

    Maintains a balanced binary tree of channel states.
    Updates require only O(log N) hash recomputations.
    """

    def __init__(self) -> None:
        self.root: MerkleNode | None = None
        self.leaf_map: dict[bytes, MerkleNode] = {}  # channel_id.value -> leaf
        self._ordered_channels: list[ChannelId] = []  # Sorted for determinism

    @staticmethod
    def from_channel_states(
        channel_states: Sequence[tuple[ChannelId, StateValue]],
    ) -> IncrementalSg:
        """Build Merkle tree from initial channel states.

        Sorts by channel_id for canonical ordering.
        """
        tree = IncrementalSg()

        if not channel_states:
            return tree

        # Sort by channel_id for deterministic ordering
        sorted_states = sorted(channel_states, key=lambda pair: pair[0].value)

        # Build leaves
        leaves = []
        for channel_id, state in sorted_states:
            leaf = MerkleNode(
                hash=sha256(state.data).data,  # Extract bytes from StateValue
                channel_id=channel_id,
                state_value=state,  # Store original state for rebuilds
            )
            tree.leaf_map[channel_id.value] = leaf
            tree._ordered_channels.append(channel_id)
            leaves.append(leaf)

        # Build tree bottom-up
        tree.root = tree._build_tree(leaves)

        return tree

    def _build_tree(self, nodes: list[MerkleNode]) -> MerkleNode:
        """Build balanced tree from leaf nodes."""
        if len(nodes) == 0:
            raise ValueError("Cannot build tree from empty node list")

        if len(nodes) == 1:
            return nodes[0]

        # Build parent layer
        parents = []
        for i in range(0, len(nodes), 2):
            left = nodes[i]
            right = nodes[i + 1] if i + 1 < len(nodes) else None

            # Compute parent hash
            if right:
                parent_hash = sha256(left.hash + right.hash).data  # Extract bytes
            else:
                # Odd number of nodes - promote single child
                parent_hash = left.hash

            parent = MerkleNode(
                hash=parent_hash,
                left=left,
                right=right,
            )

            left.parent = parent
            if right:
                right.parent = parent

            parents.append(parent)

        # Recurse until we have single root
        return self._build_tree(parents)

    def update_channel(self, channel_id: ChannelId, new_state: StateValue) -> None:
        """Update a single channel state and recompute path to root.

        O(log N) complexity - only recomputes hashes along path.
        """
        leaf = self.leaf_map.get(channel_id.value)
        if not leaf:
            raise ValueError(f"Channel {channel_id.value.hex()[:8]} not in tree")

        # Update leaf hash and state
        leaf.hash = sha256(new_state.data).data  # Extract bytes from StateValue
        leaf.state_value = new_state  # Store new state for rebuilds

        # Recompute path to root
        self._recompute_path(leaf)

    def add_channel(self, channel_id: ChannelId, initial_state: StateValue) -> None:
        """Add a new channel to the tree.

        Rebuilds tree to maintain balance. O(N) but called infrequently.
        """
        if channel_id.value in self.leaf_map:
            raise ValueError(f"Channel {channel_id.value.hex()[:8]} already exists")

        # Add to ordered list
        self._ordered_channels.append(channel_id)
        self._ordered_channels.sort(key=lambda ch: ch.value)

        # Rebuild tree (expensive but maintains balance)
        channel_states = [
            (ch_id, self.leaf_map[ch_id.value].state_value)
            if ch_id.value in self.leaf_map
            else (channel_id, initial_state)
            for ch_id in self._ordered_channels
        ]

        # Clear and rebuild
        new_tree = IncrementalSg.from_channel_states(channel_states)
        self.root = new_tree.root
        self.leaf_map = new_tree.leaf_map
        self._ordered_channels = new_tree._ordered_channels

    def remove_channel(self, channel_id: ChannelId) -> None:
        """Remove a channel from the tree.

        Rebuilds tree to maintain balance. O(N) but called infrequently.
        """
        if channel_id.value not in self.leaf_map:
            raise ValueError(f"Channel {channel_id.value.hex()[:8]} not in tree")

        # Remove from structures
        del self.leaf_map[channel_id.value]
        self._ordered_channels.remove(channel_id)

        if not self._ordered_channels:
            self.root = None
            return

        # Rebuild tree
        channel_states = [
            (ch_id, self.leaf_map[ch_id.value].state_value)
            for ch_id in self._ordered_channels
        ]

        new_tree = IncrementalSg.from_channel_states(channel_states)
        self.root = new_tree.root
        self.leaf_map = new_tree.leaf_map

    def _recompute_path(self, node: MerkleNode) -> None:
        """Recompute hashes from node to root."""
        current = node.parent

        while current is not None:
            # Recompute hash from children
            if current.right:
                current.hash = sha256(current.left.hash + current.right.hash).data  # Extract bytes
            else:
                # Single child (odd tree)
                current.hash = current.left.hash

            current = current.parent

    def get_root_hash(self) -> GlobalState:
        """Get current global state (Merkle root).

        Returns Sg value compatible with compose_ordered().
        """
        if self.root is None:
            raise ValueError("Cannot get root hash from empty tree")

        return GlobalState(value=StateValue(self.root.hash))

    def channel_count(self) -> int:
        """Get number of channels in tree."""
        return len(self.leaf_map)


def compose_ordered_incremental(
    channel_states: Sequence[tuple[ChannelId, StateValue]],
) -> GlobalState:
    """Compute global state using Merkle tree (one-shot).

    For compatibility with existing code that expects compose_ordered().
    Use IncrementalSg directly for incremental updates.
    """
    if not channel_states:
        raise ValueError("Cannot compose global state from zero states")

    tree = IncrementalSg.from_channel_states(channel_states)
    return tree.get_root_hash()
