"""Unit tests for frame caching (P3.2)."""

import pytest

from mfp.core.frame import (
    FrameCache,
    FrameCacheKey,
    clear_frame_cache,
    configure_frame_cache,
    get_frame_cache_stats,
    sample_frame_cross_runtime,
)
from mfp.core.types import StateValue


class TestFrameCache:
    """Tests for frame cache implementation."""

    def test_cache_starts_empty(self):
        """New cache should be empty."""
        cache = FrameCache()
        hits, misses, hit_rate = cache.get_stats()

        assert hits == 0
        assert misses == 0
        assert hit_rate == 0.0

    def test_cache_miss(self):
        """Cache miss should increment misses."""
        cache = FrameCache()

        key = FrameCacheKey(
            local_state=b"a" * 32,
            step=0,
            bilateral_ratchet_state=b"b" * 32,
            shared_prng_seed=b"c" * 32,
            depth=4,
        )

        result = cache.get(key)
        assert result is None

        hits, misses, hit_rate = cache.get_stats()
        assert hits == 0
        assert misses == 1
        assert hit_rate == 0.0

    def test_cache_hit(self):
        """Cache hit should return cached value."""
        from mfp.core.types import Block, Frame

        cache = FrameCache()

        key = FrameCacheKey(
            local_state=b"a" * 32,
            step=0,
            bilateral_ratchet_state=b"b" * 32,
            shared_prng_seed=b"c" * 32,
            depth=4,
        )

        # Create a frame
        blocks = tuple(Block(b"x" * 16) for _ in range(4))
        frame = Frame(blocks)

        # Put in cache
        cache.put(key, frame)

        # Get from cache
        cached_frame = cache.get(key)
        assert cached_frame is not None
        assert cached_frame == frame

        hits, misses, hit_rate = cache.get_stats()
        assert hits == 1
        assert misses == 0
        assert hit_rate == 1.0

    def test_cache_lru_eviction(self):
        """Cache should evict LRU entries when full."""
        from mfp.core.types import Block, Frame

        cache = FrameCache(maxsize=3)

        frames = []
        keys = []
        for i in range(4):
            key = FrameCacheKey(
                local_state=(i.to_bytes(1, 'big') * 32)[:32],
                step=i,
                bilateral_ratchet_state=b"b" * 32,
                shared_prng_seed=b"c" * 32,
                depth=4,
            )
            blocks = tuple(Block((i.to_bytes(1, 'big') * 16)[:16]) for _ in range(4))
            frame = Frame(blocks)

            keys.append(key)
            frames.append(frame)

            cache.put(key, frame)

        # First key should be evicted (LRU)
        assert cache.get(keys[0]) is None  # Evicted

        # Others should still be cached
        assert cache.get(keys[1]) is not None
        assert cache.get(keys[2]) is not None
        assert cache.get(keys[3]) is not None

    def test_cache_lru_order_updated_on_access(self):
        """Accessing cached entry should update LRU order."""
        from mfp.core.types import Block, Frame

        cache = FrameCache(maxsize=2)

        key1 = FrameCacheKey(
            local_state=b"a" * 32,
            step=1,
            bilateral_ratchet_state=b"b" * 32,
            shared_prng_seed=b"c" * 32,
            depth=4,
        )
        key2 = FrameCacheKey(
            local_state=b"d" * 32,
            step=2,
            bilateral_ratchet_state=b"e" * 32,
            shared_prng_seed=b"f" * 32,
            depth=4,
        )
        key3 = FrameCacheKey(
            local_state=b"g" * 32,
            step=3,
            bilateral_ratchet_state=b"h" * 32,
            shared_prng_seed=b"i" * 32,
            depth=4,
        )

        blocks = tuple(Block(b"x" * 16) for _ in range(4))
        frame = Frame(blocks)

        # Add key1 and key2
        cache.put(key1, frame)
        cache.put(key2, frame)

        # Access key1 (makes it most recently used)
        cache.get(key1)

        # Add key3 (should evict key2, not key1)
        cache.put(key3, frame)

        # key1 should still be cached
        assert cache.get(key1) is not None

        # key2 should be evicted
        assert cache.get(key2) is None

        # key3 should be cached
        assert cache.get(key3) is not None

    def test_cache_clear(self):
        """Clear should remove all entries and reset stats."""
        from mfp.core.types import Block, Frame

        cache = FrameCache()

        key = FrameCacheKey(
            local_state=b"a" * 32,
            step=0,
            bilateral_ratchet_state=b"b" * 32,
            shared_prng_seed=b"c" * 32,
            depth=4,
        )

        blocks = tuple(Block(b"x" * 16) for _ in range(4))
        frame = Frame(blocks)

        cache.put(key, frame)
        cache.get(key)  # Hit

        hits, misses, _ = cache.get_stats()
        assert hits > 0

        cache.clear()

        # Stats reset
        hits, misses, hit_rate = cache.get_stats()
        assert hits == 0
        assert misses == 0
        assert hit_rate == 0.0

        # Cache empty
        assert cache.get(key) is None

    def test_cache_hit_rate_calculation(self):
        """Hit rate should be correctly calculated."""
        from mfp.core.types import Block, Frame

        cache = FrameCache()

        key = FrameCacheKey(
            local_state=b"a" * 32,
            step=0,
            bilateral_ratchet_state=b"b" * 32,
            shared_prng_seed=b"c" * 32,
            depth=4,
        )

        blocks = tuple(Block(b"x" * 16) for _ in range(4))
        frame = Frame(blocks)

        # 1 miss
        cache.get(key)

        # Add to cache
        cache.put(key, frame)

        # 3 hits
        cache.get(key)
        cache.get(key)
        cache.get(key)

        hits, misses, hit_rate = cache.get_stats()
        assert hits == 3
        assert misses == 1
        assert hit_rate == 0.75  # 3 / (3 + 1)


class TestSampleFrameCrossRuntimeCaching:
    """Tests for frame caching in sample_frame_cross_runtime."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_frame_cache()

    def test_same_inputs_produce_cache_hit(self):
        """Same inputs should produce cache hit."""
        local_state = StateValue(b"local" + b"\x00" * 27)
        step = 42
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)
        depth = 4

        # First call - cache miss
        frame1 = sample_frame_cross_runtime(
            local_state, step, bilateral_state, prng_seed, depth
        )

        # Second call with same inputs - cache hit
        frame2 = sample_frame_cross_runtime(
            local_state, step, bilateral_state, prng_seed, depth
        )

        # Frames should be identical
        assert frame1 == frame2

        # Check cache stats
        hits, misses, hit_rate = get_frame_cache_stats()
        assert hits == 1
        assert misses == 1
        assert hit_rate == 0.5

    def test_different_local_state_no_cache_hit(self):
        """Different local_state should not hit cache."""
        local_state1 = StateValue(b"local1" + b"\x00" * 26)
        local_state2 = StateValue(b"local2" + b"\x00" * 26)
        step = 42
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        frame1 = sample_frame_cross_runtime(
            local_state1, step, bilateral_state, prng_seed
        )
        frame2 = sample_frame_cross_runtime(
            local_state2, step, bilateral_state, prng_seed
        )

        # Frames should be different
        assert frame1 != frame2

        # Both should be cache misses
        hits, misses, _ = get_frame_cache_stats()
        assert hits == 0
        assert misses == 2

    def test_different_step_no_cache_hit(self):
        """Different step should not hit cache."""
        local_state = StateValue(b"local" + b"\x00" * 27)
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        frame1 = sample_frame_cross_runtime(
            local_state, 1, bilateral_state, prng_seed
        )
        frame2 = sample_frame_cross_runtime(
            local_state, 2, bilateral_state, prng_seed
        )

        # Frames should be different
        assert frame1 != frame2

        # Both should be cache misses
        hits, misses, _ = get_frame_cache_stats()
        assert hits == 0
        assert misses == 2

    def test_cache_disabled(self):
        """Cache can be disabled."""
        local_state = StateValue(b"local" + b"\x00" * 27)
        step = 42
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # First call with cache disabled
        frame1 = sample_frame_cross_runtime(
            local_state, step, bilateral_state, prng_seed, use_cache=False
        )

        # Second call with cache disabled
        frame2 = sample_frame_cross_runtime(
            local_state, step, bilateral_state, prng_seed, use_cache=False
        )

        # Frames should be identical (deterministic)
        assert frame1 == frame2

        # No cache activity
        hits, misses, _ = get_frame_cache_stats()
        assert hits == 0
        assert misses == 0

    def test_configure_frame_cache_size(self):
        """Frame cache size should be configurable."""
        configure_frame_cache(maxsize=2)

        local_state_base = b"local" + b"\x00" * 27
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # Generate 3 different frames
        for i in range(3):
            local_state = StateValue((local_state_base[:5] + i.to_bytes(1, 'big') + local_state_base[6:]))
            sample_frame_cross_runtime(local_state, i, bilateral_state, prng_seed)

        # First frame should be evicted (cache size = 2)
        local_state1 = StateValue((local_state_base[:5] + b'\x00' + local_state_base[6:]))
        sample_frame_cross_runtime(local_state1, 0, bilateral_state, prng_seed)

        # Should be a miss (evicted)
        hits, misses, _ = get_frame_cache_stats()
        assert misses == 4  # 3 initial + 1 re-access

    def test_deterministic_cross_runtime_frames_cacheable(self):
        """Cross-runtime frames are deterministic and cacheable."""
        local_state = StateValue(b"local" + b"\x00" * 27)
        step = 100
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # Generate same frame 10 times
        frames = []
        for _ in range(10):
            frame = sample_frame_cross_runtime(
                local_state, step, bilateral_state, prng_seed
            )
            frames.append(frame)

        # All frames should be identical
        assert all(f == frames[0] for f in frames)

        # Should have 1 miss and 9 hits
        hits, misses, hit_rate = get_frame_cache_stats()
        assert misses == 1
        assert hits == 9
        assert hit_rate == 0.9
