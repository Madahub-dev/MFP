"""Message deduplication for replay protection (P3.4).

Tracks recent message IDs per channel to detect and reject duplicates.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from mfp.core.types import ChannelId, MessageId


@dataclass
class DeduplicationConfig:
    """Configuration for message deduplication."""
    window_size: int = 1000  # Track last N messages per channel
    ttl_seconds: float = 300.0  # 5 minutes - messages older than this are evicted


@dataclass
class MessageRecord:
    """Record of a seen message."""
    message_id: bytes
    timestamp: float


class DeduplicationTracker:
    """Tracks recent messages per channel for duplicate detection.

    Uses a sliding window approach:
    - Stores last N message IDs per channel
    - Evicts messages older than TTL
    - O(1) duplicate check with set lookup
    """

    def __init__(self, config: DeduplicationConfig | None = None):
        self.config = config or DeduplicationConfig()
        # channel_id -> deque of MessageRecords (FIFO)
        self._recent_messages: dict[bytes, deque[MessageRecord]] = {}
        # channel_id -> set of message_id bytes for O(1) lookup
        self._message_sets: dict[bytes, set[bytes]] = {}

    def is_duplicate(self, channel_id: ChannelId, message_id: MessageId) -> bool:
        """Check if message_id has been seen recently on this channel.

        Also performs cleanup of old messages beyond TTL.

        Returns:
            True if duplicate (message_id seen before), False otherwise
        """
        ch_id_bytes = channel_id.value
        msg_id_bytes = message_id.value

        # Initialize channel tracking if not exists
        if ch_id_bytes not in self._recent_messages:
            self._recent_messages[ch_id_bytes] = deque(maxlen=self.config.window_size)
            self._message_sets[ch_id_bytes] = set()

        # Evict old messages beyond TTL
        self._evict_old_messages(ch_id_bytes)

        # Check for duplicate
        if msg_id_bytes in self._message_sets[ch_id_bytes]:
            return True

        # Not a duplicate - record it for future checks
        return self._record_message(ch_id_bytes, msg_id_bytes)

    def _record_message(self, channel_id_bytes: bytes, message_id_bytes: bytes) -> bool:
        """Record a new message. Returns False (not a duplicate)."""
        now = time.time()
        record = MessageRecord(message_id=message_id_bytes, timestamp=now)

        recent_deque = self._recent_messages[channel_id_bytes]
        msg_set = self._message_sets[channel_id_bytes]

        # Check if deque is at capacity
        evicted_msg_id = None
        if len(recent_deque) == self.config.window_size:
            # Deque is full - appending will evict the leftmost (oldest)
            evicted_msg_id = recent_deque[0].message_id

        # Append new record (may evict oldest if at maxlen)
        recent_deque.append(record)

        # Remove evicted from set if something was evicted
        if evicted_msg_id is not None:
            msg_set.discard(evicted_msg_id)

        # Add new to set
        msg_set.add(message_id_bytes)

        return False  # Not a duplicate

    def _evict_old_messages(self, channel_id_bytes: bytes) -> None:
        """Remove messages older than TTL from the channel's tracking."""
        if channel_id_bytes not in self._recent_messages:
            return

        now = time.time()
        cutoff = now - self.config.ttl_seconds

        recent = self._recent_messages[channel_id_bytes]
        msg_set = self._message_sets[channel_id_bytes]

        # Evict from left (oldest) while timestamps are too old
        while recent and recent[0].timestamp < cutoff:
            evicted = recent.popleft()
            msg_set.discard(evicted.message_id)

    def clear_channel(self, channel_id: ChannelId) -> None:
        """Clear all tracked messages for a channel (e.g., when channel closes)."""
        ch_id_bytes = channel_id.value
        self._recent_messages.pop(ch_id_bytes, None)
        self._message_sets.pop(ch_id_bytes, None)

    def get_tracked_count(self, channel_id: ChannelId) -> int:
        """Get number of messages currently tracked for a channel."""
        ch_id_bytes = channel_id.value
        if ch_id_bytes in self._recent_messages:
            return len(self._recent_messages[ch_id_bytes])
        return 0
