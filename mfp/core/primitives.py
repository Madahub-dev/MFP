"""MFP Cryptographic Primitives — thin wrappers around the cryptography library.

Each function wraps a single cryptographic operation. No custom crypto logic.
No key management. Stateless.

Maps to: impl/I-02_primitives.md
"""

from __future__ import annotations

import hmac as stdlib_hmac
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import hmac as crypto_hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mfp.core.types import (
    BLOCK_SIZE,
    STATE_SIZE,
    Block,
    CSPRNGError,
    StateValue,
)


# ---------------------------------------------------------------------------
# HMAC-SHA-256
# ---------------------------------------------------------------------------

def hmac_sha256(key: bytes, message: bytes) -> StateValue:
    """Compute HMAC-SHA-256(key, message).

    Maps to: spec.md §4.1, §4.2, §4.3, §5.2, §6.6, §7.4.
    """
    h = crypto_hmac.HMAC(key, hashes.SHA256())
    h.update(message)
    return StateValue(h.finalize())


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> StateValue:
    """Compute SHA-256(data).

    Maps to: spec.md §4.3, federation.md §9.2.
    """
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return StateValue(digest.finalize())


# ---------------------------------------------------------------------------
# ChaCha20 PRNG
# ---------------------------------------------------------------------------

class ChaCha20PRNG:
    """Deterministic PRNG based on ChaCha20 keystream.

    Two instances with the same seed produce identical output.

    Maps to: spec.md §5.2, §5.4.
    """

    def __init__(self, seed: StateValue) -> None:
        # ChaCha20 requires a 16-byte nonce. Fixed at zeros — the seed
        # already incorporates all differentiation. See I-02 §5.3.
        nonce = b"\x00" * 16
        cipher = Cipher(algorithms.ChaCha20(seed.data, nonce), mode=None)
        self._encryptor = cipher.encryptor()

    def next_bytes(self, n: int) -> bytes:
        """Draw n bytes from the keystream."""
        return self._encryptor.update(b"\x00" * n)


# ---------------------------------------------------------------------------
# AES-256-GCM
# ---------------------------------------------------------------------------

def aes_256_gcm_encrypt(
    key: bytes, nonce: bytes, plaintext: bytes, aad: bytes,
) -> bytes:
    """Encrypt with AES-256-GCM.

    Returns ciphertext || 16-byte GCM authentication tag.

    Maps to: spec.md §6.5.
    """
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise ValueError(f"AES-256-GCM nonce must be 12 bytes, got {len(nonce)}")
    aesgcm = AESGCM(key)
    return aesgcm.encrypt(nonce, plaintext, aad)


def aes_256_gcm_decrypt(
    key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes,
) -> bytes | None:
    """Decrypt with AES-256-GCM.

    Returns plaintext on success, None if authentication tag fails.

    Maps to: spec.md §6.2.
    """
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM key must be 32 bytes, got {len(key)}")
    if len(nonce) != 12:
        raise ValueError(f"AES-256-GCM nonce must be 12 bytes, got {len(nonce)}")
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        return None


# ---------------------------------------------------------------------------
# OS CSPRNG
# ---------------------------------------------------------------------------

def random_bytes(n: int) -> bytes:
    """Draw n bytes from the operating system's CSPRNG.

    Maps to: spec.md §5.3 — runtime_entropy(b).
    """
    try:
        return os.urandom(n)
    except NotImplementedError:
        raise CSPRNGError("OS CSPRNG unavailable")


def random_block() -> Block:
    """Generate a random Block (16 bytes) from OS CSPRNG."""
    return Block(random_bytes(BLOCK_SIZE))


def random_state_value() -> StateValue:
    """Generate a random StateValue (32 bytes) from OS CSPRNG."""
    return StateValue(random_bytes(STATE_SIZE))


def random_id(size: int = 16) -> bytes:
    """Generate a random identifier of the given size."""
    return random_bytes(size)


# ---------------------------------------------------------------------------
# Constant-Time Comparison
# ---------------------------------------------------------------------------

def constant_time_equal(a: bytes, b: bytes) -> bool:
    """Compare two byte strings in constant time.

    Maps to: threat-model.md §4.10.
    """
    return stdlib_hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def encode_u64_be(value: int) -> bytes:
    """Encode an unsigned 64-bit integer in big-endian format.

    Maps to: spec.md §5.2.
    """
    return struct.pack(">Q", value)
