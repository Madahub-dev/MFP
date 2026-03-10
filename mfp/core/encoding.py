"""MFP Encoding Engine — payload encryption/decryption with context binding.

Independent of framing. The frame authenticates the envelope; encoding
protects the content. Neither substitutes for the other.

Maps to: impl/I-05_encoding.md
"""

from __future__ import annotations

from mfp.core.primitives import (
    aes_256_gcm_decrypt,
    aes_256_gcm_encrypt,
    encode_u64_be,
    hmac_sha256,
)
from mfp.core.types import (
    ALGORITHM_AES_256_GCM,
    ChannelId,
    EncodingContext,
    StateValue,
)


# ---------------------------------------------------------------------------
# Key Derivation
# ---------------------------------------------------------------------------

def derive_encoding_key(
    local_state: StateValue,
    algorithm_id: bytes,
) -> StateValue:
    """Derive the encoding key from ratchet state and algorithm.

    Computes: key = HMAC-SHA-256(key: Sl, message: "mfp-encoding-key" || algorithm_id)

    Re-derived every step because Sl advances with each exchange.

    Maps to: spec.md §6.6.
    """
    message = b"mfp-encoding-key" + algorithm_id
    return hmac_sha256(local_state.data, message)


# ---------------------------------------------------------------------------
# Nonce Derivation
# ---------------------------------------------------------------------------

def derive_nonce(
    key: StateValue,
    channel_id: ChannelId,
    step: int,
) -> bytes:
    """Derive a 12-byte nonce for AES-256-GCM.

    Computes: nonce = HMAC-SHA-256(key, channel_id || encode_u64_be(t))[:12]

    Maps to: spec.md §6.5.
    """
    message = channel_id.value + encode_u64_be(step)
    full_hash = hmac_sha256(key.data, message)
    return full_hash.data[:12]


# ---------------------------------------------------------------------------
# AAD Construction
# ---------------------------------------------------------------------------

def build_aad(channel_id: ChannelId, step: int) -> bytes:
    """Construct AAD for AES-256-GCM: channel_id || encode_u64_be(t).

    Maps to: spec.md §6.5.
    """
    return channel_id.value + encode_u64_be(step)


# ---------------------------------------------------------------------------
# Context Construction
# ---------------------------------------------------------------------------

def build_encoding_context(
    local_state: StateValue,
    channel_id: ChannelId,
    step: int,
    algorithm_id: bytes = ALGORITHM_AES_256_GCM,
) -> EncodingContext:
    """Build a complete encoding context from channel state.

    Derives the encoding key and assembles the context.

    Maps to: spec.md §6.3, §6.6.
    """
    key = derive_encoding_key(local_state, algorithm_id)
    return EncodingContext(
        algorithm_id=algorithm_id,
        key=key,
        channel_id=channel_id,
        step=step,
    )


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def encode(payload: bytes, ctx: EncodingContext) -> bytes:
    """Encode (encrypt) a payload with AES-256-GCM.

    Returns ciphertext || 16-byte GCM tag (16 bytes longer than payload).

    Maps to: spec.md §6.2, §6.5.
    """
    if ctx.algorithm_id != ALGORITHM_AES_256_GCM:
        raise ValueError(f"Unsupported encoding algorithm: {ctx.algorithm_id!r}")

    nonce = derive_nonce(ctx.key, ctx.channel_id, ctx.step)
    aad = build_aad(ctx.channel_id, ctx.step)

    return aes_256_gcm_encrypt(
        key=ctx.key.data,
        nonce=nonce,
        plaintext=payload,
        aad=aad,
    )


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def decode(encoded_payload: bytes, ctx: EncodingContext) -> bytes | None:
    """Decode (decrypt) an encoded payload.

    Returns plaintext on success, None if integrity verification fails.

    Maps to: spec.md §6.2, §6.5.
    """
    if ctx.algorithm_id != ALGORITHM_AES_256_GCM:
        raise ValueError(f"Unsupported encoding algorithm: {ctx.algorithm_id!r}")

    nonce = derive_nonce(ctx.key, ctx.channel_id, ctx.step)
    aad = build_aad(ctx.channel_id, ctx.step)

    return aes_256_gcm_decrypt(
        key=ctx.key.data,
        nonce=nonce,
        ciphertext=encoded_payload,
        aad=aad,
    )
