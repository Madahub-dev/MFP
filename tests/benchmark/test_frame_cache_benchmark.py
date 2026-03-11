"""Benchmark tests for frame caching (P3.2).

Measures performance improvement from caching deterministic cross-runtime frames.
"""

import time

import pytest

from mfp.core.frame import (
    clear_frame_cache,
    configure_frame_cache,
    get_frame_cache_stats,
    sample_frame_cross_runtime,
)
from mfp.core.types import StateValue


class TestFrameCacheBenchmark:
    """Benchmark tests for frame cache performance."""

    def setup_method(self):
        """Clear cache and configure for benchmarking."""
        configure_frame_cache(maxsize=1000)
        clear_frame_cache()

    def test_cache_speedup_repeated_frames(self):
        """Measure speedup from caching repeated frame derivations."""
        local_state = StateValue(b"local" + b"\x00" * 27)
        step = 42
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # Warmup
        sample_frame_cross_runtime(local_state, step, bilateral_state, prng_seed)
        clear_frame_cache()

        iterations = 1000

        # Without cache
        start = time.perf_counter()
        for _ in range(iterations):
            sample_frame_cross_runtime(
                local_state, step, bilateral_state, prng_seed, use_cache=False
            )
        uncached_time = time.perf_counter() - start

        # With cache (same inputs)
        clear_frame_cache()
        start = time.perf_counter()
        for _ in range(iterations):
            sample_frame_cross_runtime(
                local_state, step, bilateral_state, prng_seed, use_cache=True
            )
        cached_time = time.perf_counter() - start

        # Cache should be faster
        speedup = uncached_time / cached_time
        print(f"\nCache speedup (repeated frames): {speedup:.2f}x")
        print(f"Uncached: {uncached_time*1000:.2f}ms")
        print(f"Cached: {cached_time*1000:.2f}ms")

        # Expect significant speedup for repeated frames
        assert speedup > 2.0, f"Expected >2x speedup, got {speedup:.2f}x"

        # Check cache stats
        hits, misses, hit_rate = get_frame_cache_stats()
        print(f"Cache hits: {hits}, misses: {misses}, hit rate: {hit_rate:.2%}")
        assert hit_rate > 0.99  # Should have ~100% hit rate

    def test_cache_overhead_unique_frames(self):
        """Measure cache overhead for unique frame derivations."""
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        iterations = 100

        # Without cache - all unique frames
        start = time.perf_counter()
        for i in range(iterations):
            local_state = StateValue((i.to_bytes(4, 'big') + b"\x00" * 28))
            sample_frame_cross_runtime(
                local_state, i, bilateral_state, prng_seed, use_cache=False
            )
        uncached_time = time.perf_counter() - start

        # With cache - all unique frames (all misses)
        clear_frame_cache()
        start = time.perf_counter()
        for i in range(iterations):
            local_state = StateValue((i.to_bytes(4, 'big') + b"\x00" * 28))
            sample_frame_cross_runtime(
                local_state, i, bilateral_state, prng_seed, use_cache=True
            )
        cached_time = time.perf_counter() - start

        overhead = (cached_time - uncached_time) / uncached_time * 100
        print(f"\nCache overhead (all misses): {overhead:.1f}%")
        print(f"Uncached: {uncached_time*1000:.2f}ms")
        print(f"Cached (all misses): {cached_time*1000:.2f}ms")

        # Cache overhead should be reasonable (<50%)
        # Note: Overhead includes key creation, hashing, lookup, and LRU updates
        # This is acceptable given the 16x+ speedup for cache hits
        assert overhead < 50.0, f"Cache overhead too high: {overhead:.1f}%"

        # Check cache stats - should be all misses
        hits, misses, hit_rate = get_frame_cache_stats()
        print(f"Cache hits: {hits}, misses: {misses}, hit rate: {hit_rate:.2%}")
        assert hit_rate == 0.0  # All misses

    def test_realistic_workload_mixed_hit_rate(self):
        """Simulate realistic workload with mixed cache hit/miss."""
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        # Simulate 10 channels with repeated state accesses
        num_channels = 10
        accesses_per_channel = 50
        iterations = num_channels * accesses_per_channel

        # Without cache
        start = time.perf_counter()
        for i in range(iterations):
            channel_id = i % num_channels
            local_state = StateValue((channel_id.to_bytes(4, 'big') + b"\x00" * 28))
            sample_frame_cross_runtime(
                local_state, channel_id, bilateral_state, prng_seed, use_cache=False
            )
        uncached_time = time.perf_counter() - start

        # With cache
        clear_frame_cache()
        start = time.perf_counter()
        for i in range(iterations):
            channel_id = i % num_channels
            local_state = StateValue((channel_id.to_bytes(4, 'big') + b"\x00" * 28))
            sample_frame_cross_runtime(
                local_state, channel_id, bilateral_state, prng_seed, use_cache=True
            )
        cached_time = time.perf_counter() - start

        speedup = uncached_time / cached_time
        print(f"\nRealistic workload speedup: {speedup:.2f}x")
        print(f"Uncached: {uncached_time*1000:.2f}ms")
        print(f"Cached: {cached_time*1000:.2f}ms")

        # Should have good speedup with ~98% hit rate
        hits, misses, hit_rate = get_frame_cache_stats()
        print(f"Cache hits: {hits}, misses: {misses}, hit rate: {hit_rate:.2%}")

        assert hit_rate > 0.95  # Expect >95% hit rate
        assert speedup > 1.5, f"Expected >1.5x speedup, got {speedup:.2f}x"

    def test_cache_size_impact(self):
        """Measure impact of cache size on hit rate."""
        bilateral_state = StateValue(b"bilateral" + b"\x00" * 23)
        prng_seed = StateValue(b"prng" + b"\x00" * 28)

        num_unique_frames = 100
        accesses_per_frame = 10

        results = []

        for cache_size in [10, 50, 100, 500]:
            configure_frame_cache(maxsize=cache_size)
            clear_frame_cache()

            # Access pattern: cycle through unique frames
            for _ in range(accesses_per_frame):
                for i in range(num_unique_frames):
                    local_state = StateValue((i.to_bytes(4, 'big') + b"\x00" * 28))
                    sample_frame_cross_runtime(
                        local_state, i, bilateral_state, prng_seed
                    )

            hits, misses, hit_rate = get_frame_cache_stats()
            results.append((cache_size, hit_rate))
            print(f"Cache size {cache_size}: hit rate {hit_rate:.2%}")

        # Larger cache should have better hit rate
        for i in range(len(results) - 1):
            assert results[i+1][1] >= results[i][1], \
                f"Larger cache should have >= hit rate"

        # Full cache (size >= unique frames) should have near-perfect hit rate
        full_cache_hit_rate = results[-1][1]
        assert full_cache_hit_rate > 0.85, \
            f"Full cache should have >85% hit rate, got {full_cache_hit_rate:.2%}"
