"""MFP Bilateral State — bootstrap, DH ceremony, cross-runtime channels.

Manages bilateral state S_AB between runtime pairs. Deterministic bootstrap
for same-trust-domain, ceremonial (X25519 DH) for cross-organizational.

Maps to: impl/I-15_bilateral.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from mfp.core.primitives import hmac_sha256
from mfp.core.ratchet import bilateral_advance, bilateral_seed
from mfp.core.types import (
    BilateralState,
    ChannelId,
    ChannelState,
    Frame,
    RuntimeId,
    StateValue,
)
from mfp.federation.rotation import RotationConfig, RotationSession, RotationTrigger
from mfp.observability.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


# ---------------------------------------------------------------------------
# Bootstrap — Deterministic (same trust domain)
# ---------------------------------------------------------------------------

def bootstrap_deterministic(
    runtime_a: RuntimeId,
    runtime_b: RuntimeId,
) -> BilateralState:
    """Derive S_AB₀ deterministically from runtime identities.

    Both runtimes compute identical state independently.
    No initial secrecy — security accumulates through ratcheting.

    Maps to: federation.md §3.
    """
    return bilateral_seed(runtime_a, runtime_b)


# ---------------------------------------------------------------------------
# Bootstrap — Ceremonial (cross-organizational, DH)
# ---------------------------------------------------------------------------

def generate_dh_keypair() -> tuple[bytes, bytes]:
    """Generate X25519 keypair.

    Returns (private_key_bytes, public_key_bytes), each 32 bytes.
    """
    private = X25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes_raw()
    private_bytes = private.private_bytes_raw()
    return private_bytes, public_bytes


def compute_shared_secret(
    private_key: bytes,
    peer_public: bytes,
) -> bytes:
    """Compute X25519 shared secret from private key and peer's public key.

    Returns 32 bytes.
    """
    private = X25519PrivateKey.from_private_bytes(private_key)
    peer = X25519PublicKey.from_public_bytes(peer_public)
    return private.exchange(peer)


def bootstrap_ceremonial(
    runtime_a: RuntimeId,
    runtime_b: RuntimeId,
    shared_secret: bytes,
) -> BilateralState:
    """Derive S_AB₀ from a DH shared secret + runtime identities.

    Provides initial confidentiality. Requires out-of-band
    identity exchange and DH ceremony.

    Maps to: federation.md §4.
    """
    id_a, id_b = runtime_a.value.data, runtime_b.value.data
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    message = id_a + id_b
    ratchet_state = hmac_sha256(shared_secret, b"mfp-bilateral" + message)
    prng_seed = hmac_sha256(shared_secret, b"mfp-bilateral-prng" + message)

    return BilateralState(
        ratchet_state=ratchet_state,
        shared_prng_seed=prng_seed,
        step=0,
    )


# ---------------------------------------------------------------------------
# State Advancement
# ---------------------------------------------------------------------------

def advance_bilateral_state(
    state: BilateralState,
    frame: Frame,
) -> BilateralState:
    """Advance bilateral state after successful exchange.

    Delegates to core ratchet bilateral_advance.

    Maps to: spec.md §7.5.
    """
    return bilateral_advance(state, frame)


# ---------------------------------------------------------------------------
# Pending Advance (implicit acknowledgment tracking)
# ---------------------------------------------------------------------------

@dataclass
class PendingAdvance:
    """Tracks unacknowledged bilateral advancement.

    The sending runtime records this after sending a cross-runtime message.
    It is cleared when the peer's response validates successfully.
    """
    bilateral_id: bytes
    pre_advance_state: BilateralState
    expected_post_state: BilateralState
    frame: Frame
    step: int


# ---------------------------------------------------------------------------
# Cross-Runtime Channel
# ---------------------------------------------------------------------------

@dataclass
class CrossRuntimeChannel:
    """An agent channel that spans two runtimes.

    Links a local agent channel to its bilateral channel for
    cross-runtime frame sampling and Sg incorporation.
    """
    channel_id: ChannelId
    local_agent_id: bytes
    remote_agent_id: bytes
    bilateral_id: bytes
    state: ChannelState
    depth: int


# ---------------------------------------------------------------------------
# Bilateral Channel Registry
# ---------------------------------------------------------------------------

@dataclass
class BilateralChannel:
    """A bilateral channel between two runtimes.

    Manages the shared state and tracks pending acknowledgments.

    Circuit breaker (P3.1):
    - Tracks failures per bilateral channel
    - Opens after N consecutive failures
    - Prevents cascading failures from problematic peers

    Key rotation (P3.3):
    - Tracks message count and time since last rotation
    - Supports automatic rotation based on thresholds
    - X25519 DH-based rekey protocol
    """
    bilateral_id: bytes
    local_runtime: RuntimeId
    peer_runtime: RuntimeId
    state: BilateralState
    status: str = "active"  # active, recovery, suspended
    pending: PendingAdvance | None = None
    cross_runtime_channels: list[CrossRuntimeChannel] = field(
        default_factory=list,
    )
    _circuit_breaker: CircuitBreaker | None = field(default=None, init=False)
    _rotation_trigger: RotationTrigger = field(default_factory=RotationTrigger)
    _rotation_session: RotationSession = field(default_factory=RotationSession)
    _rotation_config: RotationConfig = field(default_factory=RotationConfig)

    def get_circuit_breaker(self, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        """Get or create circuit breaker for this bilateral channel."""
        if self._circuit_breaker is None:
            name = f"bilateral_{self.bilateral_id.hex()[:8]}"
            self._circuit_breaker = CircuitBreaker(name, config)
        return self._circuit_breaker

    def configure_rotation(self, config: RotationConfig) -> None:
        """Configure key rotation parameters."""
        self._rotation_config = config

    def should_rotate(self) -> bool:
        """Check if key rotation should be triggered.

        Returns True if rotation is needed based on configured thresholds.
        """
        reason = self._rotation_trigger.should_rotate(self._rotation_config)
        if reason:
            self._rotation_session.reason = reason
            return True
        return False

    def increment_message_count(self) -> None:
        """Increment message counter (call after each bilateral exchange)."""
        self._rotation_trigger.increment_message_count()

    def get_rotation_trigger(self) -> RotationTrigger:
        """Get rotation trigger for inspection/testing."""
        return self._rotation_trigger

    def get_rotation_session(self) -> RotationSession:
        """Get active rotation session for protocol handling."""
        return self._rotation_session


def derive_bilateral_id(
    runtime_a: RuntimeId,
    runtime_b: RuntimeId,
) -> bytes:
    """Derive a deterministic bilateral channel ID from sorted runtime IDs.

    Returns SHA-256(sorted_id_a || sorted_id_b).
    """
    id_a, id_b = runtime_a.value.data, runtime_b.value.data
    if id_a > id_b:
        id_a, id_b = id_b, id_a
    from mfp.core.primitives import sha256
    return sha256(id_a + id_b).data
