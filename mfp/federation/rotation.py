"""Key rotation protocol for bilateral channels (P3.3).

Implements X25519 DH-based key rotation for long-lived bilateral channels
to mitigate cryptographic key fatigue.

Maps to: BUILD_JOURNAL.md P3.3 — Key Rotation Mechanism
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from mfp.core.primitives import hmac_sha256
from mfp.core.types import BilateralState, RuntimeId, StateValue


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RotationConfig:
    """Configuration for bilateral channel key rotation."""
    enable_auto_rotation: bool = True
    rotation_message_threshold: int = 1_000_000  # 1M messages
    rotation_time_threshold_seconds: float = 86400.0  # 24 hours
    manual_rotation_enabled: bool = True


# ---------------------------------------------------------------------------
# Rotation Triggers
# ---------------------------------------------------------------------------

class RotationReason(Enum):
    """Reason for initiating key rotation."""
    MESSAGE_COUNT = "message_count"
    TIME_ELAPSED = "time_elapsed"
    MANUAL = "manual"


@dataclass
class RotationTrigger:
    """Tracks conditions for triggering key rotation."""
    messages_since_rotation: int = 0
    last_rotation_time: float = 0.0

    def __post_init__(self) -> None:
        if self.last_rotation_time == 0.0:
            self.last_rotation_time = time.time()

    def should_rotate(self, config: RotationConfig) -> RotationReason | None:
        """Check if rotation should be triggered.

        Returns rotation reason if rotation is needed, None otherwise.
        """
        if not config.enable_auto_rotation:
            return None

        # Check message count threshold
        if self.messages_since_rotation >= config.rotation_message_threshold:
            return RotationReason.MESSAGE_COUNT

        # Check time threshold
        elapsed = time.time() - self.last_rotation_time
        if elapsed >= config.rotation_time_threshold_seconds:
            return RotationReason.TIME_ELAPSED

        return None

    def increment_message_count(self) -> None:
        """Increment message counter (called after each bilateral message)."""
        self.messages_since_rotation += 1

    def reset(self) -> None:
        """Reset counters after successful rotation."""
        self.messages_since_rotation = 0
        self.last_rotation_time = time.time()


# ---------------------------------------------------------------------------
# Rekey Protocol Messages
# ---------------------------------------------------------------------------

@dataclass
class RekeyRequest:
    """Request to perform key rotation.

    Sent by initiator with their new ephemeral public key.
    """
    sender_runtime: RuntimeId
    receiver_runtime: RuntimeId
    ephemeral_public_key: bytes  # X25519 public key (32 bytes)
    proposed_step: int  # Step at which to switch to new key

    def to_bytes(self) -> bytes:
        """Serialize to wire format."""
        return (
            self.sender_runtime.value.data +
            self.receiver_runtime.value.data +
            self.ephemeral_public_key +
            self.proposed_step.to_bytes(8, 'big')
        )

    @classmethod
    def from_bytes(cls, data: bytes, sender: RuntimeId, receiver: RuntimeId) -> RekeyRequest:
        """Deserialize from wire format."""
        if len(data) != 104:  # 32 + 32 + 32 + 8
            raise ValueError(f"RekeyRequest requires 104 bytes, got {len(data)}")

        ephemeral_public_key = data[64:96]
        proposed_step = int.from_bytes(data[96:104], 'big')

        return cls(
            sender_runtime=sender,
            receiver_runtime=receiver,
            ephemeral_public_key=ephemeral_public_key,
            proposed_step=proposed_step,
        )


@dataclass
class RekeyAccept:
    """Acceptance of key rotation request.

    Sent by responder with their new ephemeral public key.
    """
    sender_runtime: RuntimeId
    receiver_runtime: RuntimeId
    ephemeral_public_key: bytes  # X25519 public key (32 bytes)
    accepted_step: int  # Confirmed step for key switch

    def to_bytes(self) -> bytes:
        """Serialize to wire format."""
        return (
            self.sender_runtime.value.data +
            self.receiver_runtime.value.data +
            self.ephemeral_public_key +
            self.accepted_step.to_bytes(8, 'big')
        )

    @classmethod
    def from_bytes(cls, data: bytes, sender: RuntimeId, receiver: RuntimeId) -> RekeyAccept:
        """Deserialize from wire format."""
        if len(data) != 104:  # 32 + 32 + 32 + 8
            raise ValueError(f"RekeyAccept requires 104 bytes, got {len(data)}")

        ephemeral_public_key = data[64:96]
        accepted_step = int.from_bytes(data[96:104], 'big')

        return cls(
            sender_runtime=sender,
            receiver_runtime=receiver,
            ephemeral_public_key=ephemeral_public_key,
            accepted_step=accepted_step,
        )


# ---------------------------------------------------------------------------
# Key Derivation
# ---------------------------------------------------------------------------

def derive_rotated_bilateral_state(
    current_state: BilateralState,
    shared_secret: bytes,
    runtime_a: RuntimeId,
    runtime_b: RuntimeId,
) -> BilateralState:
    """Derive new bilateral state after key rotation.

    Combines current state with new DH shared secret to derive rotated keys.
    Preserves step counter continuity.

    Args:
        current_state: Current bilateral state before rotation
        shared_secret: X25519 DH shared secret (32 bytes)
        runtime_a: First runtime ID (for determinism)
        runtime_b: Second runtime ID (for determinism)

    Returns:
        New BilateralState with rotated keys
    """
    # Ensure deterministic ordering
    id_a, id_b = runtime_a.value.data, runtime_b.value.data
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    # Derive new ratchet state from: old_state + shared_secret + runtime_ids
    rotation_material = (
        current_state.ratchet_state.data +
        shared_secret +
        id_a +
        id_b
    )
    new_ratchet = hmac_sha256(b"mfp-rotation-ratchet", rotation_material)
    new_prng_seed = hmac_sha256(b"mfp-rotation-prng", rotation_material)

    return BilateralState(
        ratchet_state=new_ratchet,
        shared_prng_seed=new_prng_seed,
        step=current_state.step,  # Preserve step continuity
    )


# ---------------------------------------------------------------------------
# Rotation Coordinator
# ---------------------------------------------------------------------------

class RotationState(Enum):
    """State machine for key rotation protocol."""
    IDLE = "idle"
    INITIATING = "initiating"  # Sent REKEY_REQUEST, awaiting REKEY_ACCEPT
    RESPONDING = "responding"  # Received REKEY_REQUEST, sending REKEY_ACCEPT
    ROTATING = "rotating"      # Both sides agreed, switching keys at next step
    COMPLETE = "complete"


@dataclass
class RotationSession:
    """Tracks an in-progress key rotation session."""
    state: RotationState = RotationState.IDLE
    local_private_key: bytes | None = None
    local_public_key: bytes | None = None
    peer_public_key: bytes | None = None
    rotation_step: int | None = None  # Step at which to switch keys
    shared_secret: bytes | None = None
    new_bilateral_state: BilateralState | None = None
    reason: RotationReason | None = None

    def generate_keypair(self) -> tuple[bytes, bytes]:
        """Generate ephemeral X25519 keypair for rotation.

        Returns (private_key, public_key) tuple.
        """
        private_key = X25519PrivateKey.generate()
        public_key = private_key.public_key()

        self.local_private_key = private_key.private_bytes_raw()
        self.local_public_key = public_key.public_bytes_raw()

        return self.local_private_key, self.local_public_key

    def compute_shared_secret(self, peer_public_key: bytes) -> bytes:
        """Compute DH shared secret from peer's public key."""
        if self.local_private_key is None:
            raise ValueError("Local private key not generated")

        private = X25519PrivateKey.from_private_bytes(self.local_private_key)
        peer = X25519PublicKey.from_public_bytes(peer_public_key)

        self.shared_secret = private.exchange(peer)
        self.peer_public_key = peer_public_key

        return self.shared_secret

    def reset(self) -> None:
        """Reset session to IDLE state."""
        self.state = RotationState.IDLE
        self.local_private_key = None
        self.local_public_key = None
        self.peer_public_key = None
        self.rotation_step = None
        self.shared_secret = None
        self.new_bilateral_state = None
        self.reason = None
