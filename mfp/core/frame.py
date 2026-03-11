"""MFP Frame Engine — derivation, sampling, validation, assembly.

Two sampling modes (intra-runtime with OS jitter, cross-runtime with
per-step PRNG jitter) sharing one distribution seed derivation.

Frame caching (P3.2) for cross-runtime frames to avoid redundant
ChaCha20 keystream generation.

Maps to: impl/I-04_frame.md
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from mfp.core.primitives import (
    ChaCha20PRNG,
    constant_time_equal,
    encode_u64_be,
    hmac_sha256,
    random_bytes,
)
from mfp.core.types import (
    BLOCK_SIZE,
    DEFAULT_FRAME_DEPTH,
    Block,
    Frame,
    ProtocolMessage,
    StateValue,
)


# ---------------------------------------------------------------------------
# Frame Cache (P3.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrameCacheKey:
    """Cache key for deterministic frame sampling.

    Only used for cross-runtime frames (deterministic).
    Intra-runtime frames use OS jitter and cannot be cached.
    """
    local_state: bytes  # Sl (StateValue.data)
    step: int  # t
    bilateral_ratchet_state: bytes  # Acts as Sg for cross-runtime
    shared_prng_seed: bytes  # For jitter derivation
    depth: int


class FrameCache:
    """LRU cache for cross-runtime frame sampling.

    Caches deterministic frames to avoid redundant ChaCha20 computations.
    Note: Cache hit rate depends on state reuse patterns.
    """

    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize
        self._cache: dict[FrameCacheKey, Frame] = {}
        self._hits = 0
        self._misses = 0
        self._access_order: list[FrameCacheKey] = []

    def get(self, key: FrameCacheKey) -> Frame | None:
        """Get cached frame if exists."""
        if key in self._cache:
            self._hits += 1
            # Update LRU order
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, key: FrameCacheKey, frame: Frame) -> None:
        """Cache a frame with LRU eviction."""
        if key in self._cache:
            # Update existing
            self._access_order.remove(key)
            self._access_order.append(key)
            self._cache[key] = frame
            return

        # Add new entry
        if len(self._cache) >= self.maxsize:
            # Evict LRU
            lru_key = self._access_order.pop(0)
            del self._cache[lru_key]

        self._cache[key] = frame
        self._access_order.append(key)

    def get_stats(self) -> tuple[int, int, float]:
        """Get cache statistics: (hits, misses, hit_rate).

        Returns (0, 0, 0.0) if no accesses yet.
        """
        total = self._hits + self._misses
        if total == 0:
            return 0, 0, 0.0
        hit_rate = self._hits / total
        return self._hits, self._misses, hit_rate

    def clear(self) -> None:
        """Clear cache and reset statistics."""
        self._cache.clear()
        self._access_order.clear()
        self._hits = 0
        self._misses = 0


# Global frame cache (can be configured)
_frame_cache = FrameCache(maxsize=1000)


def configure_frame_cache(maxsize: int) -> None:
    """Configure global frame cache size."""
    global _frame_cache
    _frame_cache = FrameCache(maxsize=maxsize)


def get_frame_cache_stats() -> tuple[int, int, float]:
    """Get global frame cache statistics."""
    return _frame_cache.get_stats()


def clear_frame_cache() -> None:
    """Clear global frame cache."""
    _frame_cache.clear()


# ---------------------------------------------------------------------------
# XOR Helper
# ---------------------------------------------------------------------------

def xor_bytes(a: bytes, b: bytes) -> bytes:
    """XOR two byte strings of equal length."""
    if len(a) != len(b):
        raise ValueError(f"XOR requires equal lengths, got {len(a)} and {len(b)}")
    return bytes(x ^ y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Distribution Seed Derivation
# ---------------------------------------------------------------------------

def derive_distribution_seed(
    local_state: StateValue,
    step: int,
    global_state: StateValue,
) -> StateValue:
    """Derive the distribution seed ds for frame derivation.

    Computes: ds = HMAC-SHA-256(key: Sl, message: encode_u64_be(t) || Sg)

    Maps to: spec.md §5.2, Stage 1.
    """
    message = encode_u64_be(step) + global_state.data
    return hmac_sha256(local_state.data, message)


# ---------------------------------------------------------------------------
# Frame Sampling — Intra-Runtime
# ---------------------------------------------------------------------------

def sample_frame(
    local_state: StateValue,
    step: int,
    global_state: StateValue,
    depth: int = DEFAULT_FRAME_DEPTH,
) -> Frame:
    """Sample a frame using OS CSPRNG jitter (intra-runtime).

    Maps to: spec.md §5.3.
    """
    ds = derive_distribution_seed(local_state, step, global_state)
    prng = ChaCha20PRNG(ds)

    blocks: list[Block] = []
    for _ in range(depth):
        candidate = prng.next_bytes(BLOCK_SIZE)
        jitter = random_bytes(BLOCK_SIZE)
        block_data = xor_bytes(candidate, jitter)
        blocks.append(Block(block_data))

    return Frame(tuple(blocks))


# ---------------------------------------------------------------------------
# Frame Sampling — Cross-Runtime
# ---------------------------------------------------------------------------

def sample_frame_cross_runtime(
    local_state: StateValue,
    step: int,
    bilateral_ratchet_state: StateValue,
    shared_prng_seed: StateValue,
    depth: int = DEFAULT_FRAME_DEPTH,
    use_cache: bool = True,
) -> Frame:
    """Sample a frame using per-step PRNG jitter (cross-runtime).

    Both runtimes produce identical frames deterministically.
    Idempotent per step — repeated derivation yields the same frame.

    Caching (P3.2): Caches deterministic frames to avoid redundant
    ChaCha20 keystream generation. Cache hit rate depends on state
    reuse patterns.

    Args:
        local_state: Local channel state (Sl)
        step: Channel step counter (t)
        bilateral_ratchet_state: Bilateral ratchet state (acts as Sg)
        shared_prng_seed: Shared PRNG seed for jitter derivation
        depth: Frame depth (number of blocks)
        use_cache: Whether to use frame cache (default: True)

    Returns:
        Sampled frame

    Maps to: spec.md §5.4.
    """
    # Check cache first
    if use_cache:
        cache_key = FrameCacheKey(
            local_state=local_state.data,
            step=step,
            bilateral_ratchet_state=bilateral_ratchet_state.data,
            shared_prng_seed=shared_prng_seed.data,
            depth=depth,
        )
        cached_frame = _frame_cache.get(cache_key)
        if cached_frame is not None:
            return cached_frame

    # Cache miss or caching disabled - compute frame
    # Distribution seed — uses bilateral ratchet_state as Sg substitute
    ds = derive_distribution_seed(local_state, step, bilateral_ratchet_state)
    prng = ChaCha20PRNG(ds)

    # Per-step jitter PRNG — deterministic, shared between both runtimes
    jitter_seed = hmac_sha256(shared_prng_seed.data, encode_u64_be(step))
    jitter_prng = ChaCha20PRNG(jitter_seed)

    blocks: list[Block] = []
    for _ in range(depth):
        candidate = prng.next_bytes(BLOCK_SIZE)
        jitter = jitter_prng.next_bytes(BLOCK_SIZE)
        block_data = xor_bytes(candidate, jitter)
        blocks.append(Block(block_data))

    frame = Frame(tuple(blocks))

    # Store in cache
    if use_cache:
        _frame_cache.put(cache_key, frame)

    return frame


# ---------------------------------------------------------------------------
# Frame Validation
# ---------------------------------------------------------------------------

def validate_frame(
    frame_open: Frame,
    frame_close: Frame,
    expected_frame: Frame,
) -> bool:
    """Validate a message's frame pair.

    Check 1: Mirror symmetry (not constant-time — public structure).
    Check 2: State match (constant-time — prevents timing side channels).

    Maps to: spec.md §3.4, runtime-interface.md §9.5.
    """
    # Check 1: frame_close must be mirror(frame_open)
    if frame_close != frame_open.mirror():
        return False

    # Check 2: frame_open must match expected (constant-time)
    return constant_time_equal(
        frame_open.to_bytes(),
        expected_frame.to_bytes(),
    )


# ---------------------------------------------------------------------------
# Message Assembly
# ---------------------------------------------------------------------------

def assemble_message(
    frame: Frame,
    encoded_payload: bytes,
) -> ProtocolMessage:
    """Assemble a complete protocol message: frame_open || E(P) || frame_close.

    Maps to: spec.md §3.5, runtime-interface.md §9.4.
    """
    return ProtocolMessage(
        frame_open=frame,
        encoded_payload=encoded_payload,
        frame_close=frame.mirror(),
    )
