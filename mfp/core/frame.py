"""MFP Frame Engine — derivation, sampling, validation, assembly.

Two sampling modes (intra-runtime with OS jitter, cross-runtime with
per-step PRNG jitter) sharing one distribution seed derivation.

Maps to: impl/I-04_frame.md
"""

from __future__ import annotations

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
) -> Frame:
    """Sample a frame using per-step PRNG jitter (cross-runtime).

    Both runtimes produce identical frames deterministically.
    Idempotent per step — repeated derivation yields the same frame.

    Maps to: spec.md §5.4.
    """
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

    return Frame(tuple(blocks))


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
